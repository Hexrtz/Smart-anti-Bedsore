# การใช้เครื่องมือ AI (สำหรับรายงาน ตามข้อ 4.6)

| วันที่ | ผู้ใช้ | เครื่องมือ | ใช้กับส่วนไหน | หมายเหตุ |
| --- | --- | --- | --- | --- |
| 2026-10-30 | Phatcharapon | Claude (claude.ai) | ร่าง Proposal, Block diagram, BOM | |
| 2026-11-08 | Phatcharapon | Claude + Autodesk Fusion MCP | โมเดล 3D โครงเตียงและกลไกบานพับ | |
| 2026-11-09 | Phatcharapon | Claude (claude.ai) | ร่าง Integration Contract และเอกสารแบ่งงาน | ทีมรับรองเป็น v1 |
| 2026-11-10 | Phatcharapon | Claude (claude.ai) | โครง repo, `config.yaml`, `gen_config.py`, JSON Schema, `check_schema.py` | |
| 2026-11-12 | Natthasit | Claude Code | ไพป์ไลน์ข้อมูลเสียง `node2/experiment/`: `src/download_data.py` (โหลด COUGHVID, Speech Commands, ESC-50, Common Voice ไทย, MUSAN, Coswara), `src/check_audio.py` (ตรวจ sample rate/channel), `src/select_data.py` (เลือกไฟล์ให้ cough กับ other เท่ากัน สร้าง `manifest.csv`), `src/to_interim.py` (แปลงเป็น mono 16 kHz), `src/dataset_info.py` (สรุปชั่วโมงต่อ dataset/label/split), `src/make_clips.py` (หาเสียงไอด้วย energy, วางไว้กลาง clip 2 s, ปรับสมดุล, แบ่ง train/val/test ตาม group), `Makefile`, `README.md`; ช่วย debug notebook (path ของไฟล์, `librosa` sample rate, argument ของ `kagglehub`) และแนะนำแหล่งข้อมูล | Natthasit กำหนดลำดับไพป์ไลน์ กฎการเลือกข้อมูล และให้เสียงไออยู่กลาง clip Claude Code เขียนโค้ดและทดสอบรันบน VM แล้ว รอ Natthasit review ก่อน commit |
