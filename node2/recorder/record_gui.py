#!/usr/bin/env python3
"""
Cough Recorder GUI for the ESP32-S3 + INMP441 board (node2/src/record/record.ino).

Setup (once):   pip install -r requirements.txt
Run:            python record_gui.py

First launch asks where to keep the recordings (remembered in ~/.cough_recorder.json,
change it later with the "เปลี่ยน..." button). The window finds the board by itself.
Colours match the board LED:  blue = ready, yellow = recording, red = just stopped.

While recording, press Space (or the mark button) right after each sound.
After stopping, pick what the recording is (cough, throat clear, ..., background).
Layout of the save folder:
    <folder>/cough/<name>_<place>_<time>.wav
    <folder>/other_label/...
    <folder>/unlabeled/...      recordings nobody labelled yet (window closed, crash)
    <folder>/labels.csv         one row per recording: file, label, name, place, marks...
"""
import array
import csv
import datetime as dt
import json
import math
import os
import re
import struct
import subprocess
import sys
import threading
import time
import wave

try:
    import serial
    from serial.tools import list_ports
except ImportError:
    sys.exit("pyserial is not installed. Run:  pip install pyserial")

MAGIC = bytes([0xA5, 0x5A, 0xC3, 0x3C])
HEADER = struct.Struct("<BIH")           # type, seq, count
T_AUDIO, T_STATUS = ord("A"), ord("H")
ST_IDLE, ST_REC, ST_STOPPED = 0, 1, 2
OFFLINE = "offline"
SAMPLE_RATE = 16000
BLOCK = 512
MAX_COUNT = 4096
BAUD = 921600
# default save folder: node2/data/recordings (resolved from this file, so the working directory doesn't matter)
DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "recordings")
SETTINGS_PATH = os.path.join(os.path.expanduser("~"), ".cough_recorder.json")

COLORS = {OFFLINE: "#888780", ST_IDLE: "#378ADD", ST_REC: "#EF9F27", ST_STOPPED: "#E24B4A"}
STATE_TEXT = {OFFLINE: "ไม่พบบอร์ด", ST_IDLE: "พร้อมอัด", ST_REC: "กำลังอัด...", ST_STOPPED: "หยุดอัดแล้ว"}
# (Thai text, folder name). "cough" is the target class, the rest are negatives for training.
LABELS = [("ไอ", "cough"), ("กระแอม", "throat_clear"), ("จาม", "sneeze"), ("หัวเราะ", "laugh"),
          ("พูดคุย", "speech"), ("เสียงรอบข้าง", "background"), ("อื่นๆ", "other")]
UNLABELED = "unlabeled"
LABEL_TEXT = {label: text for text, label in LABELS}
LABEL_TEXT[UNLABELED] = "ยังไม่ระบุ"
INDEX_FIELDS = ["file", "label", "name", "place", "recorded_at", "seconds", "marks_sec", "problems"]


def safe(text):
    return re.sub(r"[^A-Za-z0-9_-]+", "-", text.strip()).strip("-")


