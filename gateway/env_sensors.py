"""G2: เซนเซอร์ที่ต่อกับ Gateway โดยตรง
  DHT22  อ่านผ่านไดรเวอร์ dht11 ของ Linux (sysfs) ต้องมี dtoverlay=dht11,gpiopin=4 ใน /boot/firmware/config.txt
  MQ-135 อ่านผ่าน ADS1115 (I2C 0x48) ช่อง A0 มีวงจรแบ่งแรงดัน 10k / 20k  ค่าจริงที่ขา AO = ค่าที่อ่าน x 1.5

DHT22 อ่านพลาดเป็นครั้งคราวเป็นเรื่องปกติ: ใช้ค่าล่าสุดไปก่อน ถ้าพลาดติดกันเกิน sensor_fail_count ครั้ง ค่อยรายงาน null
MQ-135 แปลงเป็น ppm ได้ไม่แม่น จึงรายงานเป็นแรงดัน AO และดัชนีเทียบกับค่าอากาศสะอาด (aq_baseline_v ใน .env)
"""
import glob
import logging
import os
import time

from settings import CONFIG

log = logging.getLogger("env")
PINS = CONFIG["pins"]["gateway"]
FAIL_LIMIT = CONFIG["sensors"]["sensor_fail_count"]
DIVIDER = 1.5


class DHT22:
    def __init__(self):
        devs = glob.glob("/sys/bus/iio/devices/iio:device*/in_temp_input")
        self.dir = os.path.dirname(devs[0]) if devs else None
        self.last = (None, None)
        self.fails = 0
        if not self.dir:
            log.warning("DHT22 driver not found (check dtoverlay)")

    def read(self):
        """คืน (temp_c, humidity) หรือ (None, None) ถ้าอ่านพลาดติดกันเกินกำหนด"""
        if self.dir:
            try:
                t = int(open(f"{self.dir}/in_temp_input").read()) / 1000
                h = int(open(f"{self.dir}/in_humidityrelative_input").read()) / 1000
                if -20 < t < 80 and 0 <= h <= 100:
                    self.last, self.fails = (round(t, 1), round(h, 1)), 0
                    return self.last
            except OSError:
                pass
        self.fails += 1
        return self.last if self.fails < FAIL_LIMIT else (None, None)


class MQ135:
    def __init__(self):
        self.bus = None
        self.baseline = float(os.environ.get("AQ_BASELINE_V", "0") or 0)
        self.fails = 0
        try:
            from smbus2 import SMBus
            self.bus = SMBus(1)
        except Exception as e:  # noqa: BLE001
            log.warning("I2C not available: %s", e)

    def read_volts(self, channel: int) -> float:
        mux = 0x4 + channel                                  # single-ended AINx
        hi = 0x80 | (mux << 4) | (0x1 << 1) | 0x1            # start, ±4.096 V, single-shot
        self.bus.write_i2c_block_data(PINS["ads1115_addr"], 0x01, [hi, 0x83])
        time.sleep(0.01)
        b = self.bus.read_i2c_block_data(PINS["ads1115_addr"], 0x00, 2)
        raw = (b[0] << 8) | b[1]
        if raw & 0x8000:
            raw -= 1 << 16
        return raw * 4.096 / 32768

    def read(self):
        """คืน (ao_volts, index) โดย index = ao / baseline (1.0 = อากาศสะอาด ยิ่งมากยิ่งแย่)"""
        if not self.bus:
            return None, None
        try:
            v = sum(self.read_volts(PINS["mq135_channel"]) for _ in range(5)) / 5 * DIVIDER
            self.fails = 0
        except OSError:
            self.fails += 1
            return (None, None)
        idx = round(v / self.baseline, 2) if self.baseline > 0 else None
        return round(v, 3), idx


class EnvSensors:
    def __init__(self, enabled: bool = True):
        self.enabled = enabled
        if enabled:
            self.dht, self.mq = DHT22(), MQ135()

    def read(self) -> dict:
        if not self.enabled:
            return {}
        t, h = self.dht.read()
        v, idx = self.mq.read()
        faults = [n for n, bad in (("dht22", t is None), ("mq135", v is None)) if bad]
        return {"room_c": t, "humidity": h, "aq_v": v, "aq": idx, "faults": faults}
