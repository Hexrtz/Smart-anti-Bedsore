// Node 2 live cough / choke detector: INMP441 -> log-mel -> int8 CNN (TFLite Micro) -> Serial.
//
// Serial 115200, one line per event (nothing is printed for speech / background windows):
//   [  12.5 s] COUGH #3  p=0.97
//   [  13.0 s] CHOKE  3 coughs in 5 s
//   [  20.0 s] alive  no cough for 5 s  (last window: background 0.91, 312 ms/window, skipped 0)
//
// FreeRTOS tasks (no polling in loop()):
//   audio_task   (core 0) reads I2S blocks, int32 >> 16 -> int16 (same as record.ino),
//                writes a 2 s ring buffer, notifies infer_task every 0.5 s of audio
//   infer_task   (core 1) waits for the notification, copies the last 1 s, front end + model,
//                sends the result to a queue
//   report_task  (core 1) turns window results into cough events and choke alerts
//
// Cough event: consecutive windows with p_cough >= COUGH_THRESHOLD count as ONE cough
// (windows overlap by 0.5 s, so one cough usually appears in two windows).
// Choke: >= CHOKE_COUGHS cough events within CHOKE_WINDOW_MS.
//
// Arduino IDE: "ESP32S3 Dev Module", Flash 16MB, PSRAM "OPI PSRAM", USB CDC On Boot as you use it.
// Library: tflm_esp32. Wiring as record.ino: SCK 12, WS 13, SD 14, L/R -> GND.

#include <Arduino.h>
#include <new>
#include <esp_timer.h>
#include "driver/i2s_std.h"
#include <tflm_esp32.h>

#include "cough_model_data.h"
#include "cough_features.h"
#include "cough_frontend.h"

// ---------------- settings ----------------
#define I2S_SCK 12
#define I2S_WS 13
#define I2S_SD 14
#define SHIFT 16                     // int32 I2S sample >> 16 -> int16, same as the recorder
#define BLOCK 512                    // samples per I2S read

#define HOP_SAMPLES (COUGH_SAMPLE_RATE / 2)   // run the model every 0.5 s
#define RING_SAMPLES (COUGH_SAMPLE_RATE * 2)  // keep 2 s of audio

#define COUGH_THRESHOLD 0.5f         // p_cough for a window to count as cough
#define CHOKE_COUGHS 3               // coughs ...
#define CHOKE_WINDOW_MS 5000         // ... within this time = choke
#define ALIVE_MS 5000                // print "alive" after this long without a cough
#define PRINT_EVERY_WINDOW 0         // 1 = print every window (for tuning the threshold)

constexpr size_t ARENA_SIZE = 96 * 1024;  // 79,740 bytes measured
constexpr int N_INPUT = COUGH_N_MELS * COUGH_FRAMES;

// ---------------- shared state ----------------
alignas(16) static uint8_t arena[ARENA_SIZE];
alignas(16) static uint8_t interpreter_mem[sizeof(tflite::MicroInterpreter) + 4096];
static tflite::MicroMutableOpResolver<4> resolver;
static tflite::MicroInterpreter *interpreter = nullptr;

static i2s_chan_handle_t rx_chan;
static int16_t *ring = nullptr;                  // RING_SAMPLES, PSRAM
static volatile uint64_t samples_written = 0;    // total samples since boot (audio clock)
static SemaphoreHandle_t ring_mutex;
static TaskHandle_t infer_handle;
static QueueHandle_t result_queue;

struct WindowResult {
  uint32_t end_ms;   // audio time at the end of the 1 s window
  float p[COUGH_N_CLASSES];
  uint8_t pred;
  uint32_t compute_ms;
  uint32_t skipped;  // hops dropped because the previous window was still running
};

