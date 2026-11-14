"""G1: รับข้อความจาก Node ผ่าน UDP, ตรวจ Schema, ตอบ ACK, กันข้อความซ้ำ (E4 ฝั่งผู้รับ)

ลำดับต่อหนึ่ง packet:
  1. แปลง JSON  ไม่ได้ -> log แล้วทิ้ง
  2. ตรวจกับ shared/schema  ไม่ผ่าน -> log แล้วทิ้ง (ไม่ตอบ ACK ให้ Node รู้ว่าส่งผิด)
  3. ตอบ ACK ทันที ก่อนทำอย่างอื่น (Node รอ ACK แค่ 300 ms)
  4. ถ้า seq นี้เคยเห็นแล้ว = Node ส่งซ้ำเพราะ ACK ก่อนหน้าหาย -> ไม่ประมวลผลซ้ำ
  5. ส่งต่อให้ handler พร้อมเวลาจริงที่รับ (unix ms)
"""
import asyncio
import json
import logging
import sys
import time
from collections import deque

from settings import NET, ROOT

sys.path.insert(0, str(ROOT / "tools"))
from check_schema import validate  # noqa: E402

log = logging.getLogger("receiver")


class NodeState:
    """สถานะของแต่ละ Node: seq ล่าสุดที่เคยเห็น และเวลาที่ได้ยินล่าสุด"""

    def __init__(self, window: int):
        self.seen = deque(maxlen=window)   # seq ล่าสุด N ค่า (เรียงตามเวลาที่รับ)
        self.seen_set = set()
        self.last_seen_ms = 0
        self.online = False
        self.duplicates = 0
        self.received = 0

    def is_duplicate(self, seq: int) -> bool:
        # Node รีบูตแล้ว seq เริ่มใหม่จาก 0: ถ้า seq ตกลงไปมากกว่าขนาดหน้าต่าง ให้ล้างประวัติ
        if self.seen and seq < min(self.seen) - self.seen.maxlen:
            self.seen.clear()
            self.seen_set.clear()
        if seq in self.seen_set:
            return True
        if len(self.seen) == self.seen.maxlen:
            self.seen_set.discard(self.seen[0])
        self.seen.append(seq)
        self.seen_set.add(seq)
        return False


class Receiver(asyncio.DatagramProtocol):
    def __init__(self, on_message, on_offline, on_online):
        self.on_message = on_message      # async (msg, recv_ms)
        self.on_offline = on_offline      # async (node, last_seen_ms)
        self.on_online = on_online        # async (node, recv_ms)
        self.nodes: dict[str, NodeState] = {}
        self.transport = None

    def connection_made(self, transport):
        self.transport = transport
        log.info("listening on UDP %s", NET["udp_port"])

    def datagram_received(self, data: bytes, addr):
        recv_ms = int(time.time() * 1000)
        try:
            msg = json.loads(data.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            log.warning("drop non-JSON packet from %s", addr)
            return

        errors = validate(msg)
        if errors:
            log.warning("drop invalid %s from %s: %s", msg.get("type"), addr, "; ".join(errors))
            return

        node, seq = msg["node"], msg["seq"]
        ack = {"v": 1, "type": "ack", "node": node, "seq": seq}
        self.transport.sendto(json.dumps(ack, separators=(",", ":")).encode(), addr)

        st = self.nodes.setdefault(node, NodeState(NET["dedup_window"]))
        st.last_seen_ms = recv_ms
        if not st.online:
            st.online = True
            asyncio.ensure_future(self.on_online(node, recv_ms))

        if st.is_duplicate(seq):
            st.duplicates += 1
            log.debug("duplicate %s seq=%s (ACK resent)", node, seq)
            return
        st.received += 1
        asyncio.ensure_future(self.on_message(msg, recv_ms))

    async def watch_offline(self):
        """ทุก 5 วินาที เช็กว่ามี Node ไหนเงียบเกิน offline_s แล้วหรือยัง"""
        limit_ms = NET["offline_s"] * 1000
        while True:
            await asyncio.sleep(5)
            now = int(time.time() * 1000)
            for node, st in self.nodes.items():
                if st.online and now - st.last_seen_ms > limit_ms:
                    st.online = False
                    log.warning("%s offline (silent %.0f s)", node, (now - st.last_seen_ms) / 1000)
                    await self.on_offline(node, st.last_seen_ms)


async def start(on_message, on_offline, on_online):
    loop = asyncio.get_running_loop()
    transport, proto = await loop.create_datagram_endpoint(
        lambda: Receiver(on_message, on_offline, on_online),
        local_addr=("0.0.0.0", NET["udp_port"]),
    )
    return transport, proto
