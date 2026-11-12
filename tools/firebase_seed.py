#!/usr/bin/env python3
"""ใส่ข้อมูลจำลองลง Firebase RTDB แทน Gateway เพื่อพัฒนา Dashboard และ Notifier (เจ้าของ: Nantapong)

ทำตัวเหมือน Gateway ทุกอย่างตาม Integration Contract ข้อ 4:
  - จำลอง Node สร้างข้อความตาม shared/schema/ (ตรวจด้วย validate() ของ check_schema.py ทุกข้อความ)
  - เขียน live/* ทุก 1 วินาที, history/{yyyy-mm-dd}/{pushId}, events/{pushId}
  - ฟัง commands/backrest แล้วตอบแบบ Gateway (moving 1 วินาที แล้ว up/flat, status=done)

ใช้:
  python tools/firebase_seed.py                       # 2 node, เขียน Firebase จริง (อ่าน credential จาก .env)
  python tools/firebase_seed.py --time-scale 60       # โหมดเดโม: 1 วินาทีจริง = 1 นาทีของ dwell
  python tools/firebase_seed.py --event choke         # ยิง event หลังเริ่ม 5 วินาที (ใส่ --event ซ้ำได้)
  python tools/firebase_seed.py --nodes 3             # node3 ขึ้นไปส่งแค่ hb
  python tools/firebase_seed.py --dry-run --duration 30   # ไม่ต่อ Firebase แค่พิมพ์สิ่งที่จะเขียน

Credential อ่านจากไฟล์ .env ที่ root ของ repo เท่านั้น: FIREBASE_DB_URL, FIREBASE_SA_PATH, BED_ID (ไม่ใส่ = ใช้ bed_id ใน config)
"""
from __future__ import annotations  # ให้ type hint แบบ float | None ใช้ได้บน Python 3.9

import argparse
import json
import random
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

import yaml

TOOLS = Path(__file__).resolve().parent
ROOT = TOOLS.parent
sys.path.insert(0, str(TOOLS))
from check_schema import validate  # noqa: E402  ตรวจข้อความ Node กับ shared/schema/

CFG = yaml.safe_load((ROOT / "shared" / "config.yaml").read_text(encoding="utf-8"))
NET, RATES, PRESS = CFG["network"], CFG["rates"], CFG["pressure"]
AUDIO, TEMP, MOTOR = CFG["audio"], CFG["temperature"], CFG["motor"]
FSR_ORDER = CFG["fsr_order"]

# Enum จาก Contract ข้อ 4.1
BACKREST_STATES = {"flat", "moving", "up", "fault"}
EVENT_TYPES = ("choke", "pressure", "pull_cord", "fever", "node_offline", "motor_fault", "sensor_fault")
HISTORY_TYPES = {"fsr", "audio", "temp", "emergency", "hb", "env"}

# ============================================================================
# สิ่งที่ Contract ข้อ 4 ยังไม่ระบุ (รวมไว้ที่เดียว แก้ตรงนี้เมื่อ Contract อัปเดต)
# ============================================================================

# TODO(contract): severity ของแต่ละ event ยังไม่อยู่ใน Contract รอ Phatcharapon ยืนยัน
# ที่ใช้อยู่ (Nantapong เลือก 12 พ.ย.): critical = อันตรายทันทีหรือเตียงเสีย, ที่เหลือ warn
SEVERITY = {"choke": "critical", "pull_cord": "critical", "motor_fault": "critical",
            "pressure": "warn", "fever": "warn", "node_offline": "warn", "sensor_fault": "warn"}

