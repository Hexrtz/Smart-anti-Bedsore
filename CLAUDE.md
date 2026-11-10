# Smart Anti-Bedsore & Patient Monitoring Bed (CEDT Embedded Final Project 2026)

ไฟล์นี้คือบริบทของโปรเจคสำหรับ Claude Code อ่านก่อนเริ่มทำงานทุกครั้ง
ย้ายมาจากแชทบน claude.ai (ออกแบบ Proposal, BOM, โมเดล Fusion, แบ่งงาน) เมื่อ 9 พ.ย. 2026

## กำหนดส่ง (จากเอกสารโครงงาน)

| วันที่ | สิ่งที่ส่ง |
| --- | --- |
| ศ. 20 พ.ย. 2026 23:59 | **Checkpoint video ≤ 2 นาที**: ข้อมูลไหลจริง sensor node → gateway → cloud → dashboard |
| อ. 8 ธ.ค. 2026 | นำเสนอ 10 นาที + demo สด, ส่ง slides, report ≤ 10 หน้า, video 3–5 นาที, source + Git link, peer eval |
| พ. 9 ธ.ค. 06:00 | หมด grace period (หลังจากนี้ −5 คะแนน/วัน) |

พลาดหรือส่งช้าเกิน 24 ชม. ต่อ milestone = −2 คะแนน

## ทีมและเจ้าของงาน

| คน | บทบาท | เจ้าของโฟลเดอร์ |
| --- | --- | --- |
| Phatcharapon Inpor | System Architect & Integration + Project Manager & Test Lead | `shared/`, `docs/`, กลไกเตียง, สายไฟ |
| Natthasit Wongprang | Embedded Developer (Sensor Node) | `node1/`, `node2/`, `tools/gw_sim.py` |
| Sirapop Sukkhong | Embedded Developer (Gateway & Cloud) | `gateway/`, `tools/node_sim.py` |
| Nantapong Srihattaphadungkit | UI/UX & Dashboard | `dashboard/`, `notifier/`, `tools/firebase_seed.py`, Firebase project |

**กฎสำคัญเมื่อใช้ Claude Code**
- **ห้าม Claude Code รัน `git commit`, `git push` หรือสร้าง Pull Request เด็ดขาด** แก้ไฟล์ได้อย่างเดียว คนในทีมเป็นคน review, commit และ push เองด้วยบัญชีตัวเอง ใช้คำสั่ง git แบบอ่านอย่างเดียวได้ (`git status`, `git diff`, `git log`)
- ทุกคนต้อง commit งานของตัวเองด้วยบัญชีของตัวเอง (M8 และ peer factor ใช้ Git history)
- โค้ดที่ AI ช่วยเขียน เจ้าของต้องอธิบายได้ตอน Q&A ไม่อย่างนั้นอาจได้ 0 ในส่วนนั้น อธิบายโค้ดให้เจ้าของเข้าใจทุกครั้ง
- บันทึกการใช้ AI ลง `docs/ai-usage.md` (เครื่องมือ, ส่วนไหน, ใคร) เพื่อใส่ในรายงานตามข้อ 4.6
- ห้าม commit Wi-Fi password, API key, token, service account (`.env`, `secrets.h`, `*sa*.json` อยู่ใน `.gitignore`)
- ห้ามแก้ `shared/` (contract, config, schema) โดยไม่เพิ่มเลข `v` และให้ Phatcharapon อนุมัติ

## ระบบโดยสรุป

```
Node 1 (ESP32-S3)  FSR402 x6 + สายดึงฉุกเฉิน ──┐
                                              ├─ UDP/Wi-Fi + ACK/Retry ─> Gateway (Raspberry Pi 4) ─ HTTPS/TLS ─> Firebase RTDB ─> Web Dashboard
Node 2 (ESP32-S3)  INMP441 (TinyML) + MLX90614 ┘                              DHT22, MQ-135 (ADS1115)                    └─> Telegram Notifier (บน Pi)
                                                                              TB6612 → JGY-370 → พนักพิง, Limit switch x2
```

