// คัดลอกเป็น src/cough_detector/secrets.h แล้วใส่ค่าจริง (secrets.h อยู่ใน .gitignore แล้ว ห้าม commit)
// Arduino IDE include ไฟล์นอกโฟลเดอร์ sketch ไม่ได้ จึงต้องวางไว้ข้าง cough_detector.ino
#pragma once
#define WIFI_SSID "your-hotspot"
#define WIFI_PASS "your-password"

// IP ของ Raspberry Pi (ดูด้วยคำสั่ง hostname -I บน Pi)
// ถ้าลบบรรทัดนี้ Node จะหา smartbed-gw.local ผ่าน mDNS แทน (บางเครือข่ายหาไม่เจอ)
#define GATEWAY_IP "172.20.10.3"
