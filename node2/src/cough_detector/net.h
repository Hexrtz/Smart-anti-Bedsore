// Node 2 -> Gateway messaging over UDP/Wi-Fi (contract: docs/integration-contract.md, shared/schema/).
// Same queue + ACK/retry logic as node1/src/main.cpp, moved into its own FreeRTOS task.
//
//   net_send(type, data_json, front)  any task: build the envelope and put it in the queue
//   comm_task                         keeps Wi-Fi up, sends the queue head, reads ACKs, sends hb
//
// Reliability (E4): wait CFG_NETWORK_ACK_TIMEOUT_MS for an ACK, resend up to CFG_NETWORK_MAX_RETRY
// times, then drop. Queue holds CFG_NETWORK_NODE_QUEUE_SIZE messages; when full the oldest one
// (not the head, which is in flight) is dropped. Messages wait in the queue while Wi-Fi is down.
//
// Wi-Fi name / password / Gateway IP come from secrets.h in this sketch folder (gitignored).
#pragma once
#include <Arduino.h>
#include <ESPmDNS.h>
#include <WiFi.h>
#include <WiFiUdp.h>
#include <deque>

#include "secrets.h"  // copy node2/include/secrets.example.h to this folder as secrets.h

#define NODE_ID "node2"
#define FW_VERSION "1.0.0"

// values copied from shared/config.h (Arduino IDE can't include files outside the sketch)
#define NET_GATEWAY_HOST "smartbed-gw"   // CFG_NETWORK_GATEWAY_HOST without ".local"
#define NET_UDP_PORT 5005                // CFG_NETWORK_UDP_PORT
#define NET_ACK_TIMEOUT_MS 300           // CFG_NETWORK_ACK_TIMEOUT_MS
#define NET_MAX_RETRY 5                  // CFG_NETWORK_MAX_RETRY
#define NET_QUEUE_SIZE 20                // CFG_NETWORK_NODE_QUEUE_SIZE
#define NET_HB_EVERY_MS 10000            // CFG_RATES_HB_EVERY_S * 1000

struct NetMsg {
  uint32_t seq;
  String body;
  uint8_t attempts;  // how many times it was sent
  uint32_t last_ms;  // when it was last sent
};

static std::deque<NetMsg> net_queue;
static SemaphoreHandle_t net_lock;  // the queue is touched by report_task, temp_task and comm_task
static uint32_t net_seq = 0;
static uint32_t net_sent = 0, net_acked = 0, net_dropped = 0;

static WiFiUDP net_udp;
static IPAddress net_gateway;
static bool net_gateway_ok = false;
static bool net_udp_ready = false;      // socket bound (only after Wi-Fi connects)
static volatile bool net_mlx_fault = false;  // set by temp_task, reported in hb.faults

struct NetLock {
  NetLock() { xSemaphoreTake(net_lock, portMAX_DELAY); }
  ~NetLock() { xSemaphoreGive(net_lock); }
};

// Put one message in the queue. front = jump the queue (used for choke).
static void net_send(const char *type, const String &data, bool front = false) {
  NetLock lock;
  NetMsg m;
  m.seq = net_seq++;
  m.attempts = 0;
  m.last_ms = 0;
  m.body = String("{\"v\":1,\"node\":\"" NODE_ID "\",\"seq\":") + m.seq + ",\"type\":\"" + type +
           "\",\"t\":" + millis() + ",\"data\":" + data + "}";
  net_sent++;
  if (net_queue.size() >= NET_QUEUE_SIZE) {  // full: drop the oldest waiting message (index 1)
    net_queue.erase(net_queue.begin() + 1);
    net_dropped++;
  }
  if (front) net_queue.push_front(m);
  else net_queue.push_back(m);
}

// Send the queue head, or resend it if no ACK came within NET_ACK_TIMEOUT_MS.
static void net_service_queue() {
  NetLock lock;
  // must send from the bound socket: the Gateway replies with the ACK to our source port
  if (net_queue.empty() || !net_gateway_ok || !net_udp_ready || WiFi.status() != WL_CONNECTED) return;
  NetMsg &m = net_queue.front();
  uint32_t now = millis();
  if (m.attempts > 0 && now - m.last_ms < NET_ACK_TIMEOUT_MS) return;  // still waiting for the ACK
  if (m.attempts > NET_MAX_RETRY) {                                    // 1 send + 5 retries used up
    Serial.printf("  x drop seq=%u after %d retries\n", m.seq, NET_MAX_RETRY);
    net_dropped++;
    net_queue.pop_front();
    return;
  }
  net_udp.beginPacket(net_gateway, NET_UDP_PORT);
  net_udp.write((const uint8_t *)m.body.c_str(), m.body.length());
  net_udp.endPacket();
  if (m.attempts > 0) Serial.printf("  retry seq=%u (%d)\n", m.seq, m.attempts);
  m.attempts++;
  m.last_ms = now;
}

