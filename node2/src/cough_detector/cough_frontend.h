// Log-mel front end for the cough model, steps 1-8 of cough_features.h.
// Same code as cough_tflm_test, which matched PyTorch on every value (0/3880 differ).
#ifndef COUGH_FRONTEND_H
#define COUGH_FRONTEND_H

#include <Arduino.h>
#include <math.h>
#include "cough_features.h"

static float *fe_wave = nullptr;   // COUGH_CLIP_SAMPLES floats, PSRAM
static float *fe_taper = nullptr;  // COUGH_CLIP_SAMPLES floats, PSRAM
static float fe_re[COUGH_N_FFT], fe_im[COUGH_N_FFT];
static float fe_cos[COUGH_N_FFT / 2], fe_sin[COUGH_N_FFT / 2];
static uint16_t fe_bitrev[COUGH_N_FFT];
static float fe_power[COUGH_N_BINS];

// allocate buffers and tables; false if PSRAM is missing ("OPI PSRAM" in Tools)
static bool frontend_init() {
  fe_wave = (float *)ps_malloc(COUGH_CLIP_SAMPLES * sizeof(float));
  fe_taper = (float *)ps_malloc(COUGH_CLIP_SAMPLES * sizeof(float));
  if (!fe_wave || !fe_taper) return false;
  for (int n = 0; n < COUGH_CLIP_SAMPLES; n++)
    fe_taper[n] = COUGH_TAPER_HAMMING ? 0.54f - 0.46f * cosf(2.0f * (float)M_PI * n / (COUGH_CLIP_SAMPLES - 1)) : 1.0f;

  int bits = 0;
  while ((1 << bits) < COUGH_N_FFT) bits++;
  for (int i = 0; i < COUGH_N_FFT; i++) {
    unsigned r = 0;
    for (int b = 0; b < bits; b++)
      if (i & (1 << b)) r |= 1u << (bits - 1 - b);
    fe_bitrev[i] = r;
  }
  for (int k = 0; k < COUGH_N_FFT / 2; k++) {
    double a = -2.0 * M_PI * k / COUGH_N_FFT;
    fe_cos[k] = (float)cos(a);
    fe_sin[k] = (float)sin(a);
  }
  return true;
}

// in-place radix-2 complex FFT of size COUGH_N_FFT
static void frontend_fft(float *re, float *im) {
  const int n = COUGH_N_FFT;
  for (int i = 0; i < n; i++) {
    int j = fe_bitrev[i];
    if (j > i) {
      float t = re[i]; re[i] = re[j]; re[j] = t;
      t = im[i]; im[i] = im[j]; im[j] = t;
    }
  }
  for (int len = 2; len <= n; len <<= 1) {
    int half = len / 2, step = n / len;
    for (int i = 0; i < n; i += len) {
      for (int k = 0; k < half; k++) {
        float wr = fe_cos[k * step], wi = fe_sin[k * step];
        int a = i + k, b = a + half;
        float xr = re[b] * wr - im[b] * wi;
        float xi = re[b] * wi + im[b] * wr;
        re[b] = re[a] - xr; im[b] = im[a] - xi;
        re[a] += xr;        im[a] += xi;
      }
    }
  }
}

// 1 s of int16 audio -> int8 model input, index m * COUGH_FRAMES + t
static void frontend_compute(const int16_t *audio, int8_t *dst) {
  for (int n = 0; n < COUGH_CLIP_SAMPLES; n++)
    fe_wave[n] = (audio[n] / 32768.0f) * fe_taper[n];

  for (int t = 0; t < COUGH_FRAMES; t++) {
    const float *frame = fe_wave + t * COUGH_HOP_LENGTH;
    for (int k = 0; k < COUGH_N_FFT; k++) {
      fe_re[k] = frame[k] * cough_stft_window[k];
      fe_im[k] = 0.0f;
    }
    frontend_fft(fe_re, fe_im);
    for (int b = 0; b < COUGH_N_BINS; b++)
      fe_power[b] = fe_re[b] * fe_re[b] + fe_im[b] * fe_im[b];

    for (int m = 0; m < COUGH_N_MELS; m++) {
      float mel = 0.0f;
      for (int b = 0; b < COUGH_N_BINS; b++)
        mel += fe_power[b] * cough_mel_fb[b * COUGH_N_MELS + m];
      float v = (logf(mel + COUGH_LOG_EPS) - cough_mean[m]) / cough_std[m];
      long q = lroundf(v / COUGH_INPUT_SCALE) + COUGH_INPUT_ZERO_POINT;
      if (q < -128) q = -128;
      if (q > 127) q = 127;
      dst[m * COUGH_FRAMES + t] = (int8_t)q;
    }
  }
}

#endif  // COUGH_FRONTEND_H