# TODO(contract): โครง events/{pushId}.detail ของแต่ละ type ยังไม่อยู่ใน Contract รอ Phatcharapon ยืนยัน
# detail เป็น object, field เรียงตามลำดับนี้ (event() จะเช็กว่าส่ง field มาครบและไม่เกิน)
EVENT_DETAIL_FIELDS = {
    "choke":        ("node", "label", "conf"),       # node ที่ได้ยิน, label "choke", ความมั่นใจของโมเดล
    "pressure":     ("node", "point", "dwell_min"),  # จุด FSR ตาม fsr_order, dwell สะสม (นาที)
    "pull_cord":    ("node", "source"),              # source = "pull_cord"
    "fever":        ("node", "body_c"),              # อุณหภูมิที่ทำให้เกิด event (°C)
    "node_offline": ("node", "last_seen"),           # unix ms ที่ได้ยินครั้งสุดท้าย
    "motor_fault":  ("target", "timeout_s"),         # ปลายทางที่หมุนไปไม่ถึง (up|flat), motor.timeout_s
    "sensor_fault": ("node", "sensor"),              # ชื่อเซนเซอร์จาก hb.faults เช่น mlx90614
}

# TODO(contract): หน่วยของ aq ยังไม่ตกลง (ถาม Sirapop) ตอนนี้ใช้ค่า raw ของ ADS1115 (0-32767) ไปก่อน
AQ_BASE_RAW = 9000

# NOTE สมมติฐานอื่นที่ต้องตรงกับ Gateway (Sirapop):
#   - วันที่ใน history/{yyyy-mm-dd} ใช้ local time ของเครื่องที่รัน (บน Pi = เวลาไทย) ไม่ใช่ UTC
#   - dwell_s ที่ Node ส่งมา คูณ demo time_scale แล้ว (Gateway แค่หาร 60 เป็น dwell_min)
#   - audio.choke_count / choke_window_s ยังไม่ใช้: ตอนนี้ได้ label "choke" ครั้งเดียวก็สร้าง event
#     รอตกลงว่า Node หรือ Gateway เป็นคนนับ 3 ครั้งใน 5 วินาที

MOVE_S = 1.0          # เวลาที่พนักพิงอยู่ในสถานะ moving (ตามที่ Dashboard ต้องการทดสอบ)
FW = "1.0.0"


def now_ms() -> int:
    return int(time.time() * 1000)


def log(msg: str):
    print(time.strftime("%H:%M:%S"), msg, flush=True)


# ---------------------------------------------------------------- ปลายทางที่เขียน

class FirebaseSink:
    """เขียน Firebase จริง ทุก path อ้างอิงจาก /beds/{bed_id}/"""

    def __init__(self, bed_id: str, env: dict):
        import firebase_admin
        from firebase_admin import credentials, db
        try:
            cred = credentials.Certificate(env["FIREBASE_SA_PATH"])
            firebase_admin.initialize_app(cred, {"databaseURL": env["FIREBASE_DB_URL"]})
        except Exception as e:  # ไม่พิมพ์รายละเอียดเพราะอาจมี path หรือ URL ของ credential
            sys.exit(f"ต่อ Firebase ไม่ได้ ({type(e).__name__}) ตรวจ FIREBASE_SA_PATH / FIREBASE_DB_URL ใน .env")
        self.base = db.reference(f"beds/{bed_id}")
        self.listener = None

    def update(self, values: dict):
        """multi-path update: key เป็น path ย่อย เช่น 'live/updated' (ส่งครั้งเดียว)"""
        self.base.update(values)

    def push(self, path: str, value: dict):
        self.base.child(path).push(value)

    def set(self, path: str, value):
        self.base.child(path).set(value)

    def get(self, path: str):
        return self.base.child(path).get()

    def listen(self, path: str, callback):
        self.listener = self.base.child(path).listen(callback)

    def close(self):
        if self.listener:
            self.listener.close()


