# Smart Anti-Bedsore & Patient Monitoring Bed

เตียงจำลองอัจฉริยะที่ตรวจจับแรงกดทับสะสม เสียงสำลัก และภาวะไข้ แล้วตอบสนองเองพร้อมแจ้งเตือนผู้ดูแลแบบ Real-time
Embedded System (CEDT) Final Project 2026

## ทีม

| สมาชิก | บทบาท | โฟลเดอร์ |
| --- | --- | --- |
| Phatcharapon Inpor | System Architect & Integration, Project Manager & Test Lead | `shared/`, `docs/` |
| Natthasit Wongprang | Embedded Developer (Sensor Node) | `node1/`, `node2/` |
| Sirapop Sukkhong | Embedded Developer (Gateway & Cloud) | `gateway/` |
| Nantapong Srihattaphadungkit | UI/UX & Dashboard | `dashboard/`, `notifier/` |

## ระบบ

```
Node 1 (ESP32-S3: FSR x6, สายดึงฉุกเฉิน) ─┐
                                         ├─ UDP + ACK/Retry ─> Gateway (Raspberry Pi 4: DHT22, MQ-135, มอเตอร์พนักพิง)
Node 2 (ESP32-S3: Mic + TinyML, MLX90614) ┘                        └─ TLS ─> Firebase ─> Dashboard + Telegram
```

ข้อตกลงระหว่างทุกส่วนอยู่ใน `docs/` (Integration Contract) และ `shared/`

## เริ่มต้นหลัง clone (ทุกคน)

```bash
cp tools/hooks/commit-msg .git/hooks/commit-msg && chmod +x .git/hooks/commit-msg
python3 -m venv .venv && source .venv/bin/activate
pip install -r tools/requirements.txt
python tools/check_schema.py      # ต้องขึ้น PASS ทั้งหมด
```

## กติกา

- ทำงานใน branch ของตัวเอง แล้วเปิด Pull Request เข้า `main`
- ทุกคน commit ด้วยบัญชี GitHub ของตัวเอง
- ห้าม commit secret: ใช้ `.env` และ `include/secrets.h` (อยู่ใน `.gitignore` แล้ว)
- แก้ค่าคงที่ที่ `shared/config.yaml` แล้วรัน `python tools/gen_config.py`
- ใช้ AI ช่วยส่วนไหน บันทึกใน `docs/ai-usage.md`
