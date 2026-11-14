"""G3: ควบคุมพนักพิงเตียง  JGY-370 (worm gear) ผ่านบอร์ด PRIK-KEE-NOO (VNH7040)

ขา (BCM จาก shared/config.yaml): PWM 18, INA 23, INB 24
Limit Switch ล่าง 5 / บน 6 ต่อแบบ NC + pull-up: HIGH = ถูกกด หรือ สายหลุด -> หยุด (ปลอดภัยไว้ก่อน)

สถานะ: flat, moving, up, fault
ความปลอดภัย (เกณฑ์ M7):
  - หยุดทันทีเมื่อชน Limit Switch ของทิศที่กำลังไป
  - หมุนเกิน timeout_s ยังไม่ชนสวิตช์ -> หยุดและเข้าสถานะ fault (ไม่ยอมขยับอีกจนกว่าจะ reset)
  - สวิตช์ทั้งสองตัวทำงานพร้อมกัน = สายหลุดหรือผิดปกติ -> fault
  - หยุดก่อนกลับทิศทุกครั้ง (คำเตือนในคู่มือ PRIK-KEE-NOO)
  - ทิศยกขึ้นกำหนดด้วย MOTOR_UP_DIR ใน .env (+1 หรือ -1) ได้จากการทดสอบ bedtest.py detect
"""
import logging
import os
import threading
import time

from settings import CONFIG

log = logging.getLogger("motor")
PINS = CONFIG["pins"]["gateway"]
M = CONFIG["motor"]
UP_DIR = int(os.environ.get("MOTOR_UP_DIR", "1"))


class FakeIO:
    """ใช้ตอนทดสอบบนเครื่องที่ไม่มี GPIO (--no-hw) จำลองว่าชนสวิตช์หลังหมุน 1 วินาที"""
    def __init__(self):
        self.pos, self.dir = 0.0, 0                 # 0 = ราบ, 1 = ยกสุด

    def hit_lower(self): return self.pos <= 0
    def hit_upper(self): return self.pos >= 1
    def drive(self, d, duty):
        self.dir = d if duty > 0 else 0
    def tick(self, dt):
        self.pos = min(1, max(0, self.pos + self.dir * dt))


class Motor:
    def __init__(self, on_state, hardware: bool = True):
        self.on_state = on_state            # callback(state) เรียกทุกครั้งที่สถานะเปลี่ยน
        self.lock = threading.Lock()
        self.state = "flat"
        self.fake = None
        if hardware:
            from gpiozero import DigitalOutputDevice, InputDevice, PWMOutputDevice
            self.pwm = PWMOutputDevice(PINS["motor_pwm"], frequency=M["pwm_freq_hz"], initial_value=0)
            self.ina = DigitalOutputDevice(PINS["motor_ina"])
            self.inb = DigitalOutputDevice(PINS["motor_inb"])
            self.lower = InputDevice(PINS["limit_lower"], pull_up=True)
            self.upper = InputDevice(PINS["limit_upper"], pull_up=True)
        else:
            self.fake = FakeIO()

    # ---------- ระดับฮาร์ดแวร์ ----------
    def _hit_lower(self):
        return self.fake.hit_lower() if self.fake else not self.lower.is_active

    def _hit_upper(self):
        return self.fake.hit_upper() if self.fake else not self.upper.is_active

    def _drive(self, d: int, duty: float):
        """d: +1 = ยก, -1 = ลด, 0 = หยุด"""
        if self.fake:
            self.fake.drive(d, duty)
            return
        if d == 0 or duty == 0:
            self.pwm.value = 0
            self.ina.off(); self.inb.off()
            return
        if d * UP_DIR > 0:
            self.ina.on(); self.inb.off()
        else:
            self.ina.off(); self.inb.on()
        self.pwm.value = duty

    def stop(self):
        self._drive(0, 0)

    def _set(self, state):
        if state != self.state:
            self.state = state
            log.info("backrest -> %s", state)
            self.on_state(state)

    # ---------- การเคลื่อนที่ (blocking เรียกผ่าน asyncio.to_thread) ----------
    def _move(self, d: int) -> str:
        target, name = (self._hit_upper, "up") if d > 0 else (self._hit_lower, "flat")
        if target():
            return name
        self.stop(); time.sleep(M["reverse_pause_s"])
        self._set("moving")
        t0 = last = time.monotonic()
        try:
            while True:
                now = time.monotonic()
                if self.fake:
                    self.fake.tick(now - last)
                last = now
                el = now - t0
                self._drive(d, M["pwm_duty"] * min(1.0, el / M["ramp_s"]) if M["ramp_s"] else M["pwm_duty"])
                if self._hit_lower() and self._hit_upper():
                    self.stop(); log.error("both limit switches active (wire off?)")
                    return "fault"
                if target():
                    self.stop(); log.info("reached %s in %.2f s", name, el)
                    return name
                if el > M["timeout_s"]:
                    self.stop(); log.error("timeout %.1f s without limit switch", M["timeout_s"])
                    return "fault"
                time.sleep(0.005)
        finally:
            self.stop()

    def go(self, action: str) -> tuple[bool, str]:
        """action: up / down / home  คืน (สำเร็จไหม, เหตุผล)"""
        if not self.lock.acquire(blocking=False):
            return False, "busy"
        try:
            if self.state == "fault" and action != "reset":
                return False, "fault"
            if action == "reset":
                self._set("flat" if self._hit_lower() else "moving")
                action = "home"
            d = 1 if action == "up" else -1
            result = self._move(d)
            self._set(result)
            return result != "fault", "" if result != "fault" else "motor_fault"
        finally:
            self.lock.release()
