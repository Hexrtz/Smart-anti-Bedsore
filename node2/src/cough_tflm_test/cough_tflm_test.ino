// Node 2 cough model: on-device check for TFLite Micro on the ESP32-S3 (N16R8).
//
// What it does (Serial 115200, once at boot):
//   1. load the int8 model (cough_model_data.h) and allocate tensors
//   2. for each golden test vector (cough_test_vector.h, one per class):
//      A. feed the expected int8 input  -> model output must equal the PC output
//      B. compute the log-mel front end from the raw audio on the board
//         -> int8 input must equal the PC input (at most +-1 per value)
//         -> run the model on it and print the class + probabilities
//   3. print timings (front end, inference) and memory use
//
// Arduino IDE: board "ESP32S3 Dev Module", Flash Size 16MB, PSRAM "OPI PSRAM".
// Libraries: tflm_esp32 (EloquentTinyML is not needed for this sketch).
// The three headers come from node2/experiment `make export` (runs/<name>/<time>/tflite/).

#include <Arduino.h>
#include <math.h>
#include <new>
#include <esp_timer.h>
#include <esp_heap_caps.h>
#include <tflm_esp32.h>

#include "cough_model_data.h"
#include "cough_features.h"
#include "cough_test_vector.h"

constexpr size_t ARENA_SIZE = 100 * 1024;  // shrink after reading "arena used" below
alignas(16) static uint8_t arena[ARENA_SIZE];

static tflite::MicroMutableOpResolver<4> resolver;
static tflite::MicroInterpreter *interpreter = nullptr;
// The interpreter object lives in a static buffer with spare room instead of on
// the heap: the precompiled tflm_esp32 library may use a larger object than its
// headers declare, and `new` on the heap then corrupts the heap.
alignas(16) static uint8_t interpreter_mem[sizeof(tflite::MicroInterpreter) + 4096];

// prints whether the heap is still intact after a step (finds what corrupts it)
static void heap_check(const char *step) {
  bool ok = heap_caps_check_integrity_all(true);
  Serial.printf("[heap %s] %s\n", ok ? "ok" : "CORRUPT", step);
}
static TfLiteTensor *model_in = nullptr;
static TfLiteTensor *model_out = nullptr;

constexpr int N_INPUT = COUGH_N_MELS * COUGH_FRAMES;

// ---------------- log-mel front end (must match cough_features.h) ----------------

static float *wave = nullptr;   // COUGH_CLIP_SAMPLES floats, in PSRAM
static float *taper = nullptr;  // COUGH_CLIP_SAMPLES floats, in PSRAM
static float fft_re[COUGH_N_FFT], fft_im[COUGH_N_FFT];
static float tw_cos[COUGH_N_FFT / 2], tw_sin[COUGH_N_FFT / 2];
static uint16_t bitrev[COUGH_N_FFT];
static float power_spec[COUGH_N_BINS];

static void fft_init() {
  int bits = 0;
  while ((1 << bits) < COUGH_N_FFT) bits++;
  for (int i = 0; i < COUGH_N_FFT; i++) {
    unsigned r = 0;
    for (int b = 0; b < bits; b++)
      if (i & (1 << b)) r |= 1u << (bits - 1 - b);
    bitrev[i] = r;
  }
  for (int k = 0; k < COUGH_N_FFT / 2; k++) {
    double a = -2.0 * M_PI * k / COUGH_N_FFT;  // forward FFT: e^(-2*pi*i*k/N)
    tw_cos[k] = (float)cos(a);
    tw_sin[k] = (float)sin(a);
  }
}

// in-place iterative radix-2 complex FFT, size COUGH_N_FFT
static void fft(float *re, float *im) {
  const int n = COUGH_N_FFT;
  for (int i = 0; i < n; i++) {
    int j = bitrev[i];
    if (j > i) {
      float t = re[i]; re[i] = re[j]; re[j] = t;
      t = im[i]; im[i] = im[j]; im[j] = t;
    }
  }
  for (int len = 2; len <= n; len <<= 1) {
    int half = len / 2, step = n / len;
    for (int i = 0; i < n; i += len) {
      for (int k = 0; k < half; k++) {
        float wr = tw_cos[k * step], wi = tw_sin[k * step];
        int a = i + k, b = a + half;
        float xr = re[b] * wr - im[b] * wi;
        float xi = re[b] * wi + im[b] * wr;
        re[b] = re[a] - xr; im[b] = im[a] - xi;
        re[a] += xr;        im[a] += xi;
      }
    }
  }
}

