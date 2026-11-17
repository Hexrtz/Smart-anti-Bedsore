// วาดข้อมูลลงหน้าเว็บ (ไม่ยุ่งกับ Firebase) app.js เป็นคนส่งข้อมูลเข้ามา
// ทุกค่าที่มาจากฐานข้อมูลใส่ด้วย textContent เท่านั้น (กัน HTML/สคริปต์แปลกปลอม)

// ============================================================================
// ค่าคงที่ คัดลอกมาจาก shared/config.yaml (ถ้าแก้ config.yaml ต้องแก้ที่นี่ด้วย)
// ============================================================================
export const BED_ID = "bed01";                 // bed_id
export const FSR_ORDER = ["shoulder_l", "shoulder_r", "hip_l", "hip_r", "heel_l", "heel_r"]; // fsr_order (Contract 3.4)
export const DWELL_LIMIT_MIN = 120;            // pressure.dwell_limit_min  แรงกดสะสมเกินนี้ = แดง
export const FSR_ON_THRESHOLD = 300;           // pressure.fsr_on_threshold raw ต่ำกว่านี้ = ไม่มีแรงกด
export const FEVER_C = 37.5;                   // temperature.fever_c       ตั้งแต่ค่านี้ขึ้นไป = ไข้ (Gateway ใช้ >=)

// ค่าของ Dashboard เอง (ไม่อยู่ใน config.yaml)
export const STALE_S = 10;                     // live/updated เก่ากว่านี้ = Gateway ขาดการเชื่อมต่อ
export const EVENTS_LIMIT = 50;                // จำนวน events ล่าสุดที่ดึงมาแสดง

// ============================================================================
// ข้อความภาษาไทย
// ============================================================================
const POINT_TH = {
  shoulder_l: "ไหล่ซ้าย", shoulder_r: "ไหล่ขวา", hip_l: "สะโพกซ้าย",
  hip_r: "สะโพกขวา", heel_l: "ส้นเท้าซ้าย", heel_r: "ส้นเท้าขวา",
};
// ตำแหน่งบนรูปเตียง (มองจากเพดาน ผู้ป่วยนอนหงาย ซ้ายของผู้ป่วยจึงอยู่ด้านขวาของจอ)
const POINT_XY = {
  shoulder_l: [215, 140], shoulder_r: [105, 140], hip_l: [212, 300],
  hip_r: [108, 300], heel_l: [204, 488], heel_r: [116, 488],
};
const BACKREST = {
  flat:   { th: "ราบ (0°)", angle: 0, cls: "st-ok" },
  moving: { th: "กำลังเคลื่อน…", angle: null, cls: "st-moving" },
  up:     { th: "ยกขึ้น (45°)", angle: 45, cls: "st-ok" },
  fault:  { th: "ขัดข้อง: ต้อง reset", angle: null, cls: "st-fault" },
};
const EVENT_TH = {
  choke: "สำลัก", pressure: "แรงกดทับสะสมเกินกำหนด", pull_cord: "ผู้ป่วยดึงสายขอความช่วยเหลือ",
  fever: "มีไข้", node_offline: "Node ขาดการเชื่อมต่อ", motor_fault: "มอเตอร์พนักพิงขัดข้อง",
  sensor_fault: "เซนเซอร์ขัดข้อง",
};
const SENSOR_TH = {
  mlx90614: "เทอร์โมมิเตอร์อินฟราเรด (MLX90614)", mic: "ไมโครโฟน",
  fsr_suspect: "FSR (สงสัยสายหลุด)", dht22: "DHT22 (อุณหภูมิ/ความชื้นห้อง)", mq135: "MQ-135",
};
const MOVE_TH = { up: "ยกขึ้น", down: "ลดลงราบ", flat: "ลดลงราบ", reset: "reset" };
const CAUSE_TH = { choke: "สำลัก", pressure: "แรงกดทับ" };

// ============================================================================
// ตัวช่วย
// ============================================================================
const $ = (id) => document.getElementById(id);
const SVG_NS = "http://www.w3.org/2000/svg";
const isNum = (v) => typeof v === "number" && Number.isFinite(v);
const pad = (n) => String(n).padStart(2, "0");