// ---------------- I2S (same setup as record.ino) ----------------
static bool mic_init() {
  i2s_chan_config_t chan_cfg = I2S_CHANNEL_DEFAULT_CONFIG(I2S_NUM_0, I2S_ROLE_MASTER);
  chan_cfg.dma_desc_num = 8;
  chan_cfg.dma_frame_num = 256;
  if (i2s_new_channel(&chan_cfg, NULL, &rx_chan) != ESP_OK) return false;

  i2s_std_config_t std_cfg = {
    .clk_cfg = I2S_STD_CLK_DEFAULT_CONFIG(COUGH_SAMPLE_RATE),
    .slot_cfg = I2S_STD_PHILIPS_SLOT_DEFAULT_CONFIG(I2S_DATA_BIT_WIDTH_32BIT, I2S_SLOT_MODE_MONO),
    .gpio_cfg = {
      .mclk = I2S_GPIO_UNUSED,
      .bclk = (gpio_num_t)I2S_SCK,
      .ws = (gpio_num_t)I2S_WS,
      .dout = I2S_GPIO_UNUSED,
      .din = (gpio_num_t)I2S_SD,
      .invert_flags = { .mclk_inv = false, .bclk_inv = false, .ws_inv = false },
    },
  };
  std_cfg.slot_cfg.slot_mask = I2S_STD_SLOT_LEFT;
  if (i2s_channel_init_std_mode(rx_chan, &std_cfg) != ESP_OK) return false;
  return i2s_channel_enable(rx_chan) == ESP_OK;
}

// ---------------- tasks ----------------
static void audio_task(void *) {
  static int32_t raw[BLOCK];
  static int16_t pcm[BLOCK];
  uint64_t next_hop = COUGH_CLIP_SAMPLES;  // first window once 1 s of audio exists
  for (;;) {
    size_t bytes = 0;
    if (i2s_channel_read(rx_chan, raw, sizeof(raw), &bytes, portMAX_DELAY) != ESP_OK) continue;
    int n = bytes / sizeof(int32_t);
    for (int i = 0; i < n; i++) {
      int32_t v = raw[i] >> SHIFT;
      pcm[i] = (int16_t)(v > 32767 ? 32767 : (v < -32768 ? -32768 : v));
    }
    xSemaphoreTake(ring_mutex, portMAX_DELAY);
    uint64_t w = samples_written;
    for (int i = 0; i < n; i++) ring[(w + i) % RING_SAMPLES] = pcm[i];
    samples_written = w + n;
    xSemaphoreGive(ring_mutex);
    if (samples_written >= next_hop) {
      next_hop += HOP_SAMPLES;
      xTaskNotifyGive(infer_handle);  // deferred work: inference runs in its own task
    }
  }
}

static void infer_task(void *) {
  static int16_t window[COUGH_CLIP_SAMPLES];
  TfLiteTensor *in = interpreter->input(0);
  TfLiteTensor *out = interpreter->output(0);
  uint32_t skipped = 0;
  for (;;) {
    uint32_t pending = ulTaskNotifyTake(pdTRUE, portMAX_DELAY);
    if (pending > 1) skipped += pending - 1;

    xSemaphoreTake(ring_mutex, portMAX_DELAY);
    uint64_t end = samples_written;
    for (int i = 0; i < COUGH_CLIP_SAMPLES; i++)
      window[i] = ring[(end - COUGH_CLIP_SAMPLES + i) % RING_SAMPLES];
    xSemaphoreGive(ring_mutex);

    int64_t t0 = esp_timer_get_time();
    frontend_compute(window, in->data.int8);
    if (interpreter->Invoke() != kTfLiteOk) {
      Serial.println("Invoke() failed");
      continue;
    }
    WindowResult r;
    r.end_ms = (uint32_t)(end * 1000 / COUGH_SAMPLE_RATE);
    r.compute_ms = (uint32_t)((esp_timer_get_time() - t0) / 1000);
    r.skipped = skipped;

    float logit[COUGH_N_CLASSES], mx = -1e9f, sum = 0.0f;
    r.pred = 0;
    for (int i = 0; i < COUGH_N_CLASSES; i++) {
      logit[i] = (out->data.int8[i] - COUGH_OUTPUT_ZERO_POINT) * COUGH_OUTPUT_SCALE;
      if (logit[i] > mx) { mx = logit[i]; r.pred = i; }
    }
    for (int i = 0; i < COUGH_N_CLASSES; i++) sum += (r.p[i] = expf(logit[i] - mx));
    for (int i = 0; i < COUGH_N_CLASSES; i++) r.p[i] /= sum;
    xQueueSend(result_queue, &r, 0);
  }
}