def load_settings():
    try:
        with open(SETTINGS_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_settings(settings):
    try:
        with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
            json.dump(settings, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def candidate_ports():
    skip = ("bluetooth", "debug-console")
    keys = ("usbmodem", "wchusbserial", "usbserial", "ch34", "ch910", "cp210", "uart", "usb")
    out = []
    for p in list_ports.comports():
        text = f"{p.device} {p.description or ''} {p.hwid or ''}".lower()
        if any(s in text for s in skip):
            continue
        if any(k in text for k in keys):
            out.append(p.device)
    return out


class FrameReader:
    def __init__(self, ser, timeout=2.0):
        self.ser = ser
        self.timeout = timeout
        self.buf = bytearray()

    def _fill(self, n):
        deadline = time.time() + self.timeout
        while len(self.buf) < n:
            chunk = self.ser.read(max(n - len(self.buf), self.ser.in_waiting))
            if chunk:
                self.buf += chunk
                deadline = time.time() + self.timeout
            elif time.time() > deadline:
                raise TimeoutError

    def next_frame(self):
        head = len(MAGIC) + HEADER.size
        while True:
            self._fill(head)
            i = self.buf.find(MAGIC)
            if i < 0:
                del self.buf[:-(len(MAGIC) - 1)]
                continue
            if i > 0:
                del self.buf[:i]
                self._fill(head)
            kind, seq, count = HEADER.unpack_from(self.buf, len(MAGIC))
            if kind == T_STATUS and count == 0:
                del self.buf[:head]
                return kind, seq, b""
            if kind != T_AUDIO or count == 0 or count > MAX_COUNT:
                del self.buf[:1]
                continue
            end = head + count * 2
            self._fill(end)
            pcm = bytes(self.buf[head:end])
            del self.buf[:end]
            return kind, seq, pcm


class Recording:
    def __init__(self, path, name, place):
        self.path = path
        self.name = name
        self.place = place
        self.started = dt.datetime.now()
        self.wav = wave.open(path, "wb")
        self.wav.setnchannels(1)
        self.wav.setsampwidth(2)
        self.wav.setframerate(SAMPLE_RATE)
        self.samples = 0
        self.lost = 0
        self.last_seq = None
        self.peak = 0
        self.clipped = 0
        self.marks = []          # seconds into the recording where a sound happened

    def add(self, seq, pcm):
        if self.last_seq is not None:
            gap = (seq - self.last_seq - 1) & 0xFFFFFFFF
            if 0 < gap < 1000:
                self.lost += gap
                self.wav.writeframes(bytes(gap * BLOCK * 2))
                self.samples += gap * BLOCK
        self.last_seq = seq
        self.wav.writeframes(pcm)
        a = array.array("h")
        a.frombytes(pcm)
        self.samples += len(a)
        self.peak = max(self.peak, max(a), -min(a))
        self.clipped += sum(1 for x in a if x >= 32767 or x <= -32768)

    @property
    def seconds(self):
        return self.samples / SAMPLE_RATE

    def close(self):
        self.wav.close()


class Board(threading.Thread):
    """Background thread: finds the board, reads frames, feeds the active recording."""

    def __init__(self):
        super().__init__(daemon=True)
        self.lock = threading.Lock()
        self.ser = None
        self.reader = None
        self.port = None
        self.state = OFFLINE
        self.level_db = -90.0
        self.rec = None

    def send(self, cmd):
        try:
            if self.ser:
                self.ser.write(cmd)
        except Exception:
            pass

    def _connect(self):
        for dev in candidate_ports():
            ser = serial.Serial()
            try:
                ser.port, ser.baudrate, ser.timeout = dev, BAUD, 0.2
                ser.dtr = False
                ser.rts = False
                ser.open()
                ser.reset_input_buffer()
                reader = FrameReader(ser, timeout=1.5)
                ser.write(b"P")
                kind, seq, _ = reader.next_frame()
                if kind == T_AUDIO:          # board left recording by an old session
                    ser.write(b"S")
                self.ser, self.reader, self.port = ser, reader, dev
                self.state = seq if kind == T_STATUS else ST_REC
                return
            except Exception:
                try:
                    ser.close()
                except Exception:
                    pass

    def _drop(self):
        try:
            self.ser.close()
        except Exception:
            pass
        self.ser = self.reader = self.port = None
        self.state = OFFLINE

    def run(self):
        while True:
            if self.ser is None:
                self._connect()
                if self.ser is None:
                    time.sleep(1.5)
                    continue
            try:
                kind, seq, pcm = self.reader.next_frame()
            except Exception:
                self._drop()
                continue

            if kind == T_STATUS:
                self.state = seq
                continue
            self.state = ST_REC
            a = array.array("h")
            a.frombytes(pcm)
            rms = math.sqrt(sum(x * x for x in a) / len(a)) if a else 0
            self.level_db = 20 * math.log10(rms / 32768) if rms > 0 else -90.0
            with self.lock:
                if self.rec is not None:
                    self.rec.add(seq, pcm)


class Player:
    def __init__(self):
        self.proc = None

    def play(self, path):
        self.stop()
        if sys.platform.startswith("win"):
            import winsound
            winsound.PlaySound(path, winsound.SND_FILENAME | winsound.SND_ASYNC)
        elif sys.platform == "darwin":
            self.proc = subprocess.Popen(["afplay", path])
        else:
            self.proc = subprocess.Popen(["aplay", "-q", path])

    def stop(self):
        if sys.platform.startswith("win"):
            import winsound
            winsound.PlaySound(None, winsound.SND_PURGE)
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
        self.proc = None


def open_folder(path):
    if sys.platform.startswith("win"):
        os.startfile(path)
    elif sys.platform == "darwin":
        subprocess.Popen(["open", path])
    else:
        subprocess.Popen(["xdg-open", path])


def check_quality(rec):
    problems = []
    if rec.seconds < 1:
        problems.append("สั้นเกินไป (ไม่ถึง 1 วินาที)")
    if rec.lost:
        problems.append(f"ข้อมูลหายระหว่างส่ง {rec.lost * BLOCK * 1000 // SAMPLE_RATE} ms")
    if rec.peak < 100:
        problems.append("เสียงเบามาก ไมค์อาจหลุด ลองเช็คสาย")
    if rec.samples and rec.clipped / rec.samples > 0.001:
        problems.append("เสียงดังจนแตก วางไมค์ให้ห่างขึ้น")
    return problems


def file_label(root_dir, rec, label, problems):
    """Move the WAV into <root>/<label>/ and add a row to <root>/labels.csv."""
    folder = os.path.join(root_dir, label)
    os.makedirs(folder, exist_ok=True)
    dst = os.path.join(folder, os.path.basename(rec.path))
    if os.path.abspath(dst) != os.path.abspath(rec.path):
        os.replace(rec.path, dst)
        rec.path = dst
    index = os.path.join(root_dir, "labels.csv")
    new = not os.path.exists(index)
    # utf-8-sig on a new file so Excel shows the Thai text correctly
    with open(index, "a", newline="", encoding="utf-8-sig" if new else "utf-8") as f:
        w = csv.writer(f)
        if new:
            w.writerow(INDEX_FIELDS)
        w.writerow([os.path.relpath(rec.path, root_dir).replace(os.sep, "/"), label, rec.name, rec.place,
                    rec.started.strftime("%Y-%m-%d %H:%M:%S"), f"{rec.seconds:.2f}",
                    ";".join(f"{t:.2f}" for t in rec.marks), " | ".join(problems)])


def count_files(root_dir):
    counts = {}
    for _, label in LABELS + [("", UNLABELED)]:
        folder = os.path.join(root_dir, label)
        if os.path.isdir(folder):
            n = sum(1 for f in os.listdir(folder) if f.lower().endswith(".wav"))
            if n:
                counts[label] = n
    return counts


def run_gui():
    import tkinter as tk
    from tkinter import ttk, messagebox, filedialog

    board = Board()
    board.start()
    player = Player()
    settings = load_settings()

    root = tk.Tk()
    root.title("Cough Recorder")
    root.minsize(660, 600)
    pad = {"padx": 10, "pady": 6}
    style = ttk.Style()
    style.configure("Big.TButton", font=("", 15, "bold"), padding=(18, 10))

    ui = {"rec": None, "stopping": False, "deadline": 0.0, "last_path": None,
          "keepalive": 0.0, "note": "", "root_dir": settings.get("root_dir") or DATA_DIR}

    # --- name / place / save folder ------------------------------------------
    top = ttk.Frame(root)
    top.pack(fill="x", **pad)
    ttk.Label(top, text="ชื่อ (อังกฤษ)").grid(row=0, column=0, sticky="w")
    name_var = tk.StringVar(value=settings.get("name", ""))
    ttk.Entry(top, textvariable=name_var, width=18).grid(row=0, column=1, padx=6)
    ttk.Label(top, text="สถานที่").grid(row=0, column=2, sticky="w")
    place_var = tk.StringVar(value=settings.get("place", ""))
    ttk.Entry(top, textvariable=place_var, width=18).grid(row=0, column=3, padx=6)

    ttk.Label(top, text="ที่เก็บไฟล์").grid(row=1, column=0, sticky="w", pady=(6, 0))
    dir_var = tk.StringVar(value=ui["root_dir"])
    ttk.Label(top, textvariable=dir_var, foreground="#185FA5").grid(row=1, column=1, columnspan=3, sticky="w", padx=6, pady=(6, 0))
    dir_btn = ttk.Button(top, text="เปลี่ยน...", takefocus=False)
    dir_btn.grid(row=1, column=4, pady=(6, 0))
    port_var = tk.StringVar(value="")
    ttk.Label(top, textvariable=port_var, foreground="#5F5E5A").grid(row=2, column=0, columnspan=5, sticky="w", pady=(6, 0))

    # --- status light -------------------------------------------------------
    light = tk.Canvas(root, width=150, height=150, highlightthickness=0)
    light.pack(pady=4)
    dot = light.create_oval(15, 15, 135, 135, fill=COLORS[OFFLINE], outline="")
    state_var = tk.StringVar(value=STATE_TEXT[OFFLINE])
    ttk.Label(root, textvariable=state_var, font=("", 16)).pack()
    time_var = tk.StringVar(value="00:00")
    ttk.Label(root, textvariable=time_var, font=("", 14)).pack()

    meter = tk.Canvas(root, width=400, height=14, highlightthickness=1, highlightbackground="#B4B2A9")
    meter.pack(pady=4)
    bar = meter.create_rectangle(0, 0, 0, 14, fill="#1D9E75", outline="")

    # --- buttons ------------------------------------------------------------
    # takefocus=False everywhere: otherwise Space would "press" the focused button
    # (e.g. stop the recording) instead of adding a mark
    btns = ttk.Frame(root)
    btns.pack(**pad)
    rec_btn = ttk.Button(btns, text="●  เริ่มอัดเสียง", width=18, takefocus=False)
    rec_btn.grid(row=0, column=0, padx=4)
    play_btn = ttk.Button(btns, text="▶  ฟังไฟล์ล่าสุด", width=16, state="disabled", takefocus=False)
    play_btn.grid(row=0, column=1, padx=4)
    stopplay_btn = ttk.Button(btns, text="■  หยุดเล่น", width=10, command=player.stop, takefocus=False)
    stopplay_btn.grid(row=0, column=2, padx=4)
    folder_btn = ttk.Button(btns, text="เปิดโฟลเดอร์", width=12, takefocus=False)
    folder_btn.grid(row=0, column=3, padx=4)

    marks_frame = ttk.LabelFrame(root, text="ระหว่างอัด: กด Space ทันทีหลังเกิดเสียง (ช่วยหาจังหวะตอนตัดไฟล์)")
    marks_frame.pack(fill="x", **pad)
    mark_btn = ttk.Button(marks_frame, text="มาร์กจังหวะเสียง (Space)", state="disabled", takefocus=False,
                          command=lambda: add_mark())
    mark_btn.grid(row=0, column=0, padx=4, pady=6)
    marks_var = tk.StringVar(value="")
    ttk.Label(marks_frame, textvariable=marks_var).grid(row=0, column=1, sticky="w", padx=8)

    wave_canvas = tk.Canvas(root, height=110, highlightthickness=1, highlightbackground="#B4B2A9", bg="white")
    wave_canvas.pack(fill="x", **pad)
    result_var = tk.StringVar(value="")
    result_lbl = ttk.Label(root, textvariable=result_var, wraplength=620, justify="left")
    result_lbl.pack(fill="x", **pad)
    stats_var = tk.StringVar(value="")
    ttk.Label(root, textvariable=stats_var, foreground="#5F5E5A").pack(fill="x", padx=10, pady=(0, 8))

    # --- save folder --------------------------------------------------------
    def refresh_stats():
        counts = count_files(ui["root_dir"])
        if not counts:
            stats_var.set("ในโฟลเดอร์นี้ยังไม่มีไฟล์")
            return
        parts = [f"{LABEL_TEXT[l]} {n}" for l, n in counts.items()]
        stats_var.set(f"ไฟล์ในโฟลเดอร์ (รวมทุกคน): " + " · ".join(parts))

    def set_root_dir(path):
        try:
            os.makedirs(path, exist_ok=True)
        except OSError as e:
            messagebox.showerror("ใช้โฟลเดอร์นี้ไม่ได้", str(e))
            return False
        ui["root_dir"] = path
        dir_var.set(path)
        settings["root_dir"] = path
        save_settings(settings)
        refresh_stats()
        return True

    def pick_root_dir(first=False):
        if ui["rec"] is not None:
            return
        start_dir = ui["root_dir"] if os.path.isdir(ui["root_dir"]) else os.path.dirname(DATA_DIR)
        path = filedialog.askdirectory(parent=root, initialdir=start_dir, mustexist=False,
                                       title="เลือกโฟลเดอร์สำหรับเก็บไฟล์เสียง")
        if path:
            set_root_dir(path)
        elif first:
            set_root_dir(ui["root_dir"])   # cancelled on first run: keep the default

    def first_run():
        messagebox.showinfo(
            "เลือกที่เก็บไฟล์",
            "ครั้งแรก เลือกโฟลเดอร์ที่จะเก็บไฟล์เสียงก่อน\n\n"
            "แนะนำให้ทั้งทีมใช้โฟลเดอร์เดียวกัน เช่น โฟลเดอร์ Google Drive ที่แชร์กัน\n"
            "ไฟล์จะถูกแยกเป็นโฟลเดอร์ย่อยตามชนิดเสียง (cough, speech, background, ...)\n\n"
            f"ถ้ากดยกเลิก จะใช้ {DATA_DIR}\n"
            "เปลี่ยนทีหลังได้ที่ปุ่ม \"เปลี่ยน...\"",
            parent=root)
        pick_root_dir(first=True)

    # --- recording ----------------------------------------------------------
    def start():
        if board.state == OFFLINE:
            messagebox.showwarning("ไม่พบบอร์ด", "เสียบบอร์ดแล้วรอให้ไฟเป็นสีน้ำเงิน")
            return
        name, place = safe(name_var.get()), safe(place_var.get())
        if not name or not place:
            messagebox.showwarning("ข้อมูลไม่ครบ", "กรอกชื่อและสถานที่เป็นภาษาอังกฤษก่อน")
            return
        settings["name"], settings["place"] = name_var.get().strip(), place_var.get().strip()
        save_settings(settings)
        folder = os.path.join(ui["root_dir"], UNLABELED)   # moved to its label folder after stop
        try:
            os.makedirs(folder, exist_ok=True)
        except OSError as e:
            messagebox.showerror("เขียนไฟล์ไม่ได้", f"{e}\nกด \"เปลี่ยน...\" เพื่อเลือกโฟลเดอร์ใหม่")
            return
        player.stop()
        stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
        rec = Recording(os.path.join(folder, f"{name}_{place}_{stamp}.wav"), name, place)
        with board.lock:
            board.rec = rec
        ui["rec"] = rec
        board.send(b"R")
        ui["keepalive"] = time.time()
        root.focus_set()                    # take focus out of the text boxes so Space marks
        rec_btn.config(text="■  หยุดอัด")
        mark_btn.config(state="normal")
        play_btn.config(state="disabled")
        dir_btn.config(state="disabled")
        marks_var.set("")
        result_var.set("")
        wave_canvas.delete("all")

    def stop():
        if ui["rec"] is None or ui["stopping"]:
            return
        board.send(b"S")
        ui["stopping"] = True
        ui["deadline"] = time.time() + 1.5
        rec_btn.config(state="disabled")

    def finish():
        with board.lock:
            rec = board.rec
            board.rec = None
        ui["rec"] = None
        ui["stopping"] = False
        rec.close()
        rec_btn.config(text="●  เริ่มอัดเสียง", state="normal")
        mark_btn.config(state="disabled")
        dir_btn.config(state="normal")
        draw_wave(rec)
        problems = check_quality(rec)
        if ui["note"]:
            problems.insert(0, ui["note"])
            ui["note"] = ""
        ask_label(rec, problems)

    def toggle():
        if ui["rec"] is None:
            start()
        else:
            stop()

    def add_mark():
        rec = ui["rec"]
        if rec is None or ui["stopping"]:
            return
        t = rec.seconds
        rec.marks.append(t)
        marks_var.set(f"ล่าสุดที่ {t:.1f} s  (รวม {len(rec.marks)} จุด)")

    # --- label dialog after stop --------------------------------------------
    def ask_label(rec, problems):
        win = tk.Toplevel(root)
        win.title("ไฟล์นี้เป็นเสียงอะไร")
        win.transient(root)
        win.resizable(False, False)

        ttk.Label(win, text="ไฟล์ที่เพิ่งอัดเป็นเสียงอะไร", font=("", 15, "bold")).pack(padx=16, pady=(14, 2))
        ttk.Label(win, text=f"{os.path.basename(rec.path)}  ·  {rec.seconds:.1f} s  ·  มาร์ก {len(rec.marks)} จุด",
                  foreground="#5F5E5A").pack(padx=16)
        if problems:
            ttk.Label(win, text="⚠ " + "\n⚠ ".join(problems), foreground="#A32D2D",
                      justify="left").pack(padx=16, pady=(6, 0))

        ttk.Button(win, text="▶  ฟังก่อน", takefocus=False,
                   command=lambda: player.play(rec.path)).pack(pady=8)

        def choose(label):
            player.stop()
            win.grab_release()
            win.destroy()
            try:
                file_label(ui["root_dir"], rec, label, problems)
            except OSError as e:
                messagebox.showerror("บันทึกไม่สำเร็จ", f"{e}\nไฟล์ยังอยู่ที่ {rec.path}")
            show_result(rec, label, problems)

        def discard():
            if not messagebox.askyesno("ทิ้งไฟล์นี้", "ลบไฟล์ที่เพิ่งอัดทิ้ง กู้คืนไม่ได้ แน่ใจไหม?", parent=win):
                return
            player.stop()
            win.grab_release()
            win.destroy()
            try:
                os.remove(rec.path)
            except OSError:
                pass
            ui["last_path"] = None
            play_btn.config(state="disabled")
            wave_canvas.delete("all")
            result_var.set("ทิ้งไฟล์แล้ว")
            result_lbl.config(foreground="#5F5E5A")

        # the target class gets its own big button; the rest are "not cough"
        ttk.Button(win, text="1  ไอ (cough)", style="Big.TButton", takefocus=False,
                   command=lambda: choose("cough")).pack(padx=16, pady=(4, 8), fill="x")
        others = ttk.LabelFrame(win, text="ไม่ใช่เสียงไอ")
        others.pack(padx=16, pady=4, fill="x")
        for i, (text, label) in enumerate(LABELS[1:]):
            ttk.Button(others, text=f"{i + 2}  {text}", takefocus=False,
                       command=lambda l=label: choose(l)).grid(row=i // 3, column=i % 3, padx=4, pady=4, sticky="ew")
        for c in range(3):
            others.columnconfigure(c, weight=1)

        bottom = ttk.Frame(win)
        bottom.pack(fill="x", padx=16, pady=(8, 14))
        ttk.Button(bottom, text="ไว้ระบุทีหลัง", takefocus=False,
                   command=lambda: choose(UNLABELED)).pack(side="left")
        ttk.Button(bottom, text="ทิ้งไฟล์นี้", takefocus=False, command=discard).pack(side="right")

        for i, (_, label) in enumerate(LABELS):
            win.bind(str(i + 1), lambda e, l=label: choose(l))
        win.protocol("WM_DELETE_WINDOW", lambda: choose(UNLABELED))
        win.update_idletasks()
        x = root.winfo_rootx() + (root.winfo_width() - win.winfo_reqwidth()) // 2
        y = root.winfo_rooty() + 80
        win.geometry(f"+{max(x, 0)}+{max(y, 0)}")
        win.grab_set()
        win.focus_set()

    def show_result(rec, label, problems):
        ui["last_path"] = rec.path
        play_btn.config(state="normal")
        info = f"บันทึกเป็น \"{LABEL_TEXT[label]}\"  →  {rec.path}"
        if problems:
            result_var.set(info + "\n⚠ " + "\n⚠ ".join(problems))
            result_lbl.config(foreground="#A32D2D")
        else:
            result_var.set(info + "\n✓ ไฟล์ใช้ได้")
            result_lbl.config(foreground="#3B6D11")
        refresh_stats()

    def draw_wave(rec):
        wave_canvas.delete("all")
        width = max(wave_canvas.winfo_width(), 200)
        height = int(wave_canvas["height"])
        mid = height / 2
        with wave.open(rec.path, "rb") as w:
            a = array.array("h")
            a.frombytes(w.readframes(w.getnframes()))
        if not a:
            return
        step = max(1, len(a) // width)
        for x in range(width):
            seg = a[x * step:(x + 1) * step]
            if not seg:
                break
            y1 = mid - max(seg) / 32768 * mid
            y2 = mid - min(seg) / 32768 * mid
            wave_canvas.create_line(x, y1, x, y2 + 1, fill="#534AB7")
        for t in rec.marks:
            x = t * SAMPLE_RATE / step
            wave_canvas.create_line(x, 0, x, height, fill="#E24B4A", dash=(3, 2))

    def play_last():
        if ui["last_path"] and os.path.exists(ui["last_path"]):
            player.play(ui["last_path"])

    def open_dir():
        os.makedirs(ui["root_dir"], exist_ok=True)
        open_folder(ui["root_dir"])

    rec_btn.config(command=toggle)
    play_btn.config(command=play_last)
    folder_btn.config(command=open_dir)
    dir_btn.config(command=pick_root_dir)
    root.bind("<space>", lambda e: add_mark() if ui["rec"] and not isinstance(e.widget, ttk.Entry) else None)

    # --- periodic update ----------------------------------------------------
    def tick():
        st = board.state
        light.itemconfig(dot, fill=COLORS.get(st, COLORS[OFFLINE]))
        state_var.set(STATE_TEXT.get(st, STATE_TEXT[OFFLINE]))
        port_var.set(f"บอร์ด: {board.port}" if board.port else "กำลังหาบอร์ด... เสียบสาย USB ของบอร์ด")

        rec = ui["rec"]
        if rec is not None:
            secs = int(rec.seconds)
            time_var.set(f"{secs // 60:02d}:{secs % 60:02d}")
            frac = min(1.0, max(0.0, (board.level_db + 60) / 60))
            meter.coords(bar, 0, 0, 400 * frac, 14)
            if time.time() - ui["keepalive"] > 1.0:
                board.send(b"K")
                ui["keepalive"] = time.time()
            if ui["stopping"] and (st in (ST_STOPPED, ST_IDLE) or time.time() > ui["deadline"]):
                finish()
            elif not ui["stopping"] and st == OFFLINE:
                ui["note"] = "บอร์ดหลุดระหว่างอัด ไฟล์ถูกบันทึกไว้เท่าที่ได้"
                ui["stopping"] = True
                ui["deadline"] = 0
        else:
            meter.coords(bar, 0, 0, 0, 14)
        root.after(50, tick)

    def on_close():
        rec = ui["rec"]
        if rec is not None:      # closed mid-recording: keep it as unlabeled
            board.send(b"S")
            with board.lock:
                board.rec = None
            rec.close()
            try:
                file_label(ui["root_dir"], rec, UNLABELED, check_quality(rec))
            except OSError:
                pass
        player.stop()
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_close)
    refresh_stats()
    if "root_dir" not in settings:
        root.after(300, first_run)
    tick()
    root.mainloop()


if __name__ == "__main__":
    run_gui()
