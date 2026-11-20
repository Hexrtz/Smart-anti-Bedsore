#!/usr/bin/env python3
"""Gateway จำลองสำหรับทดสอบ Node โดยไม่ต้องใช้ Raspberry Pi (เจ้าของ: Natthasit)

ทำตัวเหมือน gateway/receiver.py ฝั่งรับ:
  1. รับ UDP ที่พอร์ตใน shared/config.yaml (5005)
  2. แปลง JSON + ตรวจ Schema (tools/check_schema.py)  ไม่ผ่าน -> แสดง error, ไม่ตอบ ACK
  3. ตอบ ACK {"v":1,"type":"ack","node":..,"seq":..} กลับไปที่ IP:port ต้นทาง
  4. กันซ้ำด้วย seq ล่าสุด 50 ค่าต่อ node (ข้อความซ้ำยังได้ ACK แต่ไม่นับเป็นข้อความใหม่)

จำลอง packet loss เพื่อทดสอบ ACK/Retry ของ Node (E4):
  --drop 0.3      ทิ้ง packet ขาเข้า 30% (เหมือนไม่เคยได้รับ จึงไม่ตอบ ACK)
  --ack-drop 0.1  ได้รับแล้วแต่ทิ้ง ACK 10% (Node จะส่งซ้ำ -> ทดสอบการกันซ้ำ)

ใช้:
  python tools/gw_sim.py                    # ปกติ
  python tools/gw_sim.py --drop 0.3         # ทดสอบ delivery >= 99% ที่ loss 30%
  python tools/gw_sim.py --quiet            # แสดงเฉพาะสรุป
กด Ctrl+C เพื่อหยุดและดูสรุปสุดท้าย

สรุปต่อ node:
  unique     ข้อความที่ได้รับ (ไม่นับซ้ำ)
  missing    seq ที่ขาดหายระหว่าง seq แรกถึงล่าสุด (Node retry ครบแล้วก็ยังไม่ถึง)
  delivery   unique / (unique + missing)  <- ตัวเลขสำหรับรายงาน
  hb         ตัวนับ sent/acked/dropped จาก heartbeat ล่าสุดของ Node (มุมมองฝั่ง Node)
"""
import argparse
import json
import random
import signal
import socket
import sys
import time
from collections import deque
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
from check_schema import validate  # noqa: E402

CONFIG = yaml.safe_load((ROOT / "shared" / "config.yaml").read_text(encoding="utf-8"))
NET = CONFIG["network"]


class NodeStats:
    def __init__(self, window: int):
        self.seen = deque(maxlen=window)  # seq ล่าสุด N ค่า สำหรับกันซ้ำ
        self.got = set()                  # ทุก seq ที่เคยได้รับ สำหรับนับ missing
        self.first_seq = None
        self.max_seq = -1
        self.duplicates = 0
        self.sim_dropped = 0
        self.ack_dropped = 0
        self.by_type: dict[str, int] = {}
        self.last_hb = None
        self.addr = None

    def is_duplicate(self, seq: int) -> bool:
        if seq in self.seen:
            return True
        self.seen.append(seq)
        return False

    def record(self, seq: int, mtype: str):
        if self.first_seq is None or seq < self.first_seq - NET["dedup_window"]:
            # ครั้งแรก หรือ seq ตกลงมาก = Node รีบูต เริ่มนับใหม่
            self.got.clear()
            self.first_seq = seq
            self.max_seq = seq
        self.first_seq = min(self.first_seq, seq)
        self.max_seq = max(self.max_seq, seq)
        self.got.add(seq)
        self.by_type[mtype] = self.by_type.get(mtype, 0) + 1

    def summary(self, node: str) -> str:
        unique = len(self.got)
        expected = self.max_seq - self.first_seq + 1 if self.first_seq is not None else 0
        missing = max(expected - unique, 0)
        rate = 100.0 * unique / expected if expected else 0.0
        types = " ".join(f"{k}={v}" for k, v in sorted(self.by_type.items()))
        line = (f"  {node} {self.addr[0] if self.addr else '?'}: unique {unique}, missing {missing}, "
                f"delivery {rate:.2f}%, dup {self.duplicates}, sim-drop {self.sim_dropped}, "
                f"ack-drop {self.ack_dropped}  [{types}]")
        if self.last_hb:
            d = self.last_hb
            sent = d["sent"]
            node_rate = 100.0 * d["acked"] / sent if sent else 0.0
            line += (f"\n     hb: rssi {d['rssi']} dBm, fw {d['fw']}, faults {d['faults'] or '-'}, "
                     f"sent {sent} acked {d['acked']} dropped {d['dropped']} ({node_rate:.2f}% acked)")
        return line