export function fmtTime(ms) {
  const d = new Date(ms);
  return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
}
function fmtDateTime(ms) {
  const d = new Date(ms);
  return `${pad(d.getDate())}/${pad(d.getMonth() + 1)} ${fmtTime(ms)}`;
}
export function fmtAgo(ms) {
  const s = Math.max(0, Math.round((Date.now() - ms) / 1000));
  if (s < 60) return `${s} วินาทีที่แล้ว`;
  if (s < 3600) return `${Math.floor(s / 60)} นาทีที่แล้ว`;
  return `${Math.floor(s / 3600)} ชม. ที่แล้ว`;
}
// ระยะเวลาเป็นหน่วยที่อ่านง่าย (ตัดเศษลงแบบเดียวกับ fmtAgo)
function fmtDuration(sec) {
  const s = Math.max(0, Math.round(sec));
  if (s < 60) return `${s} วินาที`;
  if (s < 3600) return `${Math.floor(s / 60)} นาที`;
  return `${Math.floor(s / 3600)} ชม.`;
}
function el(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text !== undefined) e.textContent = text;
  return e;
}
function svg(tag, attrs) {
  const e = document.createElementNS(SVG_NS, tag);
  for (const [k, v] of Object.entries(attrs || {})) e.setAttribute(k, v);
  return e;
}

// สีตาม dwell: 0 = เขียว, ครึ่งเกณฑ์ = เหลือง, ถึงเกณฑ์ = แดง
export function dwellColor(dwellMin) {
  const r = Math.min(1, Math.max(0, dwellMin / DWELL_LIMIT_MIN));
  const hue = r < 0.5 ? 130 - (130 - 48) * (r / 0.5) : 48 - 48 * ((r - 0.5) / 0.5);
  return `hsl(${hue.toFixed(0)} 75% 55%)`;
}

// ============================================================================
// โครงหน้าเว็บ
// ============================================================================
export function initStatic() {
  $("bed-label").textContent = `เตียง ${BED_ID}`;
  $("legend-mid").textContent = `${DWELL_LIMIT_MIN / 2}`;
  $("legend-max").textContent = `${DWELL_LIMIT_MIN}+ นาที`;
  $("legend-off-text").textContent = `ไม่มีแรงกด (raw < ${FSR_ON_THRESHOLD})`;

  // สร้างวงกลม 6 จุดครั้งเดียว แล้ว renderFsr แค่อัปเดตค่า
  const g = $("fsr-points");
  g.replaceChildren();
  for (const p of FSR_ORDER) {
    const [x, y] = POINT_XY[p];
    const grp = svg("g", { id: `pt-${p}`, class: "pt pt-nodata" });
    grp.append(
      svg("title"),                       // tooltip ตอนเอาเมาส์ชี้ (มีค่า raw เต็ม)
      svg("circle", { class: "pt-alarm", cx: x, cy: y, r: 34 }),
      svg("circle", { class: "pt-dot", cx: x, cy: y, r: 27 }),
    );
    const num = svg("text", { class: "pt-num", x, y: y + 2, "text-anchor": "middle" });
    const unit = svg("text", { class: "pt-unit", x, y: y + 15, "text-anchor": "middle" });
    unit.textContent = "นาที";
    const name = svg("text", { class: "pt-name", x, y: y + 44, "text-anchor": "middle" });
    name.textContent = POINT_TH[p];
    const sub = svg("text", { class: "pt-sub", x, y: y + 57, "text-anchor": "middle" });
    grp.append(num, unit, name, sub);
    g.append(grp);
  }
}

export function showView(which) {
  $("login-view").hidden = which !== "login";
  $("dash-view").hidden = which !== "dash";
}

export function showBootError(msg) {
  const e = $("boot-error");
  e.textContent = msg;
  e.hidden = false;
}

export function setLoginBusy(busy, errorText = "") {
  $("login-btn").disabled = busy;
  $("login-btn").textContent = busy ? "กำลังเข้าสู่ระบบ…" : "เข้าสู่ระบบ";
  $("login-error").textContent = errorText;
}

export function setUser(email) {
  $("user-email").textContent = email || "";
}

