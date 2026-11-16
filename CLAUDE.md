# Smart Anti-Bedsore & Patient Monitoring Bed (CEDT Embedded Final Project 2026)

ไฟล์นี้คือบริบทของโปรเจคสำหรับ Claude Code อ่านก่อนเริ่มทำงานทุกครั้ง
ย้ายมาจากแชทบน claude.ai (Proposal, BOM, Fusion, แบ่งงาน, ตั้ง Pi, เขียน Gateway และ Node1) อัปเดตล่าสุด 16 พ.ย. 2026
ผู้ใช้หลักคือ Petch (Phatcharapon, หัวหน้าทีม) คุยภาษาไทย ตอบเป็นภาษาไทย อธิบายแบบเข้าใจง่าย

## กฎสำคัญ (ห้ามละเมิด)

- **ห้าม Claude Code รัน `git commit`, `git push` หรือสร้าง Pull Request เด็ดขาด** แก้ไฟล์ได้อย่างเดียว คนในทีม review, commit, push เองด้วยบัญชีตัวเอง (ไม่อยากให้มีชื่อ Claude ใน GitHub) ใช้ git แบบอ่านอย่างเดียวได้ (`status`, `diff`, `log`) ห้ามใส่บรรทัด Co-Authored-By หรือ attribution ใดๆ (`tools/hooks/commit-msg` บล็อกไว้แล้ว)
- ทุกคนต้อง commit งานตัวเองด้วยบัญชีตัวเอง (M8 และ peer factor ดูจาก Git history)
- โค้ดที่ AI ช่วยเขียน เจ้าของต้องอธิบายได้ตอน Q&A ไม่งั้นอาจได้ 0 ในส่วนนั้น อธิบายโค้ดให้เจ้าของเข้าใจทุกครั้ง
- บันทึกการใช้ AI ลง `docs/ai-usage.md` (ข้อ 4.6 ของรายงาน)
- ห้าม commit Wi-Fi password, API key, token, service account (`.env`, `secrets.h`, `*sa*.json` อยู่ใน `.gitignore`) Service Account อยู่บน Pi ที่ `/etc/smartbed/sa.json` (chmod 600) เท่านั้น ห้ามส่งในแชทกลุ่ม
- แก้ `shared/` (config, schema) ต้องให้ Phatcharapon อนุมัติ ถ้ากระทบ interface ให้เพิ่ม `contract_version` แก้ `config.yaml` แล้วรัน `python tools/gen_config.py` ทุกครั้ง

## กำหนดส่ง

| วันที่ | สิ่งที่ส่ง |
| --- | --- |
| **ศ. 20 พ.ย. 2026 23:59** | **Checkpoint video ≤ 2 นาที**: ข้อมูลไหลจริง **Node1 ตัวจริง** → Gateway → Firebase → Dashboard (เป้าคืออัดวันที่ 19) |
| อ. 8 ธ.ค. 2026 | นำเสนอ 10 นาที + demo สด, slides, report ≤ 10 หน้า, video 3–5 นาที, source + Git link, peer eval |
| พ. 9 ธ.ค. 06:00 | หมด grace period (หลังจากนี้ −5 คะแนน/วัน) |

พลาดหรือส่งช้าเกิน 24 ชม. ต่อ milestone = −2 คะแนน

## ทีม (บทบาทใหม่ที่ตกลงแล้ว 13 พ.ย.)

| คน | GitHub | บทบาท | งาน |
| --- | --- | --- | --- |
| Phatcharapon Inpor (Petch) | @Hexrtz | Architect & Integration **+ Gateway** | `shared/`, `gateway/`, กลไกเตียง, สายไฟ, Pi |
| Natthasit Wongprang | @natthasitwp | Embedded (Sensor Node) + AI | `node1/`, `node2/` (TinyML เสียง, MLX90614 driver) |
| Sirapop Sukkhong | @6833264821 | **Project Manager & Test Lead** | test plan, วัด metrics, failure tests, สคริปต์วิดีโอ, รายงาน |
| Nantapong Srihattaphadungkit | @Overdose7060 | UI/UX, Dashboard, Firebase | `dashboard/`, `notifier/` (Telegram), Firebase project |

