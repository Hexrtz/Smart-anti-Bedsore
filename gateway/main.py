"""Gateway main: รวมทุกส่วนของ Gateway เป็นโปรแกรมเดียว

  receiver.py      รับ UDP จาก Node, ตรวจ Schema, ACK, กันซ้ำ      (G1, E4)
  rules.py         ตรรกะ Closed-loop สร้าง event และสั่งมอเตอร์      (G4)
  motor.py         ยก/ลดพนักพิง พร้อม Limit Switch และ Timeout       (G3, M7)
  env_sensors.py   DHT22 + MQ-135 ของ Gateway                        (G2)
  cloud.py         เขียน Firebase ผ่าน store_forward.py              (G6, G5, E4, E5)

รัน:
  python gateway/main.py                    ใช้งานจริงบน Pi
  python gateway/main.py --dry-run          ไม่เขียน Firebase พิมพ์ออกจอแทน
  python gateway/main.py --dry-run --no-hw  ทดสอบบนโน้ตบุ๊ก (จำลองมอเตอร์ ไม่อ่านเซนเซอร์)
  python gateway/main.py --no-home          ไม่ลดพนักพิงลงราบตอนเริ่มโปรแกรม
"""
import argparse
import asyncio
import logging
import time

import receiver
from cloud import Cloud
from env_sensors import EnvSensors
from motor import Motor
from rules import Rules
from settings import RATES


async def run(args):
    loop = asyncio.get_running_loop()
    cloud = Cloud(dry_run=args.dry_run)
    motor = Motor(on_state=cloud.backrest, hardware=not args.no_hw)
    rules = Rules(cloud, motor)
    env = EnvSensors(enabled=not args.no_hw)

    async def on_message(msg: dict, recv_ms: int):
        logging.debug("%s seq=%-5d %s", msg["node"], msg["seq"], msg["type"])
        if msg["type"] == "fsr":
            cloud.on_fsr(msg, recv_ms)          # fsr มีการลดความถี่ history ในตัว
        else:
            cloud.history(msg["node"], msg["type"], msg["data"], recv_ms)
        await rules.on_message(msg, recv_ms)

    async def on_online(node: str, recv_ms: int):
        logging.info("%s online", node)
        cloud.node_status(node, True, recv_ms)

    async def on_offline(node: str, last_seen_ms: int):
        cloud.node_status(node, False, last_seen_ms)
        rules.event("node_offline", "warn", {"node": node}, dedup=False)

    async def env_loop():
        while True:
            values = await asyncio.to_thread(env.read)
            if values:
                cloud.vitals({k: values[k] for k in ("room_c", "humidity", "aq")})
                cloud.history("gateway", "env", values, int(time.time() * 1000))
                rules.on_env(values)
            await asyncio.sleep(RATES["env_every_s"])

    # คำสั่งจาก Dashboard มาจาก thread ของ firebase-admin ต้องส่งเข้า event loop
    listener = cloud.listen_commands(
        lambda cmd: asyncio.run_coroutine_threadsafe(rules.on_command(cmd), loop))

    if not args.no_home:
        logging.info("homing: lowering backrest to flat")
        asyncio.ensure_future(rules.backrest("down", "startup"))

    transport, proto = await receiver.start(on_message, on_offline, on_online)
    try:
        await asyncio.gather(cloud.outbox.worker(), proto.watch_offline(), env_loop())
    finally:
        motor.stop()
        transport.close()
        if listener:
            listener.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="ไม่เขียน Firebase พิมพ์ออกจอแทน")
    ap.add_argument("--no-hw", action="store_true", help="ไม่ใช้ GPIO/I2C (ทดสอบบนโน้ตบุ๊ก)")
    ap.add_argument("--no-home", action="store_true", help="ไม่ลดพนักพิงตอนเริ่ม")
    ap.add_argument("-v", "--verbose", action="store_true", help="แสดงทุกข้อความที่รับ")
    args = ap.parse_args()
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(name)-8s %(levelname)-7s %(message)s", datefmt="%H:%M:%S")
    for noisy in ("urllib3", "google", "cachecontrol"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    try:
        asyncio.run(run(args))
    except KeyboardInterrupt:
        logging.info("stopped")


if __name__ == "__main__":
    main()
