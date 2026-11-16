/* Node1: วัดแรงกด 6 จุด (FSR402) + สายดึงฉุกเฉิน แล้วส่งให้ Gateway ทาง UDP
 *
 * ทำอะไรบ้าง
 *   - อ่าน FSR 6 จุดทุก 1 วินาที (ADC1, GPIO 4-9 เรียงตาม fsr_order)
 *   - นับเวลาแรงกดสะสม dwell_s ของแต่ละจุด: กดเกิน fsr_on_threshold นับเพิ่ม
 *     ปล่อยต่อเนื่องครบ dwell_reset_s วินาทีจึงรีเซ็ตเป็น 0
 *   - สายดึงฉุกเฉิน GPIO10 ใช้ Interrupt แบบ deferred processing (เกณฑ์ E2):
 *     ISR แค่ส่ง task notification ให้ cordTask ที่รออยู่ (block ไม่กิน CPU ไม่มี busy polling)
 *     cordTask ตรวจ debounce แล้วใส่ emergency หน้าคิว ส่งซ้ำจนได้ ACK
 *   - ทุกข้อความรอ ACK 300 ms ส่งซ้ำสูงสุด 5 ครั้ง (เกณฑ์ E4) มีคิวในเครื่องกันข้อมูลหายตอน Wi-Fi หลุด
 *   - heartbeat (hb) ทุก 10 วินาที บอก RSSI และสถิติการส่ง
 *
 * รูปแบบข้อความตาม ไฟล์ใน shared/schema/  ค่าคงที่ทั้งหมดมาจาก shared/config.h
 * วงจร FSR: 3.3V -- FSR -- (ขา ADC) -- R 10k -- GND   ยิ่งกดแรง ค่า raw ยิ่งสูง
 * สายดึง: ขา GPIO10 -- สวิตช์ -- GND  (INPUT_PULLUP, ดึง = LOW)
 */
#include <Arduino.h>
#include <ESPmDNS.h>
#include <WiFi.h>
#include <WiFiUdp.h>

#include <deque>

#include "config.h"
#include "secrets.h"

#define NODE_ID "node1"
#define FW_VERSION "1.0.0"

static const int FSR_PINS[CFG_PINS_NODE1_FSR_COUNT] = CFG_PINS_NODE1_FSR;
static const int N_FSR = CFG_PINS_NODE1_FSR_COUNT;

// ---------------- คิวข้อความที่รอ ACK ----------------
struct Msg {
  uint32_t seq;
  String body;
  bool unlimited;      // emergency: ส่งซ้ำไม่จำกัดจนกว่าจะได้ ACK
  uint8_t attempts;    // ส่งไปแล้วกี่ครั้ง
  uint32_t last_ms;    // ส่งครั้งล่าสุดเมื่อไร
};

static std::deque<Msg> queue_;
static uint32_t seq_ = 0;
static uint32_t stat_sent = 0, stat_acked = 0, stat_dropped = 0;

static WiFiUDP udp;
static IPAddress gateway;
static bool gateway_ok = false;
static bool udp_ready = false;   // เปิด socket แล้วหรือยัง (udp.begin หลังต่อ Wi-Fi ได้)

// คิวถูกใช้จาก loop() และ cordTask พร้อมกัน ต้องล็อกทุกครั้งที่แตะคิว
static SemaphoreHandle_t queue_lock;
struct Lock {
  Lock() { xSemaphoreTake(queue_lock, portMAX_DELAY); }
  ~Lock() { xSemaphoreGive(queue_lock); }
};

// ---------------- สายดึงฉุกเฉิน ----------------
static TaskHandle_t cord_task = nullptr;

void IRAM_ATTR onPullCord() {                      // ใน ISR ทำแค่ปลุก task แล้วออกทันที
  BaseType_t woken = pdFALSE;
  vTaskNotifyGiveFromISR(cord_task, &woken);
  portYIELD_FROM_ISR(woken);
}

// ---------------- แรงกดสะสม ----------------
static int raw_[N_FSR];
static float dwell_s[N_FSR];
static uint32_t below_ms[N_FSR];

// ===================================================================
// ส่วนส่งข้อความ
// ===================================================================
static void enqueue(const char *type, const String &data, bool urgent) {
  Lock lock;
  Msg m;
  m.seq = seq_++;
  m.unlimited = urgent;
  m.attempts = 0;
  m.last_ms = 0;
  m.body = String("{\"v\":1,\"node\":\"" NODE_ID "\",\"seq\":") + m.seq +
           ",\"type\":\"" + type + "\",\"t\":" + millis() + ",\"data\":" + data + "}";
  stat_sent++;

  // คิวเต็ม: ทิ้งข้อความเก่าสุดที่ไม่ใช่ emergency (ตัวหน้าสุดกำลังรอ ACK อยู่ ข้ามไป)
  if (queue_.size() >= CFG_NETWORK_NODE_QUEUE_SIZE) {
    for (size_t i = 1; i < queue_.size(); i++) {
      if (!queue_[i].unlimited) {
        queue_.erase(queue_.begin() + i);
        stat_dropped++;
        break;
      }
    }
  }
  if (urgent) queue_.push_front(m);   // emergency แซงคิว
  else queue_.push_back(m);
}