**ยังไม่ได้แก้ตามบทบาทใหม่:** README ของแต่ละโฟลเดอร์, CODEOWNERS, เอกสาร Claude Docs (ยังเขียนว่า Sirapop ทำ Gateway และยังใช้ TB6612) Petch บอกว่า "เดี๋ยวค่อยแก้" ให้ถามก่อนแก้

## ระบบโดยสรุป

```
Node1 (ESP32-S3)  FSR402 x6 + สายดึงฉุกเฉิน ──┐
                                              ├─ Wi-Fi (Hotspot "PETCH") UDP:5005 + ACK/Retry ─> Gateway (Raspberry Pi 4) ─ HTTPS/TLS ─> Firebase RTDB ─> Web Dashboard
Node2 (ESP32-S3 N8R2)  INMP441 (TinyML) + MLX90614 ┘                                              DHT22, MQ-135 (ADS1115)                  └─> Telegram bot
                                                                                                  PRIK-KEE-NOO (VNH7040) → JGY-370 → พนักพิง, Limit switch x2
```

- AI: Node2 จำแนกเสียง `choke|cough|speech|background` ส่ง `audio` → Gateway ยกพนักพิงเมื่อ `choke` conf ≥ 0.80 (closed loop, M4)
- `pressure_action: raise` (ตัดสินแล้ว 10 พ.ย.): แรงกดสะสมเกิน 120 นาที → แจ้งเตือน + ยกพนักพิง
- แจ้งเตือนใช้ Telegram (เปลี่ยนจาก LINE ใน proposal เพราะ LINE Notify ปิด ต้องอธิบายในรายงาน)
- ทำไมใช้ทั้ง ESP32-S3 และ Pi: Node ต้องอยู่ที่เตียง ใช้ไฟน้อย อ่าน ADC/I2S ได้ตรง และรัน TinyML ได้ (E7) ส่วน Pi เป็น Gateway ต่อ Cloud ทำ store-and-forward คุมมอเตอร์

## Contract (ฉบับเต็ม: https://claude.ai/artifact/TB8RJdRL87JXXLrGiXDa4S, schema จริงอยู่ใน `shared/schema/`)

Envelope: `{"v":1,"node":"node1","seq":1042,"type":"fsr","t":<millis>,"data":{...}}` ≤ 512 bytes

| type | จาก | ความถี่ | data |
| --- | --- | --- | --- |
| `fsr` | node1 | 1 s | `{"raw":[6 ตัว 0-4095], "dwell_s":[6]}` ลำดับ `fsr_order` |
| `emergency` | node1 | ISR | `{"source":"pull_cord"}` ส่งซ้ำไม่จำกัด |
| `audio` | node2 | เปลี่ยนค่า + ทุก 5 s | `{"label":..., "conf":0..1}` |
| `temp` | node2 | 5 s | `{"body_c":36.7}` |
| `hb` | ทุก node | 10 s | `{"rssi","fw","faults":[mlx90614|mic|fsr_suspect],"sent","acked","dropped"}` |
| `ack` | gateway | ทันที | `{"v":1,"type":"ack","node":"node1","seq":1042}` (JSON แบบไม่มีช่องว่าง) ส่งกลับ IP:port ต้นทาง |

ACK/Retry (E4): รอ 300 ms, retry 5 ครั้ง (emergency ไม่จำกัด), คิว Node 20 ข้อความ, Gateway กันซ้ำ seq 50 ค่าต่อ node, ไม่ได้ยินเกิน 30 s = `node_offline`

`fsr_order: [shoulder_l, shoulder_r, hip_l, hip_r, heel_l, heel_r]`

Firebase RTDB `beds/bed01/`:
- `live/fsr/<point>{raw,dwell_min}`, `live/vitals{body_c,room_c,humidity,aq}`, `live/audio{label,conf}`, `live/backrest{state,updated}`, `live/nodes/<node>{online,last_seen}`, `live/updated`
- `history/<yyyy-mm-dd>/<pushId>{ts,node,type,data}` ข้อมูลดิบ (M5) fsr ลดเหลือทุก 10 s ค่าห้องของ Gateway เป็น type `env`
- `events/<pushId>{type,severity,ts,detail,ack}` type: `choke|pressure|pull_cord|fever|node_offline|motor_fault|sensor_fault` severity: `warn|critical`
- `commands/backrest{action:up|down|reset, by, ts, status:pending|done|rejected, reason, done_ts}` Dashboard เขียน pending, Gateway ทำแล้วตอบ
- backrest state: `flat|moving|up|fault`

