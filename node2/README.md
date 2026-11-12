# node2/

**เจ้าของ:** Natthasit Wongprang
**งานทั้งหมด:** https://claude.ai/artifact/CutMvD5DTYDPW332kMyaBC

PlatformIO, board `esp32-s3-devkitc-1` (ตัวที่รันโมเดลเสียง). ค่าคงที่ใช้จาก `../shared/config.h` เท่านั้น. คัดลอก `include/secrets.example.h` เป็น `include/secrets.h`

## อัด dataset เสียง (`src/record/` + `recorder/`)

| ส่วน | ไฟล์ | หน้าที่ |
| --- | --- | --- |
| เฟิร์มแวร์ | `src/record/record.ino` | อ่าน INMP441 ผ่าน I2S ส่งเสียง 16 kHz/16-bit ขึ้น PC ทาง Serial 921600 เมื่อ PC สั่ง |
| UI บน PC | `recorder/record_gui.py` | หาบอร์ดเอง, กดอัด/หยุด, Space มาร์กจังหวะเสียง, หยุดแล้วเลือกว่าเป็นเสียงอะไร, แยกโฟลเดอร์ตามชนิดเสียง, เช็คคุณภาพไฟล์ |

**Arduino IDE:** File → Open → `node2/src/record/record.ino`, Board `ESP32S3 Dev Module`, USB CDC On Boot = **Disabled** → เสียบรู **COM** หรือ **Enabled** → เสียบรู **USB** (ใช้รูไม่ตรงกับค่านี้ UI จะหาบอร์ดไม่เจอ). ห้ามเปิด Serial Monitor ค้างไว้ (แย่งพอร์ตกับ UI)

**ต่อสาย (ตรงกับขาไฟ Node 2 ใน `CLAUDE.md`):** VDD→3V3, GND→GND, L/R→GND, SCK→GPIO12, WS→GPIO13, SD→GPIO14

**รัน UI (ใช้ venv ใน `node2/.venv`):**
```bash
cd node2
python3 -m venv .venv            # ครั้งแรกครั้งเดียว
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r recorder/requirements.txt
python recorder/record_gui.py
```

**ขั้นตอนใช้งาน**
1. เปิดครั้งแรก UI ถามว่าจะเก็บไฟล์ที่ไหน (แนะนำโฟลเดอร์ Google Drive ที่ทีมแชร์กัน) ค่าที่เลือกจำไว้ใน `~/.cough_recorder.json` เปลี่ยนทีหลังที่ปุ่ม "เปลี่ยน..." ถ้ากดยกเลิกจะใช้ `node2/data/recordings/` (อยู่ใน `.gitignore` ไม่ขึ้น git)
2. กรอกชื่อ/สถานที่ (อังกฤษ) → กดเริ่มอัด → กด **Space** ทุกครั้งหลังเกิดเสียง (มาร์กจังหวะไว้ช่วยตัดไฟล์ตอนเทรน)
3. กดหยุด → หน้าต่างถามว่าเป็นเสียงอะไร: **1 ไอ** (target) หรือไม่ใช่ไอ: 2 กระแอม, 3 จาม, 4 หัวเราะ, 5 พูดคุย, 6 เสียงรอบข้าง, 7 อื่นๆ (กดเลขบนคีย์บอร์ดได้) / "ไว้ระบุทีหลัง" / "ทิ้งไฟล์นี้"

**โครงสร้างโฟลเดอร์ที่ได้**
```
<โฟลเดอร์ที่เลือก>/
├── cough/          <ชื่อ>_<สถานที่>_<เวลา>.wav
├── throat_clear/  sneeze/  laugh/  speech/  background/  other/
├── unlabeled/      ไฟล์ที่ยังไม่ได้ระบุ (กดไว้ทีหลัง, ปิดโปรแกรมระหว่างอัด)
└── labels.csv      file, label, name, place, recorded_at, seconds, marks_sec (คั่นด้วย ;), problems
```
ไฟล์เสียงห้ามขึ้น git (ใหญ่และมีเสียงคน) ให้แชร์ผ่าน Google Drive

**Protocol บอร์ด ↔ PC:** PC ส่ง 1 byte: `R` เริ่ม, `S` หยุด, `K` keep-alive (ทุก 1 s, ถ้าเงียบเกิน 3 s บอร์ดหยุดเอง), `P` ping.
บอร์ดส่ง frame: `A5 5A C3 3C | type u8 | seq u32 | count u16 | int16 × count` (little-endian), type `A` = เสียง, `H` = สถานะ (seq = 0 idle / 1 recording / 2 stopped)
