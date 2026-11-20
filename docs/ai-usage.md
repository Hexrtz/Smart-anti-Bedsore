# การใช้เครื่องมือ AI (สำหรับรายงาน ตามข้อ 4.6)

| วันที่ | ผู้ใช้ | เครื่องมือ | ใช้กับส่วนไหน | หมายเหตุ |
| --- | --- | --- | --- | --- |
| 2026-10-30 | Phatcharapon | Claude (claude.ai) | ร่าง Proposal, Block diagram, BOM | |
| 2026-11-08 | Phatcharapon | Claude + Autodesk Fusion MCP | โมเดล 3D โครงเตียงและกลไกบานพับ | |
| 2026-11-09 | Phatcharapon | Claude (claude.ai) | ร่าง Integration Contract และเอกสารแบ่งงาน | ทีมรับรองเป็น v1 |
| 2026-11-10 | Phatcharapon | Claude (claude.ai) | โครง repo, `config.yaml`, `gen_config.py`, JSON Schema, `check_schema.py` | |
| 2026-11-12 | Natthasit | Claude (claude.ai) + Claude Code | เฟิร์มแวร์อัดเสียง `node2/src/record/`, UI `node2/recorder/record_gui.py` | Claude Code ย้าย UI เข้า repo, ปรับที่เซฟไฟล์, เขียน README |
| 2026-11-14 | Natthasit | Claude Code | เฟิร์มแวร์ deploy โมเดลเสียงบน Node 2: `node2/src/cough_tflm_test/` (ตรวจ TFLite Micro + log-mel front end บนบอร์ดด้วย golden test vector, วัดเวลา) และ `node2/src/cough_detector/` (FreeRTOS: I2S -> ring buffer -> log-mel + CNN int8 ทุก 0.5 s -> นับเสียงไอ/สำลัก ส่งออก Serial) | Natthasit กำหนดกฎ: รวมหน้าต่างที่ติดกันเป็นไอ 1 ครั้ง, ไอ >= 3 ครั้งใน 5 s = choke, แจ้ง alive ทุก 5 s ที่ไม่มีเสียงไอ; Claude Code เขียนโค้ด test vector ผ่าน 3/3 บนบอร์ดจริง, cough_detector compile ผ่านแต่ยังไม่ได้ทดสอบบนบอร์ด |
| 2026-11-16 | Natthasit | Claude Code | `node2/src/cough_detector/mlx90614.h` (MLX90614 driver ระดับ SMBus: Read Word + repeated start + ตรวจ PEC CRC-8) และ `temp_task` ใน `cough_detector.ino` (อ่านทุก 5 s ด้วย vTaskDelayUntil, ล้ม 3 ครั้งติด = fault) | CRC-8 ตรวจกับตัวอย่างใน datasheet แล้ว (B4 07 B5 D2 3A -> 0x30); ยังไม่ได้ทดสอบบนบอร์ด |
| 2026-11-16 | Natthasit | Claude Code | `tools/gw_sim.py` Gateway จำลอง: รับ UDP, ตรวจ Schema, ตอบ ACK, กันซ้ำ 50 seq, จำลอง loss ขาเข้า (`--drop`) และ ACK หาย (`--ack-drop`), สรุป delivery ต่อ node | ทดสอบกับ client จำลองที่ retry 5 ครั้ง: loss 30% + ACK หาย 10% ได้ 300/300 ข้อความถึง gateway |
| 2026-11-20 | Natthasit | Claude Code | `node2/src/cough_detector/net.h` ส่ง `audio`/`temp`/`hb` ให้ Gateway ทาง UDP (คิว + ACK/retry 300 ms x5 แบบเดียวกับ Node 1, `comm_task` แยกเป็น FreeRTOS task priority ต่ำสุด) และต่อเข้ากับ `report_task`/`temp_task` | Natthasit กำหนดให้ใช้ `GATEWAY_IP` 172.20.10.3; ข้อความทุกแบบผ่าน `check_schema.py`, compile ผ่าน (flash 83%), ยังไม่ได้ทดสอบกับ Gateway จริง |