Firebase project: `smart-antibedsore` URL `https://smart-antibedsore-default-rtdb.asia-southeast1.firebasedatabase.app`

## ขาไฟ (ใช้จริงแล้ว)

- **Node1:** FSR → GPIO 4,5,6,7,8,9 (3V3–FSR–GPIO–10k–GND) ลำดับตาม fsr_order, สายดึง GPIO10 (INPUT_PULLUP ดึง = LOW)
- **Node2:** INMP441 SCK 12 / WS 13 / SD 14 (L/R → GND), MLX90614 (GY-906) SDA 8 / SCL 9 addr 0x5A
- **Gateway (BCM):** DHT22 GPIO4 (ผ่าน kernel driver `dtoverlay=dht11,gpiopin=4` อ่านจาก sysfs iio), ADS1115 0x48 บน I2C (SDA 2/SCL 3) MQ-135 AO → divider 10k/20k → A0 (ค่าจริง = อ่านได้ × 1.5), motor current sense → A1
- **มอเตอร์:** บอร์ด PRIK-KEE-NOO (VNH7040) PWM 18 / INA 23 / INB 24 ใช้อะแดปเตอร์ **6V แยก** GND ร่วมกับ Pi
- **Limit switch (NC + pull-up, HIGH = ถูกกดหรือสายหลุด):** `limit_lower: 6` (ตัวที่ถูกกดตอนเตียงราบ), `limit_upper: 5` (ตัวที่ถูกกดตอน 45°) สลับในโค้ดตามการติดตั้งจริง ห้ามบอกให้สลับสาย
- ESP32-S3 ห้ามใช้ GPIO 0, 3, 45, 46 (strapping)

## ผลทดสอบฮาร์ดแวร์ (bench test บน Pi, 13–14 พ.ย.)

- MQ-135 AO ≈ 1.09–1.40 V (เป่าแล้วขึ้น), DHT22 อ่านได้ 2–3 จาก 5 ครั้ง (`OSError` เป็นเรื่องปกติของ DHT22 โค้ดใช้ค่าล่าสุดแทน)
- มอเตอร์: `MOTOR_UP_DIR=-1` (อยู่ใน `.env` บน Pi), PWM 50% ความถี่ 1000 Hz ยกใช้ 1.20 s ลดใช้ 1.88 s
- config ที่ใช้จริง (commit e78ae17): `pwm_duty: 0.5`, `pwm_freq_hz: 1000`, `timeout_s: 5`, limit 6/5 ตามข้างบน

## Raspberry Pi (Gateway)

- Raspberry Pi OS (Debian trixie, Python 3.13), hostname `smartbed-gw`, user `rpi` → `ssh rpi@smartbed-gw.local`
- Wi-Fi: Hotspot มือถือ "PETCH" (เปิด Maximize Compatibility) NetworkManager ตั้ง `autoconnect-retries 0`
- เคย EEPROM เสีย แก้ด้วย Imager → Misc utility → Bootloader (Pi 4) SD Card Boot
- repo อยู่ที่ `~/Smart-anti-Bedsore` branch `gateway/receiver-cloud`
- venv ต้องสร้างด้วย `python3 -m venv --system-site-packages .venv` (gpiozero/lgpio มาจาก apt) pip ใช้ `--index-url https://pypi.org/simple` (piwheels SSL error)
- **ต้อง `source .venv/bin/activate` ทุกครั้ง** ไม่งั้นขึ้น `No module named 'firebase_admin'`
- `.env` บน Pi: `FIREBASE_DB_URL=...`, `FIREBASE_SA_PATH=/etc/smartbed/sa.json`, `MOTOR_UP_DIR=-1` (มี `AQ_BASELINE_V` ได้)
- ถ้า `git pull` ติด local change ของ `shared/config.h`: `git fetch && git reset --hard origin/gateway/receiver-cloud` (.env, outbox.db ไม่อยู่ใน git ไม่หาย)
- สั่งเตียงตรงๆ (ต้องปิด Gateway ก่อน เพราะแย่ง GPIO): `cd gateway && python -c "from motor import Motor; m=Motor(print); print(m.go('up'))"`