static void serviceQueue() {
  Lock lock;
  // ต้องส่งจาก socket ที่ bind พอร์ตไว้แล้ว เพราะ Gateway ตอบ ACK กลับมาที่พอร์ตต้นทาง
  if (queue_.empty() || !gateway_ok || !udp_ready || WiFi.status() != WL_CONNECTED) return;
  Msg &m = queue_.front();
  uint32_t now = millis();
  if (m.attempts > 0 && now - m.last_ms < CFG_NETWORK_ACK_TIMEOUT_MS) return;   // ยังรอ ACK อยู่

  if (!m.unlimited && m.attempts > CFG_NETWORK_MAX_RETRY) {                       // ครบ 1 + 5 ครั้งแล้ว
    Serial.printf("  x drop seq=%u after %d retries\n", m.seq, CFG_NETWORK_MAX_RETRY);
    stat_dropped++;
    queue_.pop_front();
    return;
  }
  udp.beginPacket(gateway, CFG_NETWORK_UDP_PORT);
  udp.write((const uint8_t *)m.body.c_str(), m.body.length());
  udp.endPacket();
  if (m.attempts > 0) Serial.printf("  retry seq=%u (%d)\n", m.seq, m.attempts);
  m.attempts++;
  m.last_ms = now;
}

// ACK จาก Gateway: {"v":1,"type":"ack","node":"node1","seq":123}
static void readAcks() {
  // ถ้ายังไม่ได้ udp.begin() socket ยังไม่เปิด parsePacket จะพ่น error 9 รัวๆ
  if (!udp_ready || WiFi.status() != WL_CONNECTED) return;
  int n;
  while ((n = udp.parsePacket()) > 0) {
    char buf[128];
    int len = udp.read(buf, sizeof(buf) - 1);
    if (len <= 0) continue;
    buf[len] = 0;
    if (!strstr(buf, "\"type\":\"ack\"")) continue;
    const char *p = strstr(buf, "\"seq\":");
    if (!p) continue;
    uint32_t seq = strtoul(p + 6, nullptr, 10);
    Lock lock;
    for (size_t i = 0; i < queue_.size(); i++) {
      if (queue_[i].seq == seq) {
        queue_.erase(queue_.begin() + i);
        stat_acked++;
        break;
      }
    }
  }
}

// ===================================================================
// Wi-Fi และการหา Gateway
// ===================================================================
static void resolveGateway() {
#ifdef GATEWAY_IP
  gateway_ok = gateway.fromString(GATEWAY_IP);
#else
  String host = CFG_NETWORK_GATEWAY_HOST;
  host.replace(".local", "");
  gateway = MDNS.queryHost(host.c_str(), 2000);
  gateway_ok = gateway != IPAddress(0, 0, 0, 0);
#endif
  if (gateway_ok) Serial.printf("gateway = %s:%d\n", gateway.toString().c_str(), CFG_NETWORK_UDP_PORT);
  else Serial.println("gateway not found (ใส่ GATEWAY_IP ใน secrets.h)");
}

static void keepWifi() {
  static uint32_t last_try = 0;
  static bool was_connected = false;
  bool connected = WiFi.status() == WL_CONNECTED;
  if (connected && !was_connected) {
    Serial.printf("wifi connected, ip=%s rssi=%d\n", WiFi.localIP().toString().c_str(), WiFi.RSSI());
    MDNS.begin(NODE_ID);
    udp_ready = udp.begin(CFG_NETWORK_UDP_PORT);   // พอร์ตฝั่ง Node สำหรับรับ ACK
    resolveGateway();
  }
  if (!connected && was_connected) {   // หลุด: ปิด socket เดิม ต่อใหม่ได้ค่อย begin ใหม่
    udp.stop();
    udp_ready = false;
  }
  if (!connected && millis() - last_try > 5000) {   // หลุด: ลองต่อใหม่ทุก 5 วินาที ข้อความรอในคิว
    last_try = millis();
    if (was_connected) Serial.println("wifi lost, reconnecting...");
    WiFi.reconnect();
  }
  if (connected && !gateway_ok && millis() - last_try > 5000) {
    last_try = millis();
    resolveGateway();
  }
  was_connected = connected;
}