// 1 s of int16 audio -> int8 model input [mel][frame], steps 1-8 of cough_features.h
static void compute_input(const int16_t *audio, int8_t *dst) {
  for (int n = 0; n < COUGH_CLIP_SAMPLES; n++)
    wave[n] = (audio[n] / 32768.0f) * taper[n];                       // 1-2

  for (int t = 0; t < COUGH_FRAMES; t++) {
    const float *frame = wave + t * COUGH_HOP_LENGTH;                 // 3
    for (int k = 0; k < COUGH_N_FFT; k++) {
      fft_re[k] = frame[k] * cough_stft_window[k];
      fft_im[k] = 0.0f;
    }
    fft(fft_re, fft_im);
    for (int b = 0; b < COUGH_N_BINS; b++)                            // 4
      power_spec[b] = fft_re[b] * fft_re[b] + fft_im[b] * fft_im[b];

    for (int m = 0; m < COUGH_N_MELS; m++) {
      float mel = 0.0f;
      for (int b = 0; b < COUGH_N_BINS; b++)                          // 5
        mel += power_spec[b] * cough_mel_fb[b * COUGH_N_MELS + m];
      float v = (logf(mel + COUGH_LOG_EPS) - cough_mean[m]) / cough_std[m];  // 6-7
      long q = lroundf(v / COUGH_INPUT_SCALE) + COUGH_INPUT_ZERO_POINT;     // 8
      if (q < -128) q = -128;
      if (q > 127) q = 127;
      dst[m * COUGH_FRAMES + t] = (int8_t)q;
    }
  }
}

// ---------------- helpers ----------------

static int argmax3(const int8_t *v) {
  int best = 0;
  for (int i = 1; i < COUGH_N_CLASSES; i++)
    if (v[i] > v[best]) best = i;
  return best;
}

static void softmax_from_int8(const int8_t *q, float *p) {
  float logit[COUGH_N_CLASSES], mx = -1e9f, sum = 0.0f;
  for (int i = 0; i < COUGH_N_CLASSES; i++) {
    logit[i] = (q[i] - COUGH_OUTPUT_ZERO_POINT) * COUGH_OUTPUT_SCALE;
    if (logit[i] > mx) mx = logit[i];
  }
  for (int i = 0; i < COUGH_N_CLASSES; i++) sum += (p[i] = expf(logit[i] - mx));
  for (int i = 0; i < COUGH_N_CLASSES; i++) p[i] /= sum;
}

static bool invoke(int64_t *us) {
  int64_t t0 = esp_timer_get_time();
  bool ok = interpreter->Invoke() == kTfLiteOk;
  *us = esp_timer_get_time() - t0;
  return ok;
}

// ---------------- Arduino ----------------

