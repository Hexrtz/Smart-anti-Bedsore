"""G6: เขียนข้อมูลขึ้น Firebase Realtime Database ตาม Integration Contract ข้อ 4

path ที่ไฟล์นี้เขียน (ทั้งหมดอยู่ใต้ /beds/<bed_id>/):
  live/fsr/<point>      {raw, dwell_min}       ค่าล่าสุด 6 จุด
  live/nodes/<node>     {online, last_seen}
  live/updated          unix ms
  history/<date>/<id>   {ts, node, type, data} ข้อมูลดิบย้อนหลัง (เกณฑ์ M5)
  events/<id>           {type, severity, ts, detail, ack}

การเชื่อมต่อเป็น HTTPS (TLS) ผ่าน firebase-admin ใช้ Service Account ใน /etc/smartbed/sa.json (E5)
โหมด --dry-run พิมพ์ออกจออย่างเดียว ไม่ต้องมี Firebase ใช้ทดสอบบนโน้ตบุ๊ก

TODO หลัง Checkpoint: ส่งทุกการเขียนผ่าน store_forward.py (คิว SQLite) แทนการเรียก Firebase ตรง
"""
import asyncio
import json
import logging
import time

from settings import BED_ID, FIREBASE_DB_URL, FIREBASE_SA_PATH, FSR_ORDER, RATES

log = logging.getLogger("cloud")


class Cloud:
    def __init__(self, dry_run: bool = False):
        self.dry_run = dry_run
        self.base = f"beds/{BED_ID}"
        self._last_fsr_history_ms: dict[str, int] = {}
        if dry_run:
            log.info("DRY RUN: not connecting to Firebase")
            return
        import firebase_admin
        from firebase_admin import credentials, db
        if not FIREBASE_DB_URL:
            raise SystemExit("FIREBASE_DB_URL is empty: set it in .env")
        firebase_admin.initialize_app(credentials.Certificate(FIREBASE_SA_PATH),
                                      {"databaseURL": FIREBASE_DB_URL})
        self.db = db
        log.info("connected to %s", FIREBASE_DB_URL)

    # ---------- การเขียนพื้นฐาน (รันใน thread แยก ไม่ให้บล็อกการรับ UDP) ----------
    async def _update(self, path: str, values: dict):
        if self.dry_run:
            log.info("UPDATE %s/%s %s", self.base, path, json.dumps(values, ensure_ascii=False))
            return
        await asyncio.to_thread(self.db.reference(f"{self.base}/{path}").update, values)

    async def _push(self, path: str, value: dict):
        if self.dry_run:
            log.info("PUSH   %s/%s %s", self.base, path, json.dumps(value, ensure_ascii=False))
            return
        await asyncio.to_thread(self.db.reference(f"{self.base}/{path}").push, value)

    async def _safe(self, coro):
        """ตอนนี้ถ้าเน็ตหลุด แค่ log ไว้ (จะแทนด้วย Store-and-forward ใน G5)"""
        try:
            await coro
        except Exception as e:  # noqa: BLE001
            log.error("Firebase write failed: %s", e)

    # ---------- handler ตามชนิดข้อความ ----------
    async def on_fsr(self, msg: dict, recv_ms: int):
        raw, dwell = msg["data"]["raw"], msg["data"]["dwell_s"]
        live = {f"fsr/{p}": {"raw": raw[i], "dwell_min": round(dwell[i] / 60, 1)}
                for i, p in enumerate(FSR_ORDER)}
        live["updated"] = recv_ms
        await self._safe(self._update("live", live))

        # history ของ fsr เก็บทุก history_fsr_every_s วินาที ต่อ Node (ข้อความมาทุก 1 วินาที)
        last = self._last_fsr_history_ms.get(msg["node"], 0)
        if recv_ms - last >= RATES["history_fsr_every_s"] * 1000:
            self._last_fsr_history_ms[msg["node"]] = recv_ms
            await self.history(msg, recv_ms)

    async def history(self, msg: dict, recv_ms: int):
        day = time.strftime("%Y-%m-%d", time.localtime(recv_ms / 1000))
        await self._safe(self._push(f"history/{day}",
                                    {"ts": recv_ms, "node": msg["node"], "type": msg["type"], "data": msg["data"]}))

    async def node_status(self, node: str, online: bool, last_seen_ms: int):
        await self._safe(self._update(f"live/nodes/{node}", {"online": online, "last_seen": last_seen_ms}))

    async def event(self, type_: str, severity: str, detail: dict):
        await self._safe(self._push("events", {"type": type_, "severity": severity,
                                               "ts": int(time.time() * 1000), "detail": detail}))