// ============================================================================
// live/fsr
// ============================================================================
export function renderFsr(fsr) {
  for (const p of FSR_ORDER) {
    const v = fsr ? fsr[p] : null;
    const grp = $(`pt-${p}`);
    const [num, unit, name, sub] = grp.querySelectorAll("text");
    const dot = grp.querySelector(".pt-dot");
    if (!v || !isNum(v.dwell_min)) {
      grp.setAttribute("class", "pt pt-nodata");
      dot.style.fill = "";
      num.textContent = "—";
      unit.textContent = "นาที";
      sub.textContent = "ไม่มีข้อมูล";
      grp.querySelector("title").textContent = `${POINT_TH[p]}: ไม่มีข้อมูล`;
      continue;
    }
    const pressed = isNum(v.raw) && v.raw >= FSR_ON_THRESHOLD;
    const over = v.dwell_min >= DWELL_LIMIT_MIN;
    grp.setAttribute("class", `pt ${pressed ? "pt-on" : "pt-off"}${over ? " pt-over" : ""}`);
    dot.style.fill = dwellColor(v.dwell_min);
    num.textContent = v.dwell_min >= 100 ? Math.round(v.dwell_min) : v.dwell_min.toFixed(1).replace(/\.0$/, "");
    unit.textContent = over ? "เกินเกณฑ์" : "นาที";
    sub.textContent = pressed ? `raw ${v.raw}` : "ไม่มีแรงกด";
    grp.querySelector("title").textContent =
      `${POINT_TH[p]}: กดสะสม ${v.dwell_min} นาที, raw ${isNum(v.raw) ? v.raw : "—"} (มีแรงกดเมื่อ ≥ ${FSR_ON_THRESHOLD})`;
  }
}

// ============================================================================
// live/backrest
// ============================================================================
let lastAngle = 0;   // มุมล่าสุดที่รู้แน่ (ใช้ตอน fault จะได้ค้างภาพไว้ที่เดิม)

export function renderBackrest(b) {
  const arm = $("backrest-arm");
  const chip = $("backrest-state");
  const info = b && BACKREST[b.state];
  if (!info) {
    chip.textContent = b && b.state ? `ไม่รู้จักสถานะ: ${b.state}` : "ไม่มีข้อมูล";
    chip.className = "state-chip st-unknown";
    $("backrest-updated").textContent = "";
    $("backrest-fault-icon").hidden = true;
    return;
  }
  if (info.angle !== null) lastAngle = info.angle;
  chip.textContent = info.th;
  chip.className = `state-chip ${info.cls}`;
  arm.classList.toggle("arm-moving", b.state === "moving");
  arm.classList.toggle("arm-fault", b.state === "fault");
  arm.style.transform = b.state === "moving" ? "" : `rotate(${lastAngle}deg)`;
  $("backrest-fault-icon").hidden = b.state !== "fault";
  $("backrest-updated").textContent = isNum(b.updated) ? `เปลี่ยนสถานะ ${fmtTime(b.updated)}` : "";
}

// ============================================================================
// live/vitals  (null หรือไม่มี field = เซนเซอร์ไม่มีค่า แสดง "—")
// ============================================================================
function setVital(id, value, unit, digits, sub = "", warn = false) {
  const box = $(id);
  box.querySelector(".v-value").textContent = isNum(value) ? `${value.toFixed(digits)}${unit}` : "—";
  box.querySelector(".v-sub").textContent = isNum(value) ? sub : "เซนเซอร์ไม่มีค่า";
  box.classList.toggle("vital-alert", isNum(value) && warn);
  box.classList.toggle("vital-null", !isNum(value));
}

export function renderVitals(v) {
  v = v || {};
  const fever = isNum(v.body_c) && v.body_c >= FEVER_C;
  setVital("v-body", v.body_c, " °C", 1, fever ? `มีไข้ (เกณฑ์ ${FEVER_C} °C)` : "ปกติ", fever);
  setVital("v-room", v.room_c, " °C", 1);
  setVital("v-hum", v.humidity, " %", 0);
  // TODO(contract): หน่วยของ aq ยังไม่ตกลง แสดงตัวเลขตามที่ Gateway ส่งมาไปก่อน
  setVital("v-aq", v.aq, "", Number.isInteger(v.aq) ? 0 : 2, "ยังไม่กำหนดหน่วย");
}

// ============================================================================
// live/nodes  วนทุก node ที่มีจริง ไม่ fix ชื่อ
// ============================================================================
let nodesCache = {};

