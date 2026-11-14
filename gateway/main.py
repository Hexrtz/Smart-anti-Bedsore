"""Gateway main: รวม receiver (รับจาก Node) กับ cloud (ส่งขึ้น Firebase)

รัน:
  python gateway/main.py             # ใช้งานจริง เขียน Firebase
  python gateway/main.py --dry-run   # ทดสอบ พิมพ์ออกจออย่างเดียว
  python gateway/main.py -v          # แสดง log ละเอียด (รวมข้อความซ้ำ)
"""
import argparse
import asyncio
import logging

import receiver
from cloud import Cloud


async def run(dry_run: bool):
    cloud = Cloud(dry_run=dry_run)

    async def on_message(msg: dict, recv_ms: int):
        t = msg["type"]
        logging.info("%s seq=%-5d %s", msg["node"], msg["seq"], t)
        if t == "fsr":
            await cloud.on_fsr(msg, recv_ms)
        else:
            # hb, temp, audio, emergency: ตอนนี้เก็บลง history ก่อน
            # rules.py (G4) จะมารับต่อหลัง Checkpoint เพื่อสร้าง event และสั่งมอเตอร์
            await cloud.history(msg, recv_ms)

    async def on_online(node: str, recv_ms: int):
        logging.info("%s online", node)
        await cloud.node_status(node, True, recv_ms)

    async def on_offline(node: str, last_seen_ms: int):
        await cloud.node_status(node, False, last_seen_ms)
        await cloud.event("node_offline", "warn", {"node": node})

    transport, proto = await receiver.start(on_message, on_offline, on_online)
    try:
        await proto.watch_offline()
    finally:
        transport.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="ไม่เขียน Firebase พิมพ์ออกจอแทน")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(name)-8s %(levelname)-7s %(message)s",
                        datefmt="%H:%M:%S")
    try:
        asyncio.run(run(args.dry_run))
    except KeyboardInterrupt:
        logging.info("stopped")


if __name__ == "__main__":
    main()