- AI: โมเดลเสียงบน Node 2 (E7) จำแนก `cough_choke` / `speech` / `background`; `cough_choke` ≥ 3 ครั้งใน 5 วินาที = `choke` → Gateway ยกพนักพิง (closed loop, M4)
- แจ้งเตือน: Telegram (เปลี่ยนจาก LINE ใน proposal ต้องอธิบายเหตุผลในรายงาน)

## Contract (สรุป ฉบับเต็มอยู่ใน `docs/integration-contract.md`)

**Transport:** UDP → `smartbed-gw.local:5005`, JSON ≤ 512 bytes, Gateway ตอบ ACK ไปที่ IP:port ต้นทาง

**Envelope:** `{"v":1,"node":"node1","seq":1042,"type":"fsr","t":<millis>,"data":{...}}`

| type | จาก | ความถี่ | data |
| --- | --- | --- | --- |
| `fsr` | node1 | 1 s | `{"raw":[6], "dwell_s":[6]}` ลำดับ `[shoulder_l, shoulder_r, hip_l, hip_r, heel_l, heel_r]` |
| `emergency` | node1 | ISR | `{"source":"pull_cord"}` |
| `audio` | node2 | เปลี่ยนค่า + ทุก 5 s | `{"label":"choke|cough|speech|background","conf":0..1}` |
| `temp` | node2 | 5 s | `{"body_c":36.7}` |
| `hb` | ทุก node | 10 s | `{"rssi":-61,"fw":"1.0.0"}` |
| `ack` | gateway | ทันที | `{"type":"ack","node":"node1","seq":1042}` |

**ACK/Retry (E4):** รอ 300 ms, retry สูงสุด 5 ครั้ง (`emergency` ไม่จำกัด), คิว 20 ข้อความทิ้ง `fsr` เก่าก่อน, Gateway กันซ้ำด้วย seq ล่าสุด 50 ค่าต่อ node, ไม่ได้ยินจาก node เกิน 30 s = `node_offline`

**Firebase RTDB `/beds/bed01/`**
- `live/` (Gateway เขียน): `fsr/{point}:{raw,dwell_min}`, `vitals:{body_c,room_c,humidity,aq}`, `audio`, `backrest:{state}`, `nodes/{id}:{online,last_seen}`, `updated`
- `history/{yyyy-mm-dd}/{pushId}: {ts,node,type,data}` (Gateway เขียน, ข้อมูลดิบย้อนหลังสำหรับ M5; `fsr` ทุก 10 s, ค่าห้องจาก Gateway เป็น type `env`)
- เซนเซอร์เสียรายงานใน `hb.data.faults` และ Gateway สร้าง event `sensor_fault`; `hb` มีตัวนับ `sent/acked/dropped` ไว้คำนวณ packet delivery rate
- `events/{pushId}:{type,severity,ts,detail,ack}`; `ack` เขียนโดย Notifier/Dashboard
- `commands/backrest:{action,by,ts,status}` Dashboard เขียน, Gateway ทำตาม
- Enums: backrest `flat|moving|up|fault`; event `choke|pressure|pull_cord|fever|node_offline|motor_fault`; severity `warn|critical`

**Config (`shared/config.yaml`, สร้าง `config.h` ด้วย `tools/gen_config.py`):** `dwell_limit_min: 120`, `fsr_on_threshold: 300` (calibrate), `choke_conf: 0.80`, `fever_c: 37.5`, `motor_timeout_s: 5`, `offline_s: 30`, `demo_time_scale: 1` (เดโมใช้ 60), `pressure_action: notify` (ทีมต้องตัดสิน notify หรือ raise)

## ขาไฟ

- **Node 1:** FSR → GPIO 4,5,6,7,8,9 (3V3–FSR–GPIO–10k–GND), สายดึง GPIO 10 (INPUT_PULLUP, ลง GND)
- **Node 2:** INMP441 SCK 12 / WS 13 / SD 14 (L/R → GND), MLX90614 SDA 8 / SCL 9, addr 0x5A
- **Gateway (BCM):** DHT22 4; ADS1115 SDA 2 / SCL 3 (0x48, MQ-135 → divider 10k/20k → A0); TB6612 PWMA 18, AIN1 23, AIN2 24, STBY 25 (VM จากอะแดปเตอร์ 5–6V แยก, GND ร่วม); Limit ล่าง 5 / บน 6 แบบ **NC** + pull-up (สายหลุด = หยุด)
- ห้ามใช้ GPIO 0, 3, 45, 46 บน ESP32-S3 (strapping)

