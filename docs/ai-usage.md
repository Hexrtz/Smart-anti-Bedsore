# การใช้เครื่องมือ AI (สำหรับรายงาน ตามข้อ 4.6)

| วันที่ | ผู้ใช้ | เครื่องมือ | ใช้กับส่วนไหน | หมายเหตุ |
| --- | --- | --- | --- | --- |
| 2026-10-30 | Phatcharapon | Claude (claude.ai) | ร่าง Proposal, Block diagram, BOM | |
| 2026-11-08 | Phatcharapon | Claude + Autodesk Fusion MCP | โมเดล 3D โครงเตียงและกลไกบานพับ | |
| 2026-11-09 | Phatcharapon | Claude (claude.ai) | ร่าง Integration Contract และเอกสารแบ่งงาน | ทีมรับรองเป็น v1 |
| 2026-11-10 | Phatcharapon | Claude (claude.ai) | โครง repo, `config.yaml`, `gen_config.py`, JSON Schema, `check_schema.py` | |
| 2026-11-13 | Phatcharapon | Claude (claude.ai) | Gateway v1-v2 (`gateway/`), `tools/node_sim.py`, สคริปต์ทดสอบฮาร์ดแวร์บน Pi | ทดสอบบน Pi จริงแล้ว |
| 2026-11-16 | Phatcharapon | Claude (claude.ai) | โค้ด Node1 v1 (`node1/`) FSR + สายดึง (ISR deferred) + ACK/Retry | ยังต้องทดสอบบนบอร์ดจริง |
| 2026-11-16 | Phatcharapon | Claude Code | แฟลช Node1 ลงบอร์ดจริงและต่อเข้า Gateway: แก้บั๊ก UDP socket (อ่าน ACK ก่อน `udp.begin()`), เปิด USB CDC | ทดสอบจริง: ACK ครบ, `live/fsr` ใน Firebase อัปเดตทุก 1 s, กด FSR ครบ 6 จุด |
| 2026-11-16 | Phatcharapon | Claude Code | Gateway: DHT22 อ่านซ้ำก่อนนับว่าพลาด (ลด `sensor_fault` หลอก), รีเซ็ต `live/nodes/*/online` ตอนเริ่ม, ติดตั้ง systemd service | ทดสอบบน Pi: ไม่มี fault หลอกหลัง restart, node2 แสดง offline ถูกต้อง |
| 2026-11-16 | Phatcharapon | Claude Code | ตั้ง `demo.time_scale: 60` สำหรับวิดีโอ Checkpoint | ต้องตั้งกลับเป็น 1 หลังอัด |
