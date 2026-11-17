// ต่อ Firebase: Login (Email/Password) แล้วฟัง beds/bed01/ ด้วย onValue() ส่งข้อมูลให้ render.js วาด
// ไม่ล็อกอิน = ไม่เปิด listener เลย (และ Database Rules ต้องบังคับ auth != null ด้วย หน้าเว็บซ่อนอย่างเดียวไม่พอ)
import { initializeApp } from "https://www.gstatic.com/firebasejs/10.12.2/firebase-app.js";
import { getAuth, onAuthStateChanged, signInWithEmailAndPassword, signOut }
  from "https://www.gstatic.com/firebasejs/10.12.2/firebase-auth.js";
import { getDatabase, ref, onValue, query, orderByChild, limitToLast }
  from "https://www.gstatic.com/firebasejs/10.12.2/firebase-database.js";
import { firebaseConfig } from "./firebase-config.js";
import {
  BED_ID, EVENTS_LIMIT, initStatic, showView, showBootError, setLoginBusy, setUser,
  renderFsr, renderBackrest, renderVitals, renderNodes, renderEvents, setUpdated, tick, resetAll,
} from "./render.js";

const AUTH_ERROR_TH = {
  "auth/invalid-credential": "อีเมลหรือรหัสผ่านไม่ถูกต้อง",
  "auth/invalid-email": "รูปแบบอีเมลไม่ถูกต้อง",
  "auth/user-disabled": "บัญชีนี้ถูกปิดใช้งาน",
  "auth/too-many-requests": "ลองผิดหลายครั้งเกินไป รอสักครู่แล้วลองใหม่",
  "auth/network-request-failed": "เชื่อมต่ออินเทอร์เน็ตไม่ได้",
};

initStatic();

if (Object.values(firebaseConfig).some((v) => String(v).includes("REPLACE_ME"))) {
  showBootError("ยังไม่ได้ใส่ firebaseConfig ใน dashboard/firebase-config.js");
  throw new Error("firebaseConfig ยังเป็น placeholder");
}

const app = initializeApp(firebaseConfig);
const auth = getAuth(app);
const db = getDatabase(app);
const base = `beds/${BED_ID}`;

let unsubs = [];        // ตัวยกเลิก onValue ทั้งหมด (ใช้ตอน logout)
let clock = null;       // setInterval เช็ก live/updated ทุก 1 วินาที

function listen(path, cb) {
  unsubs.push(onValue(ref(db, `${base}/${path}`), (snap) => cb(snap.val()),
    (err) => console.error(`อ่าน ${path} ไม่ได้:`, err.code || err)));
}

function startDashboard() {
  listen("live/fsr", renderFsr);
  listen("live/vitals", renderVitals);
  listen("live/backrest", renderBackrest);
  listen("live/nodes", renderNodes);
  listen("live/updated", (ms) => { setUpdated(ms); tick(); });
  // ดึงเฉพาะ EVENTS_LIMIT รายการล่าสุดตาม ts (ควรมี ".indexOn": ["ts"] ใน Rules ของ events)
  const q = query(ref(db, `${base}/events`), orderByChild("ts"), limitToLast(EVENTS_LIMIT));
  unsubs.push(onValue(q, (snap) => renderEvents(snap.val()),
    (err) => console.error("อ่าน events ไม่ได้:", err.code || err)));
  tick();
  clock = setInterval(tick, 1000);
}

function stopDashboard() {
  unsubs.forEach((u) => u());
  unsubs = [];
  clearInterval(clock);
  clock = null;
  resetAll();
}

onAuthStateChanged(auth, (user) => {
  if (user) {
    setUser(user.email);
    showView("dash");
    if (!unsubs.length) startDashboard();
  } else {
    stopDashboard();
    setUser("");
    showView("login");
  }
});

document.getElementById("login-form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  setLoginBusy(true);
  try {
    await signInWithEmailAndPassword(auth,
      document.getElementById("email").value.trim(), document.getElementById("password").value);
    document.getElementById("password").value = "";
    setLoginBusy(false);
  } catch (err) {
    setLoginBusy(false, AUTH_ERROR_TH[err.code] || `เข้าสู่ระบบไม่สำเร็จ (${err.code || err.message})`);
  }
});

document.getElementById("logout-btn").addEventListener("click", () => signOut(auth));
