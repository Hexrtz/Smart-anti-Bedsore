"""G6: เขียนข้อมูลขึ้น Firebase Realtime Database ตาม Integration Contract ข้อ 4

path ที่ไฟล์นี้เขียน (ทั้งหมดอยู่ใต้ /beds/<bed_id>/):
  live/fsr/<point>      {raw, dwell_min}       ค่าล่าสุด 6 จุด
  live/vitals           {body_c, room_c, humidity, aq}
  live/audio            {label, conf}
  live/backrest         {state, updated}
  live/nodes/<node>     {online, last_seen}
  live/updated          unix ms
  history/<date>/<id>   {ts, node, type, data} ข้อมูลดิบย้อนหลัง (เกณฑ์ M5)
  events/<id>           {type, severity, ts, detail, ack}
อ่านจาก Firebase:
  commands/backrest     {action, by, ts, status}  คำสั่งยก/ลดจาก Dashboard

การเชื่อมต่อเป็น HTTPS (TLS) ผ่าน firebase-admin ใช้ Service Account ใน /etc/smartbed/sa.json (E5)
ทุกการเขียนผ่าน Outbox (store_forward.py) ก่อน เน็ตหลุดข้อมูลไม่หาย (E4)
โหมด --dry-run พิมพ์ออกจออย่างเดียว ไม่ต้องมี Firebase
"""
import json
import logging
import os
import time

from settings import BED_ID, FIREBASE_DB_URL, FIREBASE_SA_PATH, FSR_ORDER, RATES, ROOT
from store_forward import Outbox

log = logging.getLogger("cloud")


class Cloud:
    def __init__(self, dry_run: bool = False):
        self.dry_run = dry_run
        self.base = f"beds/{BED_ID}"
        self._last_fsr_history_ms: dict[str, int] = {}
        self.db = None
        if dry_run:
            log.info("DRY RUN: not connecting to Firebase")
        else:
            import firebase_admin
            from firebase_admin import credentials, db
            if not FIREBASE_DB_URL:
                raise SystemExit("FIREBASE_DB_URL is empty: set it in .env")
            firebase_admin.initialize_app(credentials.Certificate(FIREBASE_SA_PATH),
                                          {"databaseURL": FIREBASE_DB_URL, "httpTimeout": 10})
            self.db = db
            log.info("connected to %s", FIREBASE_DB_URL)
        db_path = os.environ.get("OUTBOX_PATH", str(ROOT / "outbox.db"))
        self.outbox = Outbox(db_path, self._send)

    # ---------- ส่งจริง (เรียกจาก worker ของ Outbox ใน thread แยก) ----------
    def _send(self, op: str, path: str, value: dict):
        full = f"{self.base}/{path}"
        if self.dry_run:
            log.info("%-6s %s %s", op.upper(), full, json.dumps(value, ensure_ascii=False))
            return
        ref = self.db.reference(full)
        ref.update(value) if op == "update" else ref.push(value)

    def update(self, path: str, values: dict):
        self.outbox.put("update", path, values)

    def push(self, path: str, value: dict):
        self.outbox.put("push", path, value)

    # ---------- handler ตามชนิดข้อมูล ----------
    def on_fsr(self, msg: dict, recv_ms: int):
        raw, dwell = msg["data"]["raw"], msg["data"]["dwell_s"]
        for i, p in enumerate(FSR_ORDER):
            self.update(f"live/fsr/{p}", {"raw": raw[i], "dwell_min": round(dwell[i] / 60, 1)})
        self.update("live", {"updated": recv_ms})
        last = self._last_fsr_history_ms.get(msg["node"], 0)
        if recv_ms - last >= RATES["history_fsr_every_s"] * 1000:
            self._last_fsr_history_ms[msg["node"]] = recv_ms
            self.history(msg["node"], "fsr", msg["data"], recv_ms)

    def history(self, node: str, type_: str, data: dict, recv_ms: int):
        day = time.strftime("%Y-%m-%d", time.localtime(recv_ms / 1000))
        self.push(f"history/{day}", {"ts": recv_ms, "node": node, "type": type_, "data": data})

    def node_status(self, node: str, online: bool, last_seen_ms: int):
        self.update(f"live/nodes/{node}", {"online": online, "last_seen": last_seen_ms})

    def vitals(self, values: dict):
        self.update("live/vitals", values)

    def audio(self, label: str, conf: float):
        self.update("live/audio", {"label": label, "conf": conf})

    def backrest(self, state: str):
        self.update("live/backrest", {"state": state, "updated": int(time.time() * 1000)})

    def event(self, type_: str, severity: str, detail: dict):
        log.warning("EVENT %s (%s) %s", type_, severity, detail)
        self.push("events", {"type": type_, "severity": severity,
                             "ts": int(time.time() * 1000), "detail": detail})

    # ---------- ฟังคำสั่งจาก Dashboard ----------
    def listen_commands(self, callback):
        """callback(cmd: dict) ถูกเรียกใน thread ของ firebase-admin ต้องส่งต่อเข้า event loop เอง"""
        if self.dry_run:
            return None

        def on_change(event):
            cmd = self.db.reference(f"{self.base}/commands/backrest").get()
            if isinstance(cmd, dict) and cmd.get("status") == "pending":
                callback(cmd)
        return self.db.reference(f"{self.base}/commands/backrest").listen(on_change)

    def command_done(self, status: str, reason: str = ""):
        """ตอบผลคำสั่ง ตรงนี้เขียนตรง ไม่ผ่านคิว เพราะเป็นการตอบสดของคำสั่งที่เพิ่งได้รับ"""
        if self.dry_run:
            log.info("command -> %s %s", status, reason)
            return
        self.db.reference(f"{self.base}/commands/backrest").update(
            {"status": status, "reason": reason, "done_ts": int(time.time() * 1000)})