function rssiText(rssi) {
  if (!isNum(rssi)) return "RSSI —";
  const q = rssi >= -60 ? "ดี" : rssi >= -70 ? "พอใช้" : "อ่อน";
  return `RSSI ${rssi} dBm (${q})`;
}

export function renderNodes(nodes) {
  nodesCache = nodes || {};
  const list = $("node-list");
  list.replaceChildren();
  const ids = Object.keys(nodesCache).sort((a, b) => a.localeCompare(b, "en", { numeric: true }));
  $("nodes-count").textContent = ids.length ? `${ids.filter((i) => nodesCache[i]?.online).length}/${ids.length} ออนไลน์` : "";
  if (!ids.length) {
    list.append(el("li", "empty", "ยังไม่มี Node รายงานเข้ามา"));
    return;
  }
  for (const id of ids) {
    const n = nodesCache[id] || {};
    const faults = Array.isArray(n.faults) ? n.faults : [];   // RTDB ไม่เก็บ array ว่าง จึงอาจไม่มี field นี้
    const li = el("li", `node ${n.online ? "node-on" : "node-off"}`);
    li.append(el("span", "node-dot"));
    const main = el("div", "node-main");
    const title = el("div", "node-title");
    title.append(el("strong", "", id), el("span", "node-state", n.online ? "ออนไลน์" : "ออฟไลน์"));
    const meta = el("div", "node-meta");
    meta.append(el("span", "", rssiText(n.rssi)), el("span", "node-seen", ""));
    meta.lastChild.dataset.seen = isNum(n.last_seen) ? n.last_seen : "";
    main.append(title, meta);
    const chips = el("div", "node-faults");
    if (faults.length) faults.forEach((f) => chips.append(el("span", "chip chip-bad", SENSOR_TH[f] || f)));
    else chips.append(el("span", "chip chip-ok", "เซนเซอร์ปกติ"));
    main.append(chips);
    li.append(main);
    list.append(li);
  }
  refreshAgo();
}

// ============================================================================
// events  ใหม่สุดขึ้นก่อน
// ============================================================================
function pointsText(d) {
  const pts = Array.isArray(d.points) ? d.points : d.point ? [d.point] : [];
  return pts.map((p) => POINT_TH[p] || p).join(", ");
}

// รองรับ detail ทั้งแบบ tools/firebase_seed.py (EVENT_DETAIL_FIELDS) และแบบที่ gateway/rules.py เขียนจริง
// field ที่ไม่รู้จักจะแสดงเป็น "key: value" ต่อท้าย จะได้ไม่มีข้อมูลหายไปเงียบๆ
export function eventDetailText(type, d) {
  if (!d || typeof d !== "object") return d ? String(d) : "";
  const parts = [];
  const used = new Set();
  const take = (...keys) => keys.forEach((k) => used.add(k));
  const from = d.node || d.source;
  switch (type) {
    case "choke":
      if (isNum(d.conf)) parts.push(`AI มั่นใจ ${Math.round(d.conf * 100)}%`);
      take("conf", "label");
      break;
    case "pressure": {
      const pts = pointsText(d);
      if (pts) parts.push(pts);
      if (isNum(d.dwell_min)) parts.push(`กดนาน ${Math.round(d.dwell_min)} นาที (เกณฑ์ ${DWELL_LIMIT_MIN})`);
      if (d.action === "raise") parts.push("ยกพนักพิงอัตโนมัติ");
      else if (d.action === "notify") parts.push("ให้ผู้ดูแลพลิกตัวผู้ป่วย");
      take("point", "points", "dwell_min", "action");
      break;
    }
    case "pull_cord":
      take("source");
      break;
    case "fever":
      if (isNum(d.body_c)) parts.push(`${d.body_c.toFixed(1)} °C (เกณฑ์ ${FEVER_C} °C)`);
      take("body_c");
      break;
    case "node_offline":
      if (isNum(d.last_seen)) parts.push(`ได้ยินครั้งสุดท้าย ${fmtTime(d.last_seen)}`);
      take("last_seen");
      break;
    case "motor_fault": {
      const dir = d.action || d.target;
      if (dir) parts.push(`สั่ง${MOVE_TH[dir] || dir}ไม่สำเร็จ`);
      if (isNum(d.timeout_s)) parts.push(`หมุนเกิน ${d.timeout_s} วินาทีแต่ไม่ถึง limit switch`);
      if (typeof d.cause === "string") {
        parts.push(d.cause.startsWith("dashboard:")
          ? `สั่งจาก Dashboard (${d.cause.slice(10)})`
          : `สาเหตุที่สั่ง: ${CAUSE_TH[d.cause] || d.cause}`);
      }
      take("action", "target", "timeout_s", "cause");
      break;
    }
    case "sensor_fault": {
      const s = Array.isArray(d.sensors) ? d.sensors : d.sensor ? [d.sensor] : [];
      if (s.length) parts.push(s.map((x) => SENSOR_TH[x] || x).join(", "));
      take("sensor", "sensors");
      break;
    }
  }
  if (from) parts.push(`จาก ${from}`);
  take("node", "source");
  for (const [k, v] of Object.entries(d)) {
    if (!used.has(k)) parts.push(`${k}: ${typeof v === "object" ? JSON.stringify(v) : v}`);
  }
  return parts.join(" · ");
}