## ฮาร์ดแวร์และกลไก (ตัดสินแล้ว)

- บอร์ด: ESP32-S3 N8R2 x2 (Node 2 คือตัวรัน AI)
- FSR402 Long Tail x6: ไหล่ ซ้าย/ขวา (บนพนักพิง), สะโพก ซ้าย/ขวา, ส้นเท้า ซ้าย/ขวา + แผ่นรองแข็ง 4x4 ซม.
- มอเตอร์ JGY-370 worm gear **6V 10 rpm** แกน 6 มม. D ต่อตรงที่บานพับ (ล็อกตัวเองเมื่อไฟดับ)
- Limit switch V-152-1C25 x2 ยึดด้วย M3x16; บานพับซ้าย M6x20 + nyloc
- โมเดล Fusion: เตียง 1:6 ยาว 320 x กว้าง 181 (รวมรางมอเตอร์) x สูง 120 มม., บานพับห่างหัวเตียง 110 มม., พนักพิง 0–45° ทดสอบแล้วไม่ชน
- พิมพ์ด้วย Bambu H2S: Frame วางตะแคงเอาด้านซ้ายลง, Tree support on build plate only; Backrest คว่ำหน้า
- ยังต้องวัดของจริง: ตำแหน่ง/ความยาวแกน JGY-370 (≥ 17 มม.), ระยะรูสกรู limit switch (โมเดลใช้ 22.2 มม.)
- หมายเหตุ: Fusion MCP เชื่อมกับ Claude Desktop แชทได้ แต่ log แจ้งว่าเริ่มใน Code session ไม่ได้ งาน CAD ให้ทำต่อใน Claude Desktop

## ตรวจกับเกณฑ์ (Section 4.1 และ 5)

### Minimum requirements (ขาดข้อละ −2)

| # | สถานะ | หลักฐาน |
| --- | --- | --- |
| M1 ≥5 types, ≥2 node, ≥2 gateway | ✅ | FSR, mic, MLX90614 (node) + DHT22, MQ-135 (gateway) = 5 ชนิด; สวิตช์ไม่นับ |
| M2 actuator | ✅ | JGY-370 DC motor |
| M3 node → gateway | ✅ | UDP Wi-Fi |
| M4 AI closed loop | ✅ | TinyML choke → ยกพนักพิง |
| M5 raw data in cloud + dashboard | ✅ ในแผนแล้ว | `history/{date}` เก็บข้อมูลดิบทุกข้อความ + กราฟย้อนหลังบน Dashboard |
| M6 notification | ✅ | Telegram |
| M7 safety | ✅ | limit switch + timeout 5 s, ไม่มีไฟ 220V เปลือย |
| M8 public Git, ทุกคน commit | ✅ ในแผน ต้องทำจริง | ทุกคน commit ด้วยบัญชีตัวเอง |

### Embedded Depth (เลือก 5 = 10 คะแนน)

| Item | ที่ | หมายเหตุ |
| --- | --- | --- |
| E2 Interrupt | Node 1 สายดึง | **ต้องเป็น deferred processing:** ISR ส่ง task notification/semaphore ให้ task ส่งข้อความ ห้ามให้ `loop()` วนเช็ก flag (เกณฑ์ห้าม busy polling) |
| E4 Reliable comm | Node ↔ Gateway + SQLite outbox | ✅ ตามแผน |
| E5 Security | Gateway → Firebase HTTPS + service account, rules | ✅ ห้าม secret ใน repo |
| E7 On-device AI | Node 2 TinyML | ✅ |
| E8 Low-level driver | MLX90614 ระดับ register/SMBus + PEC | ต้องอธิบาย register map, repeated start, CRC-8 ได้ |
| **E1 RTOS (สำรอง)** | Node 2 | **แนะนำเพิ่ม:** task จับเสียง / task inference / task สื่อสาร คุยผ่าน queue ถ้า item ไหนได้แค่ 1 คะแนนจะได้มีตัวชดเชย |

### Scalability (II): 2 physical nodes พร้อมกัน, node ID ทุกข้อความ, Dashboard แสดงแยก node → ได้เต็ม 3

