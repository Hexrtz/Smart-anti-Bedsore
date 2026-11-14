"""G4: ตรรกะ Closed-loop ตัดสินจากข้อความของ Node แล้วสร้าง event และสั่งมอเตอร์

| เงื่อนไข                                         | event (severity)        | เตียง                                 |
| audio label=choke และ conf >= choke_conf          | choke (critical)        | ยกพนักพิงทันที                          |
| emergency จากสายดึง                              | pull_cord (critical)    | ไม่ขยับ                                 |
| dwell จุดใดก็ได้ >= dwell_limit_min                | pressure (warn)         | ยกถ้า pressure_action = raise          |
| body_c >= fever_c ต่อเนื่อง fever_hold_s วินาที     | fever (warn)            | ไม่ขยับ                                 |
| hb มี faults (เซนเซอร์ที่ Node เสีย)                 | sensor_fault (warn)     | ไม่ขยับ                                 |
| มอเตอร์ timeout หรือสวิตช์ผิดปกติ                     | motor_fault (critical)  | หยุด เข้าสถานะ fault                    |
| คำสั่งจาก Dashboard (commands/backrest = pending)  | -                       | ทำตามคำสั่ง ตอบ done / rejected        |

event แต่ละเรื่องสร้างครั้งเดียวต่อ "เหตุการณ์" (เช่น pressure จนกว่าแรงกดจะคลาย)
และชนิดเดียวกันห้ามซ้ำภายใน notify.dedup_window_s วินาที
"""
import asyncio
import logging
import time

from settings import CONFIG, FSR_ORDER

log = logging.getLogger("rules")
P, A, T = CONFIG["pressure"], CONFIG["audio"], CONFIG["temperature"]
DEDUP_S = CONFIG["notify"]["dedup_window_s"]


class Rules:
    def __init__(self, cloud, motor):
        self.cloud, self.motor = cloud, motor
        self.last_event: dict[str, float] = {}
        self.pressure_active = False
        self.fever_since = None
        self.fever_active = False
        self.node_faults: dict[str, set] = {}

    # ---------- helpers ----------
    def event(self, type_, severity, detail, dedup=True):
        now = time.monotonic()
        if dedup and now - self.last_event.get(type_, -1e9) < DEDUP_S:
            log.info("suppress duplicate %s", type_)
            return
        self.last_event[type_] = now
        self.cloud.event(type_, severity, detail)

    async def backrest(self, action: str, why: str) -> tuple[bool, str]:
        log.info("backrest %s (%s)", action, why)
        ok, reason = await asyncio.to_thread(self.motor.go, action)
        if reason == "motor_fault":
            self.event("motor_fault", "critical", {"action": action, "cause": why}, dedup=False)
        return ok, reason

    # ---------- ข้อความจาก Node ----------
    async def on_message(self, msg: dict, recv_ms: int):
        t, d = msg["type"], msg["data"]
        if t == "fsr":
            await self._pressure(d)
        elif t == "audio":
            self.cloud.audio(d["label"], d["conf"])
            if d["label"] == "choke" and d["conf"] >= A["choke_conf"]:
                self.event("choke", "critical", {"conf": d["conf"]}, dedup=False)
                asyncio.ensure_future(self.backrest("up", "choke"))
        elif t == "emergency":
            self.event("pull_cord", "critical", {"node": msg["node"]}, dedup=False)
        elif t == "temp":
            self.cloud.vitals({"body_c": d["body_c"]})
            self._fever(d["body_c"])
        elif t == "hb":
            self._faults(msg["node"], set(d.get("faults", [])))

    async def _pressure(self, d):
        limit_s = P["dwell_limit_min"] * 60
        over = [FSR_ORDER[i] for i, s in enumerate(d["dwell_s"]) if s >= limit_s]
        if over and not self.pressure_active:
            self.pressure_active = True
            self.event("pressure", "warn", {"points": over, "action": P["pressure_action"]}, dedup=False)
            if P["pressure_action"] == "raise":
                asyncio.ensure_future(self.backrest("up", "pressure"))
        elif not over and self.pressure_active:
            self.pressure_active = False
            log.info("pressure relieved")

    def _fever(self, body_c):
        if body_c is None or body_c < T["fever_c"]:
            self.fever_since, self.fever_active = None, False
            return
        self.fever_since = self.fever_since or time.monotonic()
        if not self.fever_active and time.monotonic() - self.fever_since >= T["fever_hold_s"]:
            self.fever_active = True
            self.event("fever", "warn", {"body_c": body_c}, dedup=False)

    def _faults(self, source: str, faults: set):
        new = faults - self.node_faults.get(source, set())
        self.node_faults[source] = faults
        if new:
            self.event("sensor_fault", "warn", {"source": source, "sensors": sorted(new)}, dedup=False)

    # ---------- เซนเซอร์ของ Gateway เอง ----------
    def on_env(self, env: dict):
        self._faults("gateway", set(env.get("faults", [])))

    # ---------- คำสั่งจาก Dashboard ----------
    async def on_command(self, cmd: dict):
        action = cmd.get("action")
        if action not in ("up", "down", "reset"):
            self.cloud.command_done("rejected", f"unknown action {action!r}")
            return
        ok, reason = await self.backrest(action, f"dashboard:{cmd.get('by', '?')}")
        try:
            self.cloud.command_done("done" if ok else "rejected", reason)
        except Exception as e:  # noqa: BLE001
            log.error("cannot report command result: %s", e)