export function renderEvents(events) {
  const list = $("event-list");
  list.replaceChildren();
  const items = Object.entries(events || {})
    .map(([id, e]) => ({ id, ...e }))
    .sort((a, b) => (b.ts || 0) - (a.ts || 0));
  $("events-count").textContent = items.length ? `${items.length} รายการล่าสุด` : "";
  if (!items.length) {
    list.append(el("li", "empty", "ยังไม่มีเหตุการณ์"));
    return;
  }
  for (const e of items) {
    const sev = e.severity === "critical" ? "critical" : e.severity === "warn" ? "warn" : "other";
    const li = el("li", `event ev-${sev}`);
    const head = el("div", "event-head");
    head.append(
      el("span", `sev sev-${sev}`, sev === "critical" ? "วิกฤต" : sev === "warn" ? "เตือน" : e.severity || "?"),
      el("strong", "event-title", EVENT_TH[e.type] || e.type || "ไม่ทราบชนิด"),
    );
    if (e.ack) head.append(el("span", "chip chip-ok", "รับทราบแล้ว"));
    const time = el("span", "event-time", isNum(e.ts) ? fmtDateTime(e.ts) : "");
    head.append(time);
    li.append(head);
    const txt = eventDetailText(e.type, e.detail);
    if (txt) li.append(el("p", "event-detail", txt));
    list.append(li);
  }
}

// ============================================================================
// live/updated + นาฬิกาทุก 1 วินาที
// ============================================================================
let lastUpdated = null;

export function setUpdated(ms) {
  lastUpdated = isNum(ms) ? ms : null;
}

function refreshAgo() {
  document.querySelectorAll(".node-seen").forEach((s) => {
    s.textContent = s.dataset.seen ? `ล่าสุด ${fmtAgo(Number(s.dataset.seen))}` : "";
  });
}

// เรียกจาก setInterval ทุก 1 วินาที: เช็กว่า Gateway ยังเขียน live/updated อยู่ไหม (ไม่ใช้ last_seen ของ node)
export function tick() {
  const banner = $("stale-banner");
  const pill = $("link-pill");
  const ageS = lastUpdated === null ? null : (Date.now() - lastUpdated) / 1000;
  const stale = ageS === null || ageS > STALE_S;
  if (stale) {
    banner.textContent = ageS === null
      ? "⚠ Gateway ขาดการเชื่อมต่อ: ยังไม่เคยได้รับข้อมูล (live/updated)"
      : `⚠ Gateway ขาดการเชื่อมต่อ: ไม่มีข้อมูลใหม่ ${fmtDuration(ageS)} (ล่าสุด ${fmtTime(lastUpdated)}) ค่าที่แสดงอาจไม่ใช่ค่าปัจจุบัน`;
    pill.textContent = "Gateway ขาดการเชื่อมต่อ";
    pill.className = "pill pill-bad";
  } else {
    pill.textContent = `ข้อมูลสด · ${fmtTime(lastUpdated)}`;
    pill.className = "pill pill-ok";
  }
  banner.hidden = !stale;
  document.querySelector(".grid").classList.toggle("is-stale", stale);
  refreshAgo();
}

export function resetAll() {
  setUpdated(null);
  lastAngle = 0;
  renderFsr(null);
  renderBackrest(null);
  renderVitals(null);
  renderNodes(null);
  renderEvents(null);
}
