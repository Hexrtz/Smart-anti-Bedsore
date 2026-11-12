// ESP32-S3 + INMP441 recorder controlled from record_gui.py
// Tools: ESP32S3 Dev Module, USB CDC On Boot = Disabled, plug into the COM port
// Wiring (matches CLAUDE.md Node 2 pins): VDD->3V3, GND->GND, L/R->GND, SCK->GPIO12, WS->GPIO13, SD->GPIO14
//
// LED:  blue = idle / ready   yellow = recording   red (1 s) = just stopped
//       blinking red = microphone init failed
//
// Commands from PC (1 byte):  R = start   S = stop   K = keep-alive   P = ping
// Frames to PC (little-endian):
//   magic A5 5A C3 3C | type uint8 | seq uint32 | count uint16 | int16 x count
//   type 'A' = audio (seq increments per frame)
//   type 'H' = status (seq holds state: 0 idle, 1 recording, 2 stopped, count = 0)
#include <Arduino.h>
#include "driver/i2s_std.h"

#define I2S_SCK      12
#define I2S_WS       13
#define I2S_SD       14
#define SAMPLE_RATE  16000
#define BLOCK        512
#define SHIFT        16
#define BAUD         921600
#define KEEPALIVE_MS 3000   // stop by itself if the PC goes silent while recording

enum State : uint8_t { IDLE = 0, RECORDING = 1, STOPPED = 2 };

i2s_chan_handle_t rx_chan;
int32_t raw[BLOCK];
int16_t pcm[BLOCK];
uint32_t seq = 0;
State state = IDLE;
uint32_t lastKeepalive = 0, stoppedAt = 0, lastHeartbeat = 0;
const uint8_t MAGIC[4] = {0xA5, 0x5A, 0xC3, 0x3C};

void setLed(uint8_t r, uint8_t g, uint8_t b) {
#ifdef RGB_BUILTIN
  rgbLedWrite(RGB_BUILTIN, r, g, b);
#endif
}

void showState() {
  if (state == IDLE)      setLed(0, 0, 25);    // blue
  if (state == RECORDING) setLed(25, 15, 0);   // yellow
  if (state == STOPPED)   setLed(25, 0, 0);    // red
}

void sendFrame(uint8_t type, uint32_t s, const int16_t *data, uint16_t n) {
  Serial.write(MAGIC, 4);
  Serial.write(&type, 1);
  Serial.write((uint8_t *)&s, 4);
  Serial.write((uint8_t *)&n, 2);
  if (n) Serial.write((const uint8_t *)data, n * sizeof(int16_t));
}

void sendStatus() {
  sendFrame('H', state, nullptr, 0);
  lastHeartbeat = millis();
}

void setState(State s) {
  state = s;
  showState();
  sendStatus();
}

bool micInit() {
  i2s_chan_config_t chan_cfg = I2S_CHANNEL_DEFAULT_CONFIG(I2S_NUM_0, I2S_ROLE_MASTER);
  chan_cfg.dma_desc_num = 8;
  chan_cfg.dma_frame_num = 256;
  if (i2s_new_channel(&chan_cfg, NULL, &rx_chan) != ESP_OK) return false;

  i2s_std_config_t std_cfg = {
    .clk_cfg  = I2S_STD_CLK_DEFAULT_CONFIG(SAMPLE_RATE),
    .slot_cfg = I2S_STD_PHILIPS_SLOT_DEFAULT_CONFIG(I2S_DATA_BIT_WIDTH_32BIT, I2S_SLOT_MODE_MONO),
    .gpio_cfg = {
      .mclk = I2S_GPIO_UNUSED,
      .bclk = (gpio_num_t)I2S_SCK,
      .ws   = (gpio_num_t)I2S_WS,
      .dout = I2S_GPIO_UNUSED,
      .din  = (gpio_num_t)I2S_SD,
      .invert_flags = { .mclk_inv = false, .bclk_inv = false, .ws_inv = false },
    },
  };
  std_cfg.slot_cfg.slot_mask = I2S_STD_SLOT_LEFT;

  if (i2s_channel_init_std_mode(rx_chan, &std_cfg) != ESP_OK) return false;
  if (i2s_channel_enable(rx_chan) != ESP_OK) return false;
  return true;
}

void handleCommands() {
  while (Serial.available()) {
    char c = Serial.read();
    if (c == 'R') {
      seq = 0;
      lastKeepalive = millis();
      setState(RECORDING);
    } else if (c == 'S') {
      if (state == RECORDING) { stoppedAt = millis(); setState(STOPPED); }
    } else if (c == 'K') {
      lastKeepalive = millis();
    } else if (c == 'P') {
      sendStatus();
    }
  }
}

void setup() {
  Serial.setTxBufferSize(16384);
  Serial.begin(BAUD);

  if (!micInit()) {
    while (true) {
      setLed(25, 0, 0); delay(300);
      setLed(0, 0, 0);  delay(300);
    }
  }
  setState(IDLE);
}

void loop() {
  handleCommands();

  // always read the mic so the buffer holds fresh audio when recording starts
  size_t bytesRead = 0;
  if (i2s_channel_read(rx_chan, raw, sizeof(raw), &bytesRead, 100) != ESP_OK) return;
  uint16_t n = bytesRead / sizeof(int32_t);

  if (state == RECORDING) {
    for (int i = 0; i < n; i++) {
      int32_t v = raw[i] >> SHIFT;
      if (v > 32767) v = 32767;
      if (v < -32768) v = -32768;
      pcm[i] = (int16_t)v;
    }
    if (n) sendFrame('A', seq++, pcm, n);

    if (millis() - lastKeepalive > KEEPALIVE_MS) {   // PC disappeared
      stoppedAt = millis();
      setState(STOPPED);
    }
  } else {
    if (state == STOPPED && millis() - stoppedAt > 1000) setState(IDLE);
    if (millis() - lastHeartbeat > 500) sendStatus();
  }
}