## สถานะงาน (16 พ.ย.)

**Gateway v2 (`gateway/`) เสร็จและทดสอบบน Pi แล้ว**
- `receiver.py` UDP + validate schema + ACK + dedup + offline watch, `store_forward.py` SQLite outbox (เน็ตหลุดไม่หาย รอด restart), `cloud.py` firebase-admin, `motor.py` (limit, timeout→fault, ramp, หยุดก่อนกลับทิศ), `env_sensors.py`, `rules.py` (choke/pull_cord/pressure/fever/sensor_fault/motor_fault + คำสั่ง Dashboard, dedup 120 s), `main.py` (`--dry-run --no-hw --no-home -v`, ลดเตียงลงราบตอนเริ่ม)
- ทดสอบจริง 14 พ.ย.: `node_sim.py --node node2 --choke 5` → เตียงยกขึ้นจริง, Firebase ได้ event
- ทดสอบ dry-run: loss 20–30% ส่งครบ 100%, outbox รอด restart
- **บั๊กที่แก้แล้วแต่ยังไม่ commit:** `env_sensors.py` แจ้ง `sensor_fault dht22` ทันทีถ้าอ่านครั้งแรกพลาด แก้ให้นับ fault เมื่อ `self.dht.fails >= FAIL_LIMIT`

**Node1 (`node1/`) โค้ด v1 เขียนแล้ว 16 พ.ย. ยังไม่ได้แฟลชลงบอร์ดจริง**
- PlatformIO `esp32-s3-devkitc-1`, `-I../shared` ใช้ `config.h`, `include/secrets.h` มี `WIFI_SSID`, `WIFI_PASS`, `GATEWAY_IP` (ใช้ IP ของ Pi จาก `hostname -I` เพราะ ESP32 มักหา `.local` ไม่เจอ)
- อ่าน FSR ทุก 1 s เฉลี่ย 4 ครั้ง, dwell นับเมื่อ raw > `fsr_on_threshold` × `time_scale`, รีเซ็ตเมื่อปล่อยครบ `dwell_reset_s`
- สายดึง: ISR → `vTaskNotifyGiveFromISR` → `cordTask` (deferred processing ตามเกณฑ์ E2 ห้าม loop วนเช็ก flag) debounce 20 ms, กันซ้ำ 3 s, emergency แซงหน้าคิว
- คิว `std::deque` ล็อกด้วย mutex (ใช้ทั้ง loop และ cordTask), ส่งทีละข้อความรอ ACK, คิวเต็มทิ้งข้อความเก่าที่ไม่ใช่ emergency, Wi-Fi หลุดต่อใหม่ทุก 5 s
- hb ทุก 10 s, `faults` ยังเป็น `[]` เสมอ (ยังไม่ได้ทำตรวจ FSR หลุด)
- ตรวจแล้วด้วย g++ + stub และรันจำลองบน PC คุยกับ Gateway dry-run ที่ loss 30% สองทาง: ไม่มีหาย, ผ่าน schema, pull_cord event ขึ้น ยังไม่เคย build ด้วย toolchain ESP32 จริง
- เจ้าของตาม README คือ Natthasit ต้องตกลงในทีมว่าใคร commit และคนนั้นต้องอธิบายโค้ดได้

**Node2:** Natthasit มี branch `node2/cough-detector`, `node2/cough-deploy` (ยังไม่ได้ดูโค้ด)
**Dashboard / Telegram:** ยังไม่รู้สถานะ (Nantapong)

## งานที่ค้าง เรียงตามความสำคัญ

1. **ต่อ Node1 จริงเข้า Gateway** (แฟลช, calibrate `fsr_on_threshold`, ดู `live/fsr` ใน Firebase ขยับเมื่อกด) ภายใน 17 พ.ย.
2. Dashboard แสดง `live` จาก Firebase (Nantapong) ภายใน 18 พ.ย.
3. ติดตั้ง systemd service `gateway/smartbed-gateway.service` ให้ Gateway รันเองตอนเปิด Pi:
   `sudo cp gateway/smartbed-gateway.service /etc/systemd/system/ && sudo systemctl daemon-reload && sudo systemctl enable --now smartbed-gateway` ดู log: `journalctl -u smartbed-gateway -f`