// ACK from the Gateway: {"v":1,"type":"ack","node":"node2","seq":123} -> remove that seq.
static void net_read_acks() {
  if (!net_udp_ready || WiFi.status() != WL_CONNECTED) return;
  while (net_udp.parsePacket() > 0) {
    char buf[128];
    int len = net_udp.read(buf, sizeof(buf) - 1);
    if (len <= 0) continue;
    buf[len] = 0;
    if (!strstr(buf, "\"type\":\"ack\"")) continue;
    const char *p = strstr(buf, "\"seq\":");
    if (!p) continue;
    uint32_t seq = strtoul(p + 6, nullptr, 10);
    NetLock lock;
    for (size_t i = 0; i < net_queue.size(); i++) {
      if (net_queue[i].seq == seq) {
        net_queue.erase(net_queue.begin() + i);
        net_acked++;
        break;
      }
    }
  }
}

// GATEWAY_IP in secrets.h wins; without it, look up smartbed-gw.local with mDNS.
static void net_resolve_gateway() {
#ifdef GATEWAY_IP
  net_gateway_ok = net_gateway.fromString(GATEWAY_IP);
#else
  net_gateway = MDNS.queryHost(NET_GATEWAY_HOST, 2000);
  net_gateway_ok = net_gateway != IPAddress(0, 0, 0, 0);
#endif
  if (net_gateway_ok) Serial.printf("gateway = %s:%d\n", net_gateway.toString().c_str(), NET_UDP_PORT);
  else Serial.println("gateway not found (set GATEWAY_IP in secrets.h)");
}

static void net_keep_wifi() {
  static uint32_t last_try = 0;
  static bool was_connected = false;
  bool connected = WiFi.status() == WL_CONNECTED;
  if (connected && !was_connected) {
    Serial.printf("wifi connected, ip=%s rssi=%d\n", WiFi.localIP().toString().c_str(), WiFi.RSSI());
    MDNS.begin(NODE_ID);
    net_udp_ready = net_udp.begin(NET_UDP_PORT);  // our port for receiving ACKs
    net_resolve_gateway();
  }
  if (!connected && was_connected) {  // lost: close the socket, reopen after reconnecting
    net_udp.stop();
    net_udp_ready = false;
  }
  if (!connected && millis() - last_try > 5000) {  // retry every 5 s, messages wait in the queue
    last_try = millis();
    if (was_connected) Serial.println("wifi lost, reconnecting...");
    WiFi.reconnect();
  }
  if (connected && !net_gateway_ok && millis() - last_try > 5000) {
    last_try = millis();
    net_resolve_gateway();
  }
  was_connected = connected;
}

static void net_send_heartbeat() {
  String faults = net_mlx_fault ? "[\"mlx90614\"]" : "[]";
  String d = String("{\"rssi\":") + WiFi.RSSI() + ",\"fw\":\"" FW_VERSION "\",\"faults\":" + faults +
             ",\"sent\":" + net_sent + ",\"acked\":" + net_acked + ",\"dropped\":" + net_dropped + "}";
  net_send("hb", d);
}

// Lowest-priority task: networking never delays audio capture or inference.
static void comm_task(void *) {
  uint32_t next_hb = millis();
  for (;;) {
    net_keep_wifi();
    net_read_acks();
    net_service_queue();
    if ((int32_t)(millis() - next_hb) >= 0) {
      next_hb += NET_HB_EVERY_MS;
      net_send_heartbeat();
    }
    vTaskDelay(pdMS_TO_TICKS(5));  // sleep between rounds; ACK timeout is 300 ms so 5 ms is plenty
  }
}

static void net_begin() {
  net_lock = xSemaphoreCreateMutex();
  WiFi.mode(WIFI_STA);
  WiFi.setSleep(false);  // no power save: ACKs arrive faster
  WiFi.begin(WIFI_SSID, WIFI_PASS);
  Serial.printf("connecting to %s ...\n", WIFI_SSID);
}
