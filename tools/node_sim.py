#!/usr/bin/env python3
"""จำลอง Sensor Node ส่งข้อความให้ Gateway ตาม Contract ข้อ 3 พร้อม ACK/Retry แบบเดียวกับ Node จริง

ตัวอย่าง:
  python tools/node_sim.py --host smartbed-gw.local            # จำลอง node1
  python tools/node_sim.py --host 127.0.0.1 --node node3       # จำลอง Node ที่ 3 (ทดสอบ Scalability)
  python tools/node_sim.py --host 127.0.0.1 --loss 0.3         # ทิ้ง packet 30% ทดสอบ Retry และการกันซ้ำ
  python tools/node_sim.py --host 127.0.0.1 --pull-cord 15     # กดสายดึงฉุกเฉินหลังเริ่ม 15 วินาที
  python tools/node_sim.py --host 127.0.0.1 --node node2 --choke 10       # node2 ได้ยินเสียงสำลักที่วินาทีที่ 10
  python tools/node_sim.py --host 127.0.0.1 --dwell-start 7190            # แรงกดสะสมเกือบ 120 นาทีตั้งแต่เริ่ม
  python tools/node_sim.py --host 127.0.0.1 --node node2 --fever          # อุณหภูมิร่างกาย 38.2 °C

กด Ctrl+C เพื่อหยุด จะสรุป packet delivery rate ให้
"""
import argparse
import json
import random
import socket
import time
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
CFG = yaml.safe_load((ROOT / "shared" / "config.yaml").read_text(encoding="utf-8"))
NET, RATES, PRESS = CFG["network"], CFG["rates"], CFG["pressure"]

# ค่าแรงกดพื้นฐานตอนนอนหงาย [ไหล่ซ้าย, ไหล่ขวา, สะโพกซ้าย, สะโพกขวา, ส้นซ้าย, ส้นขวา]
BASE = [900, 880, 1700, 1650, 600, 580]


class SimNode:
    def __init__(self, host, node, loss):
        self.addr = (socket.gethostbyname(host), NET["udp_port"])
        self.node, self.loss = node, loss
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.seq = 0
        self.t0 = time.monotonic()
        self.sent = self.acked = self.dropped = self.retries = 0

    def send(self, type_, data, unlimited=False):
        msg = {"v": 1, "node": self.node, "seq": self.seq, "type": type_,
               "t": int((time.monotonic() - self.t0) * 1000), "data": data}
        self.seq += 1
        raw = json.dumps(msg, separators=(",", ":")).encode()
        self.sent += 1
        attempt = 0
        while True:
            if random.random() >= self.loss:          # จำลอง packet หายระหว่างทาง
                self.sock.sendto(raw, self.addr)
            if self._wait_ack(msg["seq"]):
                self.acked += 1
                return True
            attempt += 1
            if not unlimited and attempt > NET["max_retry"]:
                self.dropped += 1
                print(f"  ✗ {type_} seq={msg['seq']} dropped after {NET['max_retry']} retries")
                return False
            self.retries += 1

    def _wait_ack(self, seq):
        deadline = time.monotonic() + NET["ack_timeout_ms"] / 1000
        while (left := deadline - time.monotonic()) > 0:
            self.sock.settimeout(left)
            try:
                data, _ = self.sock.recvfrom(512)
            except socket.timeout:
                return False
            if random.random() < self.loss:           # จำลอง ACK หายขากลับ
                continue
            ack = json.loads(data)
            if ack.get("type") == "ack" and ack.get("seq") == seq:
                return True
        return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default=NET["gateway_host"])
    ap.add_argument("--node", default="node1")
    ap.add_argument("--loss", type=float, default=0.0, help="โอกาส packet หาย 0.0–1.0")
    ap.add_argument("--pull-cord", type=float, default=None, help="ส่ง emergency หลังเริ่มกี่วินาที")
    ap.add_argument("--choke", type=float, default=None, help="ส่ง audio choke หลังเริ่มกี่วินาที (ใช้กับ node2)")
    ap.add_argument("--fever", action="store_true", help="ส่ง temp 38.2 แทน 36.6")
    ap.add_argument("--dwell-start", type=float, default=0, help="ค่า dwell_s เริ่มต้น (วินาที)")
    args = ap.parse_args()

    n = SimNode(args.host, args.node, args.loss)
    print(f"{args.node} → {n.addr[0]}:{n.addr[1]}  loss={args.loss}")
    dwell = [args.dwell_start] * 6
    next_temp = 0
    choke_done = args.choke is None
    next_hb = 0
    cord_done = args.pull_cord is None
    try:
        while True:
            el = time.monotonic() - n.t0
            raw = [max(0, min(4095, int(b + random.gauss(0, 40)))) for b in BASE]
            for i, r in enumerate(raw):
                dwell[i] = dwell[i] + CFG["demo"]["time_scale"] if r > PRESS["fsr_on_threshold"] else 0
            if args.node != "node2":                    # node2 จริงไม่มี FSR
                ok = n.send("fsr", {"raw": raw, "dwell_s": [round(d) for d in dwell]})
                print(f"{'✓' if ok else '✗'} fsr seq={n.seq - 1} hip_l={raw[2]} dwell={dwell[2]:.0f}s")

            if el >= next_hb:
                next_hb = el + RATES["hb_every_s"]
                n.send("hb", {"rssi": -55, "fw": "sim", "faults": [],
                              "sent": n.sent, "acked": n.acked, "dropped": n.dropped})
            if args.node != "node1" and el >= next_temp:
                next_temp = el + RATES["temp_every_s"]
                n.send("temp", {"body_c": 38.2 if args.fever else round(random.gauss(36.6, 0.1), 1)})
            if not choke_done and el >= args.choke:
                choke_done = True
                print("😮‍💨 choke")
                n.send("audio", {"label": "choke", "conf": 0.93})
            if not cord_done and el >= args.pull_cord:
                cord_done = True
                print("🆘 pull cord")
                n.send("emergency", {"source": "pull_cord"}, unlimited=True)
            time.sleep(max(0, RATES["fsr_every_s"] - (time.monotonic() - n.t0 - el)))
    except KeyboardInterrupt:
        rate = 100 * n.acked / n.sent if n.sent else 0
        print(f"\nsent={n.sent} acked={n.acked} dropped={n.dropped} retries={n.retries} "
              f"delivery={rate:.1f}%")


if __name__ == "__main__":
    main()