4. อัด Checkpoint video 19 พ.ย. (ตั้ง `demo.time_scale: 60` ให้เห็น pressure → ยกเตียงใน 2 นาที) ส่งก่อน 20 พ.ย. 23:59 แผนสำรองคือ `node_sim.py` แต่เกณฑ์ต้องการ Node จริง
5. commit fix DHT22, เปิด PR `gateway/receiver-cloud` → main (branch protection: 1 approval, owner bypass ได้)
6. `docs/ai-usage.md`, `docs/wiring.md`
7. ภายหลัง: แก้ README/CODEOWNERS/Claude Docs ตามบทบาทใหม่และ PRIK-KEE-NOO (รอ Petch สั่ง), อธิบายเหตุผลสถาปัตยกรรมให้ Petch (UDP vs MQTT ฯลฯ) ซึ่งเขาขอไว้ว่า "อธิบายภายหลัง"

## ตรวจกับเกณฑ์ (Section 4.1 และ 5)

Minimum (ขาดข้อละ −2): M1 ≥5 ชนิด (FSR, mic, MLX90614 ที่ node + DHT22, MQ-135 ที่ gateway) ✅, M2 มอเตอร์ ✅, M3 node→gateway ✅, M4 AI closed loop ✅, M5 raw data `history/` + กราฟ Dashboard, M6 Telegram, M7 limit + timeout ✅, M8 ทุกคน commit

Embedded Depth (เลือก 5): E2 Interrupt (Node1 สายดึง, deferred) ✅, E4 Reliable comm (ACK/retry + SQLite outbox) ✅, E5 Security (HTTPS + service account + rules), E7 On-device AI (Node2), E8 Low-level driver (MLX90614 SMBus + PEC CRC-8) สำรอง E1 RTOS บน Node2

เป้าหมายตัวเลขสำหรับ Analysis: choke → Telegram ≤ 3 s, pull cord → Telegram ≤ 2 s, delivery ≥ 99% ที่ loss 30%, AI recall choke ≥ 90%, uptime ≥ 99% ใน 24 ชม., MLX90614 ต่างจากปรอท ≤ 0.5 °C

Test plan ต้องมี failure cases: Wi-Fi หลุด, ตัด internet, ถอดเซนเซอร์ (ต้องรายงาน error ไม่ใช่ค่าขยะ), ไฟดับ (รีบูตแล้วกลับมาเอง), limit switch หลุด รายงาน mean + min/max
อื่นๆ: สัมภาษณ์ผู้ดูแลจริง, เทียบระบบที่มีขาย, รัน 24 ชม. ต่อเนื่อง

## ฮาร์ดแวร์และกลไก

- ESP32-S3 N8R2 x2, FSR402 Long Tail x6 + แผ่นรอง, มอเตอร์ JGY-370 worm gear 6V 10 rpm แกน 6 มม. D ต่อตรงที่บานพับ (ล็อกตัวเองเมื่อไฟดับ), Limit switch V-152-1C25 x2
- Fusion design "system bed": เตียง 1:6 (320 × 181 × 120 มม.), Backrest hinge 0–45°, กล่อง Gateway 145 × 95 × 41 มม. (Base/Lid + dummy Pi/Driver/ADS1115) พิมพ์ Bambu H2S โมเดลคนลบออกแล้ว
- Fusion MCP ใช้ได้จาก Claude Desktop (ต้องเปิด Fusion ก่อน)

## เอกสารอ้างอิง (claude.ai)

- Integration Contract: https://claude.ai/artifact/TB8RJdRL87JXXLrGiXDa4S
- งาน Natthasit: https://claude.ai/artifact/CutMvD5DTYDPW332kMyaBC
- งาน Sirapop: https://claude.ai/artifact/8nda6LEMLqEDcLKd2LFJnt
- งาน Nantapong: https://claude.ai/artifact/97auhoAfpfSnvjgzeFN7gk
- งาน Phatcharapon: https://claude.ai/artifact/CvkV6euzzQMjaQ2gHnueCP
- Proposal: https://claude.ai/artifact/FoHSKqSue1ittBxthZWmQi
- Timeline: https://claude.ai/artifact/Jsjgt5xGhkuH4XRXuJQ4rF
- เอกสารวิชา: `docs/Embedded_System_CEDT_Final_Project_2026.pdf`
