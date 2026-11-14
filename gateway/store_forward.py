"""G5: Store-and-forward (E4) คิวขาออกเก็บใน SQLite ก่อนส่งขึ้น Firebase

ทุกการเขียนไป Firebase ต้องผ่านคิวนี้:
  - เขียนลงไฟล์ก่อนเสมอ ถ้าเน็ตหลุดหรือ Pi รีบูต ข้อมูลยังอยู่ในไฟล์
  - worker ส่งตามลำดับ id ส่งไม่ได้ให้รอแบบ backoff 1, 2, 4 ... สูงสุด 30 วินาที แล้วลองใหม่
  - op "update" (ค่า live/*) ต่อ path เก็บแค่ค่าล่าสุด ค่าเก่าที่ยังไม่ได้ส่งไม่มีประโยชน์
  - op "push" (history/*, events/*) เก็บครบทุกรายการ ห้ามทิ้ง
"""
import asyncio
import json
import logging
import sqlite3
import time

log = logging.getLogger("outbox")


class Outbox:
    def __init__(self, path: str, send):
        """send: ฟังก์ชันธรรมดา (ไม่ใช่ async) send(op, path, value) ที่ throw เมื่อส่งไม่สำเร็จ"""
        self.send = send
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.execute("""CREATE TABLE IF NOT EXISTS outbox(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            op TEXT NOT NULL, path TEXT NOT NULL, payload TEXT NOT NULL, created_ms INTEGER NOT NULL)""")
        self.db.commit()
        self.wake = asyncio.Event()
        n = self.pending()
        if n:
            log.info("%d item(s) left from last run, will resend", n)

    def pending(self) -> int:
        return self.db.execute("SELECT COUNT(*) FROM outbox").fetchone()[0]

    def put(self, op: str, path: str, value: dict):
        if op == "update":
            self.db.execute("DELETE FROM outbox WHERE op='update' AND path=?", (path,))
        self.db.execute("INSERT INTO outbox(op, path, payload, created_ms) VALUES (?,?,?,?)",
                        (op, path, json.dumps(value, ensure_ascii=False), int(time.time() * 1000)))
        self.db.commit()
        self.wake.set()

    async def worker(self):
        delay = 1
        while True:
            row = self.db.execute("SELECT id, op, path, payload FROM outbox ORDER BY id LIMIT 1").fetchone()
            if row is None:
                self.wake.clear()
                await self.wake.wait()
                continue
            rid, op, path, payload = row
            try:
                await asyncio.to_thread(self.send, op, path, json.loads(payload))
            except Exception as e:  # noqa: BLE001  เน็ตหลุด, Firebase ล่ม, timeout
                log.warning("send failed (%s), %d queued, retry in %ds", type(e).__name__, self.pending(), delay)
                await asyncio.sleep(delay)
                delay = min(delay * 2, 30)
                continue
            if delay > 1:
                log.info("cloud reachable again, %d left to flush", self.pending() - 1)
            delay = 1
            self.db.execute("DELETE FROM outbox WHERE id=?", (rid,))
            self.db.commit()