class DryRunSink:
    """ไม่ต่อเน็ต พิมพ์สิ่งที่จะเขียนแทน (live พิมพ์ทุก 10 วินาที ไม่ให้รก)"""

    def __init__(self, bed_id: str):
        self.prefix = f"/beds/{bed_id}/"
        self.n_live = 0

    def update(self, values: dict):
        self.n_live += 1
        if self.n_live % 10 == 1:
            log(f"UPDATE {self.prefix} {json.dumps(values, ensure_ascii=False)}")

    def push(self, path: str, value: dict):
        log(f"PUSH   {self.prefix}{path} {json.dumps(value, ensure_ascii=False)}")

    def set(self, path: str, value):
        log(f"SET    {self.prefix}{path} {json.dumps(value, ensure_ascii=False)}")

    def get(self, path: str):
        return None

    def listen(self, path: str, callback):
        log(f"(dry-run: ไม่ได้ฟัง {self.prefix}{path})")

    def close(self):
        pass


def load_env() -> dict:
    """อ่าน .env โดยตรง (ไม่ใช้ตัวแปรของ shell) และไม่พิมพ์ค่าใด ๆ ออกมา"""
    from dotenv import dotenv_values
    path = ROOT / ".env"
    if not path.exists():
        sys.exit("ไม่พบ .env ที่ root ของ repo (คัดลอกจาก .env.example แล้วใส่ค่า)")
    env = dotenv_values(path)
    missing = [k for k in ("FIREBASE_DB_URL", "FIREBASE_SA_PATH") if not env.get(k)]
    if missing:
        sys.exit(f".env ขาดค่า: {', '.join(missing)}")
    return env


# ---------------------------------------------------------------- Node จำลอง

class SimNode:
    """สร้างข้อความตาม Contract ข้อ 3.2 และนับ sent/acked/dropped สำหรับ hb"""

    def __init__(self, node_id: str):
        self.id = node_id
        self.boot = time.monotonic()
        self.seq = 0
        self.sent = self.acked = self.dropped = 0
        self.rssi = random.randint(-68, -55)
        self.faults: list[str] = []
        self.silent_until = 0.0          # ใช้จำลอง node_offline (ไม่ส่งอะไรเลยจนถึงเวลานี้)

    def silent(self) -> bool:
        return time.monotonic() < self.silent_until

    def msg(self, mtype: str, data: dict) -> dict:
        m = {"v": CFG["contract_version"], "node": self.id, "seq": self.seq, "type": mtype,
             "t": int((time.monotonic() - self.boot) * 1000), "data": data}
        errs = validate(m)
        size = len(json.dumps(m, separators=(",", ":")).encode())
        if size > NET["max_packet_bytes"]:
            errs.append(f"packet {size} bytes > {NET['max_packet_bytes']}")
        if errs:  # ถ้าตัวจำลองสร้างข้อความผิด Schema ให้หยุดทันที จะได้ไม่ส่งของผิดให้ Dashboard
            raise RuntimeError(f"{mtype} ไม่ผ่าน schema: {errs}")
        self.seq += 1
        self.sent += 1
        if random.random() < 0.003:      # จำลองข้อความที่ retry ครบแล้วยังหาย
            self.dropped += 1
        else:
            self.acked += 1
        return m

    def hb(self) -> dict:
        self.rssi = max(-85, min(-45, self.rssi + random.randint(-2, 2)))
        return self.msg("hb", {"rssi": self.rssi, "fw": FW, "faults": list(self.faults),
                               "sent": self.sent, "acked": self.acked, "dropped": self.dropped})


# ---------------------------------------------------------------- ตัวจำลองหลัก (ทำหน้าที่ Gateway)

