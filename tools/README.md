# tools/

| ไฟล์ | เจ้าของ | หน้าที่ |
| --- | --- | --- |
| `gen_config.py` | Phatcharapon | สร้าง `shared/config.h` จาก `shared/config.yaml` |
| `check_schema.py` | Phatcharapon | ตรวจข้อความกับ Schema, Gateway ใช้ `validate()` ได้ |
| `gw_sim.py` | Natthasit | จำลอง Gateway ตอบ ACK (มี `--drop`) |
| `node_sim.py` | Sirapop | จำลอง Node ส่งข้อมูล (มี `--scenario`, `--loss`) |
| `firebase_seed.py` | Nantapong | ใส่ข้อมูลปลอมลง Firebase ให้ Dashboard |
| `hooks/commit-msg` | Phatcharapon | กัน commit ที่มี attribution ของ Claude |

ติดตั้ง: `pip install -r tools/requirements.txt`
