# การใช้เครื่องมือ AI (สำหรับรายงาน ตามข้อ 4.6)

| วันที่ | ผู้ใช้ | เครื่องมือ | ใช้กับส่วนไหน | หมายเหตุ |
| --- | --- | --- | --- | --- |
| 2026-10-30 | Phatcharapon | Claude (claude.ai) | ร่าง Proposal, Block diagram, BOM | |
| 2026-11-08 | Phatcharapon | Claude + Autodesk Fusion MCP | โมเดล 3D โครงเตียงและกลไกบานพับ | |
| 2026-11-09 | Phatcharapon | Claude (claude.ai) | ร่าง Integration Contract และเอกสารแบ่งงาน | ทีมรับรองเป็น v1 |
| 2026-11-10 | Phatcharapon | Claude (claude.ai) | โครง repo, `config.yaml`, `gen_config.py`, JSON Schema, `check_schema.py` | |
| 2026-11-12 | Natthasit | Claude (claude.ai) + Claude Code | เฟิร์มแวร์อัดเสียง `node2/src/record/`, UI `node2/recorder/record_gui.py` | Claude Code ย้าย UI เข้า repo, ปรับที่เซฟไฟล์, เขียน README |
| 2026-11-14 | Natthasit | Claude Code | เฟิร์มแวร์ deploy โมเดลเสียงบน Node 2: `node2/src/cough_tflm_test/` (ตรวจ TFLite Micro + log-mel front end บนบอร์ดด้วย golden test vector, วัดเวลา) และ `node2/src/cough_detector/` (FreeRTOS: I2S -> ring buffer -> log-mel + CNN int8 ทุก 0.5 s -> นับเสียงไอ/สำลัก ส่งออก Serial) | Natthasit กำหนดกฎ: รวมหน้าต่างที่ติดกันเป็นไอ 1 ครั้ง, ไอ >= 3 ครั้งใน 5 s = choke, แจ้ง alive ทุก 5 s ที่ไม่มีเสียงไอ; Claude Code เขียนโค้ด test vector ผ่าน 3/3 บนบอร์ดจริง, cough_detector compile ผ่านแต่ยังไม่ได้ทดสอบบนบอร์ด |
