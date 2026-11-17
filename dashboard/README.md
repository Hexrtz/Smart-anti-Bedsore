# dashboard/

**เจ้าของ:** Nantapong Srihattaphadungkit
**งานทั้งหมด:** https://claude.ai/artifact/97auhoAfpfSnvjgzeFN7gk

Web Dashboard (Firebase JS SDK + Firebase Hosting) อ่านโครงสร้างข้อมูลจาก Contract ข้อ 4

## ไฟล์

| ไฟล์ | หน้าที่ |
| --- | --- |
| `index.html` | โครงหน้า Login + Dashboard |
| `style.css` | หน้าตา (ออกแบบให้พอดีจอ laptop 1366×768 ขึ้นไป) |
| `render.js` | ค่า threshold (คัดลอกจาก `shared/config.yaml`) และโค้ดวาดข้อมูลทั้งหมด |
| `app.js` | Firebase Auth + `onValue()` ของ `beds/bed01/...` แล้วส่งให้ `render.js` |
| `firebase-config.js` | ค่า `firebaseConfig` ของเว็บ (ใส่เองจาก Firebase console) |

## รันในเครื่อง

```bash
cd dashboard && python3 -m http.server 8000
```

เปิด http://localhost:8000 (ต้องเปิดผ่าน http server เพราะใช้ ES modules เปิดไฟล์ตรงๆ ไม่ได้)