class Seeder:
    def __init__(self, sink, n_nodes: int, time_scale: float):
        self.sink = sink
        self.scale = time_scale
        self.lock = threading.Lock()     # thread หลักกับ thread ที่ฟัง commands ใช้ state ร่วมกัน
        self.nodes = [SimNode(f"node{i}") for i in range(1, n_nodes + 1)]
        self.node1 = self.nodes[0]
        self.node2 = self.nodes[1] if n_nodes >= 2 else None

        # ผู้ป่วยนอนหงาย: สะโพกรับน้ำหนักมากสุด ส้นเท้าน้อยสุด (raw 0-4095)
        self.fsr_base = {"shoulder_l": 900, "shoulder_r": 870, "hip_l": 1650,
                         "hip_r": 1580, "heel_l": 430, "heel_r": 410}
        self.fsr_raw = {p: 0 for p in FSR_ORDER}
        # เริ่มด้วย dwell ไม่เท่ากัน ให้ Dashboard ไม่เป็นศูนย์หมด (วินาที)
        self.dwell_s = {p: float(random.randint(lo, hi) * 60) for p, (lo, hi) in zip(
            FSR_ORDER, [(10, 25), (10, 25), (40, 70), (40, 70), (20, 40), (20, 40)])}
        self.below_since = {p: None for p in FSR_ORDER}
        self.lean = 0.0                  # น้ำหนักเอียงซ้าย(+)/ขวา(-)
        self.heel_lift_until = 0.0

        self.body_c = 36.7
        self.body_target = 36.7
        self.audio = {"label": "background", "conf": 0.95}
        self.choke_until = 0.0
        self.env = {"room_c": 28.5, "humidity": 62.0, "aq": AQ_BASE_RAW}

        self.backrest = "flat"
        self.motor = None                # {"target", "finish", "fail", "cmd"} ระหว่างหมุน
        self.handled_cmd = None

        # สถานะของกฎฝั่ง Gateway
        self.last_seen = {}              # node -> unix ms ที่ได้ยินล่าสุด
        self.node_live = {}              # node -> {"rssi", "faults"} จาก hb ล่าสุด
        self.offline_fired = set()
        self.known_faults = {}           # node -> set ของ fault ที่สร้าง event แล้ว
        self.pressure_fired = set()
        self.fever_since = None
        self.fever_fired = False
        self.choke_fired = False
        self.last_fsr_history = {}
        # ค่าที่ Gateway ได้รับล่าสุด (live เขียนจากค่านี้ ถ้า node เงียบ live จะค้างค่าเดิมเหมือนของจริง)
        self.rx_fsr = None               # (raw[6], dwell_s[6])
        self.rx_body_c = None
        self.rx_audio = None

        self.t0 = time.monotonic()
        self.scheduled = []              # [(เวลา monotonic, ชื่อ event)]

    # ---------- ฝั่ง Gateway: รับข้อความจาก Node

    def receive(self, m: dict):
        ts = now_ms()                    # เวลาจริงให้ Gateway ประทับตอนรับ (Contract 3.2)
        node, mtype, data = m["node"], m["type"], m["data"]
        self.last_seen[node] = ts
        if node in self.offline_fired:
            self.offline_fired.discard(node)
            log(f"{node} กลับมา online")
        if mtype == "fsr":
            self.rx_fsr = (data["raw"], data["dwell_s"])
        elif mtype == "temp":
            self.rx_body_c = data["body_c"]
        elif mtype == "audio":
            self.rx_audio = dict(data)

        if mtype == "hb":
            self.node_live[node] = {"rssi": data["rssi"], "faults": data["faults"]}
            new = set(data["faults"]) - self.known_faults.get(node, set())
            for sensor in sorted(new):
                self.event("sensor_fault", node=node, sensor=sensor)
            self.known_faults[node] = set(data["faults"])
        elif mtype == "emergency":
            self.event("pull_cord", node=node, source=data["source"])
        elif mtype == "audio":
            if data["label"] == "choke" and data["conf"] >= AUDIO["choke_conf"]:
                if not self.choke_fired:
                    self.choke_fired = True
                    self.event("choke", node=node, label=data["label"], conf=data["conf"])
                    self.start_move("up", by="gateway")      # Closed loop (M4)
            else:
                self.choke_fired = False

        # history: ทุกข้อความ ยกเว้น fsr เก็บทุก history_fsr_every_s
        if mtype == "fsr":
            if ts - self.last_fsr_history.get(node, 0) < RATES["history_fsr_every_s"] * 1000:
                return
            self.last_fsr_history[node] = ts
        self.history(ts, node, mtype, data)

    def history(self, ts: int, node: str, mtype: str, data: dict):
        assert mtype in HISTORY_TYPES
        day = datetime.fromtimestamp(ts / 1000).strftime("%Y-%m-%d")
        self.sink.push(f"history/{day}", {"ts": ts, "node": node, "type": mtype, "data": data})

    def event(self, etype: str, **fields):
        assert etype in EVENT_TYPES
        assert set(fields) == set(EVENT_DETAIL_FIELDS[etype]), f"detail ของ {etype} ไม่ตรงกับ EVENT_DETAIL_FIELDS"
        detail = {k: fields[k] for k in EVENT_DETAIL_FIELDS[etype]}
        ev = {"type": etype, "severity": SEVERITY[etype], "ts": now_ms(), "detail": detail, "ack": None}
        self.sink.push("events", ev)     # ack = null จน Notifier/Dashboard เขียน {by, ts}
        log(f"EVENT {etype} ({ev['severity']}) {json.dumps(detail, ensure_ascii=False)}")

    # ---------- พนักพิง

    def set_backrest(self, state: str):
        assert state in BACKREST_STATES
        self.backrest = state
        self.sink.set("live/backrest", {"state": state, "updated": now_ms()})
        log(f"backrest -> {state}")

    def start_move(self, target: str, by: str, cmd: tuple | None = None, fail: bool = False) -> bool:
        """เริ่มหมุน (target = up | flat) คืน False ถ้ากำลังหมุนอยู่หรืออยู่ที่ปลายทางแล้ว"""
        if self.motor or self.backrest == target:
            return False
        dur = MOTOR["timeout_s"] if fail else MOVE_S
        self.motor = {"target": target, "finish": time.monotonic() + dur, "fail": fail, "cmd": cmd, "by": by}
        self.set_backrest("moving")
        return True

    def step_motor(self):
        if not self.motor or time.monotonic() < self.motor["finish"]:
            return
        job, self.motor = self.motor, None
        if job["fail"]:  # หมุนเกิน motor_timeout_s แล้วยังไม่ชน limit switch
            self.set_backrest("fault")
            self.event("motor_fault", target=job["target"], timeout_s=MOTOR["timeout_s"])
        else:
            self.set_backrest(job["target"])
        # เขียน done เฉพาะเมื่อ commands/backrest ยังเป็นคำสั่งเดิม (ถ้ามีคำสั่งใหม่มาทับ จะไม่ไปเขียนทับผลของมัน)
        if job["cmd"] and job["cmd"] == self.handled_cmd:
            self.sink.update({"commands/backrest/status": "done"})
            log("commands/backrest status=done")

    def on_command(self, event):
        """callback จาก Firebase listener (รันใน thread แยก)"""
        try:
            cmd = event.data if event.path == "/" else self.sink.get("commands/backrest")
            if not isinstance(cmd, dict) or cmd.get("status") != "pending":
                return
            key = (cmd.get("ts"), cmd.get("action"), cmd.get("by"))
            with self.lock:
                if key == self.handled_cmd:
                    return
                self.handled_cmd = key
                action = cmd.get("action")
                log(f"command backrest action={action} by={cmd.get('by')}")
                target = {"up": "up", "down": "flat"}.get(action)
                if target is None or self.motor:          # action ผิด หรือกำลังหมุนอยู่
                    self.sink.update({"commands/backrest/status": "rejected"})
                    log("commands/backrest status=rejected")
                elif self.backrest == target:             # อยู่ที่ปลายทางแล้ว ไม่ต้องหมุน
                    self.sink.update({"commands/backrest/status": "done"})
                    log("commands/backrest status=done (อยู่ที่ปลายทางแล้ว)")
                else:
                    self.start_move(target, by=cmd.get("by", "dashboard"), cmd=key)
        except Exception as e:
            log(f"command error: {type(e).__name__}: {e}")

    # ---------- โมเดลผู้ป่วย

    def step_fsr(self, dt: float):
        mono = time.monotonic()
        self.lean = max(-0.15, min(0.15, self.lean + random.uniform(-0.01, 0.01)))
        if mono > self.heel_lift_until and random.random() < 0.004:
            self.heel_lift_until = mono + random.uniform(12, 25)   # ขยับเท้า ส้นเท้าลอย
        raised = self.backrest == "up"
        for p in FSR_ORDER:
            base = self.fsr_base[p] * (1 + (self.lean if p.endswith("_l") else -self.lean))
            if p.startswith("heel") and mono < self.heel_lift_until:
                base = 60
            if raised and p.startswith("hip"):
                base *= 1.15          # ยกพนักพิงแล้วน้ำหนักลงสะโพกมากขึ้น
            raw = int(max(0, min(4095, random.gauss(base, 35))))
            self.fsr_raw[p] = raw
            # dwell เพิ่มเมื่อมีแรงกด, รีเซ็ตเมื่อไม่มีแรงกดนาน dwell_reset_s (ใช้ demo time_scale)
            if raw >= PRESS["fsr_on_threshold"]:
                self.dwell_s[p] += dt * self.scale
                self.below_since[p] = None
            else:
                self.below_since[p] = self.below_since[p] or mono
                if mono - self.below_since[p] >= PRESS["dwell_reset_s"]:
                    self.dwell_s[p] = 0.0

    def step_temp(self):
        if self.node2 is None:
            return
        if abs(self.body_target - self.body_c) > 0.2:      # กำลังเป็นไข้หรือไข้ลด
            self.body_c += max(-0.4, min(0.4, self.body_target - self.body_c))
        else:                                               # แกว่งเล็กน้อยรอบค่าปกติ
            self.body_c += random.uniform(-0.05, 0.05) + 0.1 * (self.body_target - self.body_c)

    def step_env(self):
        e = self.env
        e["room_c"] = round(max(26.0, min(31.0, e["room_c"] + random.uniform(-0.1, 0.1))), 1)
        e["humidity"] = round(max(50.0, min(75.0, e["humidity"] + random.uniform(-0.4, 0.4))), 1)
        e["aq"] = int(max(0, min(32767, e["aq"] + random.randint(-80, 80) + (AQ_BASE_RAW - e["aq"]) // 20)))

    # ---------- กฎฝั่ง Gateway ที่ดูตามเวลา

    def check_rules(self):
        ts = now_ms()
        for i, p in enumerate(FSR_ORDER):
            if self.rx_fsr is None:
                break
            dwell_min = self.rx_fsr[1][i] / 60
            if dwell_min < PRESS["dwell_limit_min"]:
                self.pressure_fired.discard(p)
            if dwell_min >= PRESS["dwell_limit_min"] and p not in self.pressure_fired:
                self.pressure_fired.add(p)
                self.event("pressure", node=self.node1.id, point=p, dwell_min=round(dwell_min, 1))
                if PRESS["pressure_action"] == "raise":
                    self.start_move("up", by="gateway")

        if self.rx_body_c is not None and self.rx_body_c >= TEMP["fever_c"]:
            self.fever_since = self.fever_since or time.monotonic()
            if not self.fever_fired and time.monotonic() - self.fever_since >= TEMP["fever_hold_s"]:
                self.fever_fired = True
                self.event("fever", node=self.node2.id, body_c=self.rx_body_c)
        else:
            self.fever_since, self.fever_fired = None, False

        for n in self.nodes:
            seen = self.last_seen.get(n.id)
            if seen and n.id not in self.offline_fired and ts - seen > NET["offline_s"] * 1000:
                self.offline_fired.add(n.id)
                self.event("node_offline", node=n.id, last_seen=seen)

    # ---------- event ที่สั่งจาก --event

    def fire(self, name: str):
        mono = time.monotonic()
        log(f"--event {name}")
        if name == "choke":
            self.audio = {"label": "choke", "conf": round(random.uniform(0.86, 0.97), 2)}
            self.choke_until = mono + 3
        elif name == "pressure":   # ดัน dwell จุดที่สูงสุดให้เกินเกณฑ์ในอีก ~3 วินาที
            p = max(FSR_ORDER, key=lambda k: self.dwell_s[k])
            self.dwell_s[p] = max(self.dwell_s[p], PRESS["dwell_limit_min"] * 60 - 3 * self.scale)
        elif name == "pull_cord":
            if not self.node1.silent():
                self.receive(self.node1.msg("emergency", {"source": "pull_cord"}))
        elif name == "fever":
            self.body_target = 38.3
            self.scheduled.append((mono + TEMP["fever_hold_s"] + 120, "_fever_end"))
            log(f"ไข้ขึ้น: event fever จะเกิดหลังเกิน {TEMP['fever_c']} °C ต่อเนื่อง {TEMP['fever_hold_s']} วินาที")
        elif name == "_fever_end":
            self.body_target = 36.7
        elif name == "node_offline":
            n = self.nodes[-1]
            n.silent_until = mono + NET["offline_s"] + 30
            log(f"{n.id} เงียบ {NET['offline_s'] + 30} วินาที (event หลัง {NET['offline_s']} วินาที)")
        elif name == "motor_fault":
            target = "flat" if self.backrest == "up" else "up"
            if not self.start_move(target, by="gateway", fail=True):
                log("motor_fault: มอเตอร์กำลังหมุนอยู่ ลองใหม่อีกครั้ง")
        elif name == "sensor_fault":
            self.node2.faults = ["mlx90614"]
            self.scheduled.append((mono + 60, "_sensor_ok"))
            log("node2 รายงาน mlx90614 เสียใน hb ถัดไป (ไม่เกิน 10 วินาที) แล้วหายเองใน 60 วินาที")
        elif name == "_sensor_ok":
            self.node2.faults = []

    # ---------- วนทุก 1 วินาที

    def tick(self, k: int, dt: float):
        mono = time.monotonic()
        due = [e for e in self.scheduled if e[0] <= mono]
        self.scheduled = [e for e in self.scheduled if e[0] > mono]
        for _, name in due:
            self.fire(name)

        self.step_fsr(dt)

        n1 = self.node1
        if not n1.silent() and k % RATES["fsr_every_s"] == 0:
            self.receive(n1.msg("fsr", {"raw": [self.fsr_raw[p] for p in FSR_ORDER],
                                        "dwell_s": [int(self.dwell_s[p]) for p in FSR_ORDER]}))

        n2 = self.node2
        if n2 is not None:
            if self.audio["label"] == "choke" and mono > self.choke_until:
                self.audio = {"label": "background", "conf": 0.9}
            changed = False
            if self.audio["label"] != "choke" and random.random() < 0.03:   # คนพูดคุยบ้าง
                label = random.choice(["speech", "background", "background", "cough"])
                changed = label != self.audio["label"]
                self.audio = {"label": label, "conf": round(random.uniform(0.7, 0.98), 2)}
            if self.audio["label"] == "choke":
                changed = True               # ระหว่างสำลักส่งทุกวินาที
            if not n2.silent() and (changed or k % RATES["audio_every_s"] == 0):
                self.receive(n2.msg("audio", dict(self.audio)))
            if k % RATES["temp_every_s"] == 0:
                self.step_temp()
                if not n2.silent():
                    body = None if "mlx90614" in n2.faults else round(self.body_c, 1)
                    self.receive(n2.msg("temp", {"body_c": body}))

        if k % RATES["hb_every_s"] == 0:
            for n in self.nodes:
                if not n.silent():
                    self.receive(n.hb())

        if k % RATES["env_every_s"] == 0:          # ค่าห้องจากเซนเซอร์ของ Gateway เอง
            self.step_env()
            self.history(now_ms(), "gateway", "env", dict(self.env))

        self.check_rules()
        self.write_live()

    def write_live(self):
        ts = now_ms()
        v = {}
        if self.rx_fsr:
            raw, dwell = self.rx_fsr
            for i, p in enumerate(FSR_ORDER):
                v[f"live/fsr/{p}"] = {"raw": raw[i], "dwell_min": round(dwell[i] / 60, 1)}
        v["live/vitals"] = {"body_c": self.rx_body_c, **self.env}   # body_c = null เมื่อ MLX เสีย
        if self.rx_audio:
            v["live/audio"] = self.rx_audio
        for n in self.nodes:
            if n.id in self.last_seen:
                info = self.node_live.get(n.id, {"rssi": n.rssi, "faults": []})
                v[f"live/nodes/{n.id}"] = {"online": n.id not in self.offline_fired,
                                           "last_seen": self.last_seen[n.id], **info}
        v["live/updated"] = ts
        self.sink.update(v)

    def run(self, duration: float | None):
        self.set_backrest("flat")
        self.sink.listen("commands/backrest", self.on_command)
        period = RATES["live_write_min_interval_s"]
        next_t = time.monotonic()
        k = 0
        try:
            while duration is None or time.monotonic() - self.t0 < duration:
                with self.lock:
                    self.step_motor()        # เช็กมอเตอร์ทุก 0.1 s ให้ moving นาน ~1 s จริง
                    if time.monotonic() >= next_t:
                        self.tick(k, period)
                        k += 1
                        next_t += period
                time.sleep(0.1)
        except KeyboardInterrupt:
            log("หยุด")
        finally:
            self.sink.close()


def main():
    ap = argparse.ArgumentParser(description="จำลอง Gateway เขียนข้อมูลลง Firebase ให้ Dashboard/Notifier")
    ap.add_argument("--nodes", type=int, default=2, help="จำนวน node (node1=FSR, node2=เสียง+อุณหภูมิ, node3+=hb อย่างเดียว)")
    ap.add_argument("--event", action="append", choices=EVENT_TYPES, default=[],
                    help="ยิง event (ใส่ซ้ำได้ ห่างกัน 10 วินาที)")
    ap.add_argument("--event-delay", type=float, default=5, help="ยิง event แรกหลังเริ่มกี่วินาที (ค่าเริ่ม 5)")
    ap.add_argument("--time-scale", type=float, default=CFG["demo"]["time_scale"],
                    help="ตัวคูณเวลาของ dwell (เดโมใช้ 60)")
    ap.add_argument("--duration", type=float, help="รันกี่วินาทีแล้วหยุด (ไม่ใส่ = จน Ctrl+C)")
    ap.add_argument("--dry-run", action="store_true", help="ไม่ต่อ Firebase พิมพ์สิ่งที่จะเขียนแทน")
    ap.add_argument("--seed", type=int, help="random seed ให้ผลซ้ำได้")
    args = ap.parse_args()

    if args.nodes < 1:
        ap.error("--nodes ต้อง >= 1")
    need_node2 = {"choke", "fever", "sensor_fault"} & set(args.event)
    if need_node2 and args.nodes < 2:
        ap.error(f"--event {', '.join(sorted(need_node2))} ต้องใช้ --nodes >= 2")
    if args.seed is not None:
        random.seed(args.seed)

    if args.dry_run:
        bed_id = CFG["bed_id"]
        sink = DryRunSink(bed_id)
    else:
        env = load_env()
        bed_id = env.get("BED_ID") or CFG["bed_id"]
        sink = FirebaseSink(bed_id, env)

    s = Seeder(sink, args.nodes, args.time_scale)
    for i, name in enumerate(args.event):
        s.scheduled.append((s.t0 + args.event_delay + 10 * i, name))
    log(f"เริ่ม bed={bed_id} nodes={args.nodes} time_scale={args.time_scale} "
        f"{'dry-run' if args.dry_run else 'firebase'}  (Ctrl+C เพื่อหยุด)")
    s.run(args.duration)


if __name__ == "__main__":
    main()