static void report_task(void *) {
  const int COUGH = 0;  // cough_classes[0] == "cough"
  uint32_t event_ms[CHOKE_COUGHS];  // times of the last CHOKE_COUGHS cough events
  int n_recent = 0;
  uint32_t n_events = 0, last_cough_ms = 0, last_alive_ms = 0;
  bool in_event = false, choke_active = false;
  WindowResult r;
  for (;;) {
    xQueueReceive(result_queue, &r, portMAX_DELAY);
    float t = (r.end_ms - 500) / 1000.0f;  // centre of the window
    bool cough = r.p[COUGH] >= COUGH_THRESHOLD;

#if PRINT_EVERY_WINDOW
    Serial.printf("[%7.1f s] %-10s cough %.2f speech %.2f background %.2f (%u ms)\n", t,
                  cough_classes[r.pred], r.p[0], r.p[1], r.p[2], r.compute_ms);
#endif

    if (cough && !in_event) {  // a new cough event starts
      n_events++;
      if (n_recent == CHOKE_COUGHS) {
        memmove(event_ms, event_ms + 1, (CHOKE_COUGHS - 1) * sizeof(uint32_t));
        n_recent--;
      }
      event_ms[n_recent++] = r.end_ms;
      Serial.printf("[%7.1f s] COUGH #%u  p=%.2f\n", t, n_events, r.p[COUGH]);

      bool burst = n_recent == CHOKE_COUGHS && r.end_ms - event_ms[0] <= CHOKE_WINDOW_MS;
      if (burst && !choke_active) {
        Serial.printf("[%7.1f s] CHOKE  %d coughs in %.0f s\n", t, CHOKE_COUGHS, CHOKE_WINDOW_MS / 1000.0f);
        choke_active = true;
      }
    }
    in_event = cough;
    if (cough) last_cough_ms = r.end_ms;

    // choke ends once the burst is over (no CHOKE_COUGHS coughs within the window any more)
    if (choke_active && !(n_recent == CHOKE_COUGHS && r.end_ms - event_ms[0] <= CHOKE_WINDOW_MS))
      choke_active = false;

    if (r.end_ms - last_cough_ms >= ALIVE_MS && r.end_ms - last_alive_ms >= ALIVE_MS) {
      Serial.printf("[%7.1f s] alive  no cough for %.0f s  (last window: %s %.2f, %u ms/window, skipped %u)\n",
                    t, ALIVE_MS / 1000.0f, cough_classes[r.pred], r.p[r.pred], r.compute_ms, r.skipped);
      last_alive_ms = r.end_ms;
    }
  }
}

// ---------------- Arduino ----------------
static void halt(const char *msg) {
  for (;;) {
    Serial.println(msg);
    delay(2000);
  }
}

void setup() {
  Serial.begin(115200);
  delay(1500);
  Serial.println("\n=== Node 2 cough detector ===");

  if (!frontend_init()) halt("PSRAM missing: set Tools > PSRAM to \"OPI PSRAM\"");
  ring = (int16_t *)ps_malloc(RING_SAMPLES * sizeof(int16_t));
  if (!ring) halt("ring buffer allocation failed");

  const tflite::Model *model = tflite::GetModel(g_cough_model);
  if (model->version() != TFLITE_SCHEMA_VERSION) halt("model schema version mismatch");
  resolver.AddConv2D();
  resolver.AddMaxPool2D();
  resolver.AddMean();
  resolver.AddFullyConnected();
  interpreter = new (interpreter_mem) tflite::MicroInterpreter(model, resolver, arena, ARENA_SIZE);
  if (interpreter->AllocateTensors() != kTfLiteOk) halt("AllocateTensors() failed");
  Serial.printf("model ready, arena used %u bytes\n", (unsigned)interpreter->arena_used_bytes());

  if (!mic_init()) halt("I2S microphone init failed: check SCK 12 / WS 13 / SD 14");

  ring_mutex = xSemaphoreCreateMutex();
  result_queue = xQueueCreate(8, sizeof(WindowResult));
  xTaskCreatePinnedToCore(infer_task, "infer", 16384, nullptr, 3, &infer_handle, 1);
  xTaskCreatePinnedToCore(report_task, "report", 4096, nullptr, 2, nullptr, 1);
  xTaskCreatePinnedToCore(audio_task, "audio", 4096, nullptr, 5, nullptr, 0);
  Serial.printf("listening: window 1 s, hop 0.5 s, cough >= %.2f, choke = %d coughs in %d s\n",
                COUGH_THRESHOLD, CHOKE_COUGHS, CHOKE_WINDOW_MS / 1000);
}

void loop() {
  vTaskDelete(nullptr);  // all work runs in the tasks above
}