def local_ip() -> str:
    """IP ของเครื่องนี้ในวง Wi-Fi (ไม่ได้ส่งอะไรออกไปจริง แค่ให้ OS เลือก interface)"""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def fmt_msg(msg: dict) -> str:
    d, t = msg["data"], msg["type"]
    if t == "audio":
        return f"{d['label']:<10} conf {d['conf']:.2f}"
    if t == "temp":
        return "body_c null (sensor fault)" if d["body_c"] is None else f"body_c {d['body_c']:.1f}"
    if t == "hb":
        return f"rssi {d['rssi']} sent {d['sent']} acked {d['acked']} dropped {d['dropped']} faults {d['faults']}"
    return json.dumps(d, separators=(",", ":"))


def main():
    ap = argparse.ArgumentParser(description="Gateway simulator: UDP receive + ACK + simulated loss")
    ap.add_argument("--port", type=int, default=NET["udp_port"])
    ap.add_argument("--drop", type=float, default=0.0, help="probability to drop an incoming packet (0..1)")
    ap.add_argument("--ack-drop", type=float, default=0.0, help="probability to drop the ACK (0..1)")
    ap.add_argument("--summary-every", type=float, default=30.0, help="seconds between summaries")
    ap.add_argument("--quiet", action="store_true", help="print only summaries and errors")
    ap.add_argument("--seed", type=int, help="random seed (repeatable loss pattern)")
    args = ap.parse_args()
    sys.stdout.reconfigure(line_buffering=True)  # show each line right away, even when piped to a file
    signal.signal(signal.SIGTERM, signal.default_int_handler)  # kill = Ctrl+C: still print the final summary
    rng = random.Random(args.seed)

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("0.0.0.0", args.port))
    sock.settimeout(1.0)

    print(f"gw_sim listening on UDP {local_ip()}:{args.port}  drop {args.drop:.0%}  ack-drop {args.ack_drop:.0%}")
    print(f"-> on the node set the gateway host to {local_ip()} (secrets.h GATEWAY_HOST)")

    nodes: dict[str, NodeStats] = {}
    start = last_summary = time.time()
    invalid = 0

    def print_summary(title: str):
        print(f"--- {title} ({time.time() - start:.0f} s, invalid {invalid}) ---")
        for name, st in sorted(nodes.items()):
            print(st.summary(name))

    try:
        while True:
            try:
                data, addr = sock.recvfrom(2048)
            except socket.timeout:
                data = None
            now = time.time()
            if now - last_summary >= args.summary_every:
                print_summary("summary")
                last_summary = now
            if data is None:
                continue
            stamp = time.strftime("%H:%M:%S")

            try:
                msg = json.loads(data.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                invalid += 1
                print(f"{stamp} !! non-JSON from {addr[0]}: {data[:80]!r}")
                continue
            errors = validate(msg)
            if len(data) > NET["max_packet_bytes"]:
                errors.append(f"packet {len(data)} bytes > {NET['max_packet_bytes']}")
            if errors:
                invalid += 1
                print(f"{stamp} !! invalid {msg.get('type')} from {addr[0]} (no ACK): {'; '.join(errors)}")
                continue

            node, seq, mtype = msg["node"], msg["seq"], msg["type"]
            st = nodes.setdefault(node, NodeStats(NET["dedup_window"]))
            st.addr = addr

            if rng.random() < args.drop:  # pretend the packet never arrived
                st.sim_dropped += 1
                if not args.quiet:
                    print(f"{stamp}    {node} #{seq:<5} {mtype:<9} [simulated loss]")
                continue

            if rng.random() < args.ack_drop:
                st.ack_dropped += 1
                ack_note = " [ACK dropped]"
            else:
                ack = {"v": 1, "type": "ack", "node": node, "seq": seq}
                sock.sendto(json.dumps(ack, separators=(",", ":")).encode(), addr)
                ack_note = ""

            if st.is_duplicate(seq):
                st.duplicates += 1
                if not args.quiet:
                    print(f"{stamp}    {node} #{seq:<5} {mtype:<9} duplicate (ACK resent){ack_note}")
                continue
            st.record(seq, mtype)
            if mtype == "hb":
                st.last_hb = msg["data"]
            if not args.quiet:
                print(f"{stamp} <- {node} #{seq:<5} {mtype:<9} {fmt_msg(msg)}  ({len(data)} B){ack_note}")
    except KeyboardInterrupt:
        print()
        print_summary("final")


if __name__ == "__main__":
    main()