### สิ่งที่แก้แล้วในเอกสาร และต้องทำให้เกิดขึ้นจริง

1. **M5 raw data history (Sirapop, Nantapong):** Gateway เขียน `history/{date}/{pushId}: {ts,node,type,data}` ทุกข้อความ (`fsr` ลดเหลือทุก 10 s) Dashboard มีกราฟย้อนหลัง จำเป็นสำหรับ 24-h field test และวัด uptime ด้วย
2. **เป้าหมายเชิงตัวเลข (Phatcharapon):** ต้องมีเพื่อใช้เทียบใน Analysis
   - Latency choke → Telegram ≤ 3 s, pull cord → Telegram ≤ 2 s
   - Packet delivery node → gateway ≥ 99% ที่ packet loss จำลอง 30%
   - AI recall `cough_choke` ≥ 90%
   - Uptime ≥ 99% ใน 24 ชม.
   - MLX90614 ต่างจากปรอทวัดไข้ ≤ 0.5 °C
3. **Test plan ต้องมี failure cases ครบ (IV):** Wi-Fi หลุด, ตัด internet, **ถอดเซนเซอร์** (FSR/MLX/DHT หลุด → ต้องรายงาน error ไม่ใช่ค่าขยะ), **ไฟดับ** (Pi/Node รีบูตแล้วกลับมาเอง), limit switch หลุด ทุกตัวชี้วัดรายงาน mean + min/max
4. **Problem evidence (I) และ Related work (VI):** สัมภาษณ์ผู้ดูแลจริง (พยาบาล, nursing home, ญาติผู้ป่วยติดเตียง) + สถิติแผลกดทับจากแหล่งอ้างอิง + เทียบกับระบบที่มีขาย (ที่นอนลมสลับแรงดัน, pressure mapping) ด้วยตัวเลขที่วัดได้
5. **Field test ระดับ 3 (III):** ถ้าทำได้ ติดตั้ง Node 2 + Gateway ข้างเตียงผู้สูงอายุจริงหรือที่ศูนย์ดูแล อย่างน้อยให้ได้ระดับ 2 (รันต่อเนื่อง 24 ชม. เก็บข้อมูล)

## Timeline ที่แนะนำ

| ช่วง | งาน |
| --- | --- |
| 9–12 พ.ย. | Repo + `shared/` + simulators, Firebase project, เริ่มอัด dataset เสียงทันที, พิมพ์ชิ้นทดสอบรู D |
| 13–18 พ.ย. | Node 1 FSR จริง → Gateway receiver/ACK → Firebase `live` + `history` → Dashboard พื้นฐาน |
| 19–20 พ.ย. | ถ่าย checkpoint video (ใช้ Node 1 จริง ไม่ใช่ simulator) ส่งก่อน 20 พ.ย. 23:59 |
| 21 พ.ย.–1 ธ.ค. | Node 2 + AI, มอเตอร์ + limit, Telegram, store-and-forward, E-items ทั้งหมด, ประกอบเตียงและ enclosure |
| 2–3 ธ.ค. | รัน 24 ชม. ต่อเนื่อง + failure tests |
| 4–6 ธ.ค. | วัด metrics, report, video, slides |
| 7 ธ.ค. | ซ้อม demo 2 รอบ (เอา hotspot มือถือไปเอง) |

## เอกสารอ้างอิง (claude.ai, ให้เก็บสำเนา Markdown ไว้ใน `docs/`)

- Integration Contract: https://claude.ai/artifact/TB8RJdRL87JXXLrGiXDa4S
- Natthasit: https://claude.ai/artifact/CutMvD5DTYDPW332kMyaBC
- Sirapop: https://claude.ai/artifact/8nda6LEMLqEDcLKd2LFJnt
- Nantapong: https://claude.ai/artifact/97auhoAfpfSnvjgzeFN7gk
- Phatcharapon: https://claude.ai/artifact/CvkV6euzzQMjaQ2gHnueCP
- Proposal: https://claude.ai/artifact/FoHSKqSue1ittBxthZWmQi
- เอกสารโครงงานของวิชา: `docs/Embedded_System_CEDT_Final_Project_2026.pdf`