// ===================================================================
// เซนเซอร์
// ===================================================================
static int readFsr(int pin) {
  int sum = 0;
  for (int i = 0; i < 4; i++) sum += analogRead(pin);   // เฉลี่ย 4 ครั้งลดสัญญาณรบกวน
  return sum / 4;
}

static void sampleFsr(float dt_s) {
  for (int i = 0; i < N_FSR; i++) {
    raw_[i] = readFsr(FSR_PINS[i]);
    if (raw_[i] > CFG_PRESSURE_FSR_ON_THRESHOLD) {
      dwell_s[i] += dt_s * CFG_DEMO_TIME_SCALE;          // ตอนเดโมตั้ง time_scale = 60 ให้ 1 วินาทีเท่ากับ 1 นาที
      below_ms[i] = 0;
    } else {
      below_ms[i] += (uint32_t)(dt_s * 1000);
      if (below_ms[i] >= CFG_PRESSURE_DWELL_RESET_S * 1000UL) dwell_s[i] = 0;   // คลายแรงกดนานพอแล้ว
    }
  }
}

static void sendFsr() {
  String raw = "[", dwell = "[";
  for (int i = 0; i < N_FSR; i++) {
    if (i) { raw += ","; dwell += ","; }
    raw += raw_[i];
    dwell += (long)dwell_s[i];
  }
  enqueue("fsr", String("{\"raw\":") + raw + "],\"dwell_s\":" + dwell + "]}", false);
}

static void sendHeartbeat() {
  String d = String("{\"rssi\":") + WiFi.RSSI() + ",\"fw\":\"" FW_VERSION "\",\"faults\":[]" +
             ",\"sent\":" + stat_sent + ",\"acked\":" + stat_acked + ",\"dropped\":" + stat_dropped + "}";
  enqueue("hb", d, false);
}

// task นี้หลับอยู่ตลอด (ulTaskNotifyTake block) ตื่นเมื่อ ISR ปลุกเท่านั้น
static void cordTask(void *) {
  uint32_t last_ms = 0;
  for (;;) {
    ulTaskNotifyTake(pdTRUE, portMAX_DELAY);
    vTaskDelay(pdMS_TO_TICKS(20));                                  // debounce
    if (digitalRead(CFG_PINS_NODE1_PULL_CORD) != LOW) continue;     // สัญญาณรบกวน ไม่ได้ดึงจริง
    if (last_ms && millis() - last_ms < 3000) continue;             // ดึงซ้ำภายใน 3 วินาทีนับเป็นครั้งเดียว
    last_ms = millis();
    Serial.println("!!! PULL CORD");
    enqueue("emergency", "{\"source\":\"pull_cord\"}", true);
  }
}

// ===================================================================
void setup() {
  Serial.begin(115200);
  delay(500);
  Serial.println("\nNode1 " FW_VERSION " (FSR x6 + pull cord)");

  analogReadResolution(12);                 // 0-4095
  analogSetAttenuation(ADC_11db);           // อ่านได้ถึงประมาณ 3.1 V
  queue_lock = xSemaphoreCreateMutex();
  xTaskCreate(cordTask, "cord", 4096, nullptr, 3, &cord_task);   // priority สูงกว่า loop (1)
  pinMode(CFG_PINS_NODE1_PULL_CORD, INPUT_PULLUP);
  attachInterrupt(digitalPinToInterrupt(CFG_PINS_NODE1_PULL_CORD), onPullCord, FALLING);

  WiFi.mode(WIFI_STA);
  WiFi.setSleep(false);                     // ปิดประหยัดพลังงาน ACK จะเร็วขึ้น
  WiFi.begin(WIFI_SSID, WIFI_PASS);
  Serial.printf("connecting to %s ...\n", WIFI_SSID);
}

void loop() {
  static uint32_t next_fsr = 0, next_hb = 0, last_sample = millis();
  uint32_t now = millis();

  keepWifi();
  readAcks();
  serviceQueue();

  if ((int32_t)(now - next_fsr) >= 0) {
    next_fsr = now + CFG_RATES_FSR_EVERY_S * 1000;
    sampleFsr((now - last_sample) / 1000.0f);
    last_sample = now;
    sendFsr();
    size_t qn;
    { Lock lock; qn = queue_.size(); }
    Serial.printf("raw %4d %4d %4d %4d %4d %4d | dwell_hip_l=%lds | queue=%u acked=%u/%u\n",
                  raw_[0], raw_[1], raw_[2], raw_[3], raw_[4], raw_[5], (long)dwell_s[2],
                  (unsigned)qn, stat_acked, stat_sent);
  }
  if ((int32_t)(now - next_hb) >= 0) {
    next_hb = now + CFG_RATES_HB_EVERY_S * 1000;
    sendHeartbeat();
  }
  delay(2);
}