void setup() {
  Serial.begin(115200);
  delay(2000);
  Serial.println("\n=== cough model on-device check ===");
  Serial.printf("flash %u bytes, PSRAM %u bytes, free heap %u bytes\n",
                ESP.getFlashChipSize(), ESP.getPsramSize(), ESP.getFreeHeap());

  wave = (float *)ps_malloc(COUGH_CLIP_SAMPLES * sizeof(float));
  taper = (float *)ps_malloc(COUGH_CLIP_SAMPLES * sizeof(float));
  if (!wave || !taper) {
    Serial.println("ps_malloc failed: is PSRAM set to \"OPI PSRAM\" in Tools?");
    return;
  }
  for (int n = 0; n < COUGH_CLIP_SAMPLES; n++)
    taper[n] = COUGH_TAPER_HAMMING ? 0.54f - 0.46f * cosf(2.0f * (float)M_PI * n / (COUGH_CLIP_SAMPLES - 1)) : 1.0f;
  fft_init();
  heap_check("after PSRAM buffers + FFT tables");

  const tflite::Model *model = tflite::GetModel(g_cough_model);
  if (model->version() != TFLITE_SCHEMA_VERSION) {
    Serial.printf("model schema %lu, library expects %d\n", (unsigned long)model->version(), TFLITE_SCHEMA_VERSION);
    return;
  }
  resolver.AddConv2D();
  resolver.AddMaxPool2D();
  resolver.AddMean();
  resolver.AddFullyConnected();

  Serial.printf("sizeof(MicroInterpreter) = %u\n", (unsigned)sizeof(tflite::MicroInterpreter));
  interpreter = new (interpreter_mem) tflite::MicroInterpreter(model, resolver, arena, ARENA_SIZE);
  heap_check("after MicroInterpreter");
  if (interpreter->AllocateTensors() != kTfLiteOk) {
    Serial.println("AllocateTensors() failed: raise ARENA_SIZE or check the ops");
    return;
  }
  heap_check("after AllocateTensors");
  model_in = interpreter->input(0);
  model_out = interpreter->output(0);
  Serial.printf("model %u bytes, arena used %u of %u bytes\n",
                g_cough_model_len, (unsigned)interpreter->arena_used_bytes(), (unsigned)ARENA_SIZE);
  Serial.printf("input %d bytes type %d scale %.7f zp %d | header scale %.7f zp %d\n",
                (int)model_in->bytes, (int)model_in->type, model_in->params.scale,
                (int)model_in->params.zero_point, COUGH_INPUT_SCALE, COUGH_INPUT_ZERO_POINT);
  if ((int)model_in->bytes != N_INPUT || model_in->type != kTfLiteInt8) {
    Serial.println("input tensor does not match cough_features.h");
    return;
  }

  static int8_t q[N_INPUT];
  int passed = 0;
  for (int v = 0; v < cough_n_test_vectors; v++) {
    const CoughTestVector &tv = cough_test_vectors[v];
    Serial.printf("\n--- test vector %d: %s ---\n", v, cough_classes[tv.label]);

    // A. model only: expected input -> output must match the PC exactly
    memcpy(model_in->data.int8, tv.input, N_INPUT);
    int64_t t_inf;
    if (!invoke(&t_inf)) { Serial.println("Invoke() failed"); return; }
    if (v == 0) heap_check("after first Invoke");
    int max_out = 0;
    for (int i = 0; i < COUGH_N_CLASSES; i++)
      max_out = max(max_out, abs(model_out->data.int8[i] - tv.output[i]));
    Serial.printf("A model:     output [%d %d %d] expected [%d %d %d]  max diff %d  (%lld us)\n",
                  model_out->data.int8[0], model_out->data.int8[1], model_out->data.int8[2],
                  tv.output[0], tv.output[1], tv.output[2], max_out, t_inf);

    // B. front end on the board: raw audio -> int8 input must match the PC
    int64_t t0 = esp_timer_get_time();
    compute_input(tv.audio, q);
    int64_t t_fe = esp_timer_get_time() - t0;
    int n_diff = 0, max_in = 0;
    for (int i = 0; i < N_INPUT; i++) {
      int d = abs(q[i] - tv.input[i]);
      if (d) n_diff++;
      max_in = max(max_in, d);
    }
    Serial.printf("B front end: %d of %d values differ, max diff %d  (%lld us)\n", n_diff, N_INPUT, max_in, t_fe);

    memcpy(model_in->data.int8, q, N_INPUT);
    if (!invoke(&t_inf)) { Serial.println("Invoke() failed"); return; }
    float p[COUGH_N_CLASSES];
    softmax_from_int8(model_out->data.int8, p);
    int pred = argmax3(model_out->data.int8);
    Serial.printf("  predicted %s  (cough %.3f, speech %.3f, background %.3f)  (%lld us)\n",
                  cough_classes[pred], p[0], p[1], p[2], t_inf);

    bool ok = max_out == 0 && max_in <= 1 && pred == tv.label;
    passed += ok;
    Serial.println(ok ? "  PASS" : "  FAIL");
  }
  Serial.printf("\n%d of %d test vectors passed. free heap %u, free PSRAM %u\n",
                passed, cough_n_test_vectors, ESP.getFreeHeap(), ESP.getFreePsram());
}

void loop() {
  delay(1000);
}
