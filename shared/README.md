# shared/

**เจ้าของ:** Phatcharapon Inpor (แก้ได้เมื่อทีมตกลงกันแล้วเท่านั้น)

| ไฟล์ | ใช้ทำอะไร |
| --- | --- |
| `config.yaml` | ค่าคงที่ทั้งหมด: threshold, ขาไฟ, พอร์ต, ความถี่ส่ง |
| `config.h` | สร้างอัตโนมัติจาก `config.yaml` ด้วย `python tools/gen_config.py` **ห้ามแก้ด้วยมือ** |
| `schema/*.json` | JSON Schema ของข้อความทุกชนิดระหว่าง Node กับ Gateway |
| `schema/examples/` | ตัวอย่างข้อความที่ถูกต้อง ตรวจด้วย `python tools/check_schema.py` |

ใน PlatformIO ให้เพิ่ม `build_flags = -I../shared` เพื่อ `#include "config.h"`
