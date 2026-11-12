"""Cut training clips from data/interim into data/processed, write data/clips.csv.

Each clip holds one 1 s training window (--clip-s) plus --margin-s of real
audio on each side (default 0.5 s, so files are 2 s). Training crops 1 s out
of it: a centre crop for val/test, a random crop for train (time-shift
augmentation without padding silence). --margin-s 0 gives plain 1 s clips.

cough       find each cough event by energy (hysteresis on frame RMS), put the
            event's energy centre in the middle of the clip. Up to
            --max-per-file events per file, strongest first, centres at least
            0.5 s apart. INMP441 marks_sec (from labels.csv) are used when set.
speech      the loudest 1 s of each file (Speech Commands, Common Voice).
            Long INMP441 speech recordings: every non-silent 1 s.
background  every non-silent, non-overlapping 1 s (ESC-50, _background_noise_,
            INMP441 background).

Balance (by clip count): other = cough * --other-ratio, of which --bg-share is
background, rest speech. INMP441 clips are always kept. If there are not
enough non-cough clips, cough clips are dropped instead, 2nd/3rd event of a
file first, so most files keep at least one clip.

Split: train/val/test 80/10/10 by hash of `group` (speaker / recording), so
the same person never lands in two splits and adding data never moves old
groups.

data/clips.csv columns (times in seconds, relative to the clip file):
  clip_path, split, label, dataset, category, group, interim_path,
  src_start_s   where the clip starts in the interim file (negative = padded)
  center_s      centre of the 1 s window (= event centre for cough)
  event_start_s, event_end_s   cough event (other labels: the 1 s window)
  valid_start_s, valid_end_s   part of the clip that is real audio, not padding
  peak_db

Usage (run from node2/experiment):
    uv run python src/make_clips.py
    uv run python src/make_clips.py --margin-s 0 --max-per-file 2 --bg-share 0.4
"""

from __future__ import annotations

import argparse
import hashlib
import re
import shutil
import sys
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd
import soundfile as sf

ROOT = Path(__file__).resolve().parents[1]  # node2/experiment
DATA = ROOT / "data"
INTERIM = DATA / "interim"
PROCESSED = DATA / "processed"
MANIFEST = DATA / "manifest.csv"
CLIPS = DATA / "clips.csv"
INMP441_LABELS = DATA / "raw" / "inmp441" / "records" / "labels.csv"

SR = 16000
FRAME, HOP = 400, 160          # 25 ms frames, 10 ms hop
MIN_DYNAMIC_DB = 12.0          # peak - noise floor; below this no clear event
EVENT_MIN_S, EVENT_MAX_S = 0.08, 1.5
SILENCE_RMS = 1e-4             # ESC-50 pads clips with digital silence
MIN_CENTER_GAP_S = 0.5

# set by init() in every worker process
P: dict = {}


def init(params: dict) -> None:
    P.update(params)


# ---------- signal helpers ----------

def frame_db(y: np.ndarray) -> np.ndarray:
    if len(y) < FRAME:
        y = np.pad(y, (0, FRAME - len(y)))
    frames = np.lib.stride_tricks.sliding_window_view(y, FRAME)[::HOP]
    rms = np.sqrt((frames.astype(np.float64) ** 2).mean(axis=1))
    return 20 * np.log10(rms + 1e-10)


def frame_time(i: float) -> float:
    return (i * HOP + FRAME / 2) / SR


def cough_events(y: np.ndarray) -> list[dict]:
    """Energy events: runs above a low threshold that also cross a high one."""
    db = frame_db(y)
    floor, peak = np.percentile(db, 10), db.max()
    if peak - floor < MIN_DYNAMIC_DB:
        return []
    on = floor + 0.5 * (peak - floor)
    off = floor + 0.25 * (peak - floor)
    above = np.concatenate([[False], db > off, [False]])
    edges = np.flatnonzero(np.diff(above.astype(int)))
    events = []
    for a, b in zip(edges[::2], edges[1::2]):          # frames [a, b)
        seg = db[a:b]
        dur = (b - a) * HOP / SR
        if seg.max() < on or not (EVENT_MIN_S <= dur <= EVENT_MAX_S):
            continue
        w = 10 ** (seg / 10)                            # power weights
        c = float((np.arange(a, b) * w).sum() / w.sum())
        events.append({"center": frame_time(c), "start": a * HOP / SR,
                       "end": min((b - 1) * HOP + FRAME, len(y)) / SR, "peak_db": float(seg.max())})
    return events


def pick_spaced(events: list[dict], k: int) -> list[dict]:
    chosen = []
    for e in sorted(events, key=lambda e: -e["peak_db"]):
        if all(abs(e["center"] - c["center"]) >= MIN_CENTER_GAP_S for c in chosen):
            chosen.append(e)
        if len(chosen) == k:
            break
    return chosen


def loudest_window(y: np.ndarray, win_s: float) -> dict:
    dur = len(y) / SR
    if dur <= win_s:
        return {"center": dur / 2, "start": 0.0, "end": dur, "peak_db": float(frame_db(y).max())}
    p = np.concatenate([[0.0], np.cumsum(y.astype(np.float64) ** 2)])
    n = int(win_s * SR)
    starts = np.arange(0, len(y) - n + 1, HOP)
    s = int(starts[np.argmax(p[starts + n] - p[starts])])
    seg = y[s:s + n]
    return {"center": (s + n / 2) / SR, "start": s / SR, "end": (s + n) / SR,
            "peak_db": float(frame_db(seg).max())}


def tiled_windows(y: np.ndarray, win_s: float, min_db: float | None) -> list[dict]:
    """Non-overlapping windows; skip digital silence (and, if min_db, quiet ones)."""
    n = int(win_s * SR)
    out = []
    floor = np.percentile(frame_db(y), 10) if min_db is not None else None
    for s in range(0, len(y) - n + 1, n):
        seg = y[s:s + n]
        if np.sqrt((seg.astype(np.float64) ** 2).mean()) < SILENCE_RMS:
            continue
        pk = float(frame_db(seg).max())
        if min_db is not None and pk < floor + min_db:
            continue
        out.append({"center": (s + n / 2) / SR, "start": s / SR, "end": (s + n) / SR, "peak_db": pk})
    return out


# ---------- pass 1: find candidate windows per file ----------

def candidates(row: dict) -> list[dict]:
    path = INTERIM / row["interim_path"]
    try:
        y, sr = sf.read(str(path), dtype="float32")
    except Exception:
        return []
    assert sr == SR, f"{path} is {sr} Hz, run to_interim first"
    clip_s, label, ds = P["clip_s"], row["label"], row["dataset"]

    if label == "cough":
        events = cough_events(y)
        marks = row.get("marks")
        if marks:  # hand marks win: snap to the nearest detected event, else +-0.2 s
            snapped = []
            for m in marks:
                near = [e for e in events if abs(e["center"] - m) <= 0.3]
                snapped.append(min(near, key=lambda e: abs(e["center"] - m)) if near else
                               {"center": m, "start": max(m - 0.2, 0), "end": m + 0.2, "peak_db": np.nan})
            wins = snapped
        else:
            k = len(events) if ds == "inmp441" else P["max_per_file"]  # own mic: keep every cough
            wins = pick_spaced(events, k)
    elif label == "speech":
        wins = tiled_windows(y, clip_s, min_db=10.0) if ds == "inmp441" else [loudest_window(y, clip_s)]
    else:
        wins = tiled_windows(y, clip_s, min_db=None)

    out = []
    for k, w in enumerate(wins):
        if label != "cough":  # the event is the whole 1 s window
            w = {**w, "start": w["center"] - clip_s / 2, "end": w["center"] + clip_s / 2}
        out.append({**{c: row[c] for c in ("interim_path", "dataset", "label", "category", "group")},
                    "k": k, "center": w["center"], "ev_start": w["start"], "ev_end": w["end"],
                    "peak_db": w["peak_db"], "dur": len(y) / SR})
    return out


# ---------- pass 2: write clips ----------

def write_clips(items: list[dict]) -> list[dict]:
    y, _ = sf.read(str(INTERIM / items[0]["interim_path"]), dtype="float32")
    dur = len(y) / SR
    total = P["clip_s"] + 2 * P["margin_s"]
    n = int(round(total * SR))
    rows = []
    for it in items:
        start = it["center"] - total / 2
        start = min(max(start, 0.0), dur - total) if dur >= total else (dur - total) / 2
        s = int(round(start * SR))
        clip = np.zeros(n, dtype=np.float32)
        a, b = max(s, 0), min(s + n, len(y))
        clip[a - s:b - s] = y[a:b]
        dst = PROCESSED / it["clip_path"]
        dst.parent.mkdir(parents=True, exist_ok=True)
        sf.write(str(dst), clip, SR, subtype="PCM_16")
        t0 = s / SR
        rows.append({**it, "src_start_s": round(t0, 4),
                     "center_s": round(it["center"] - t0, 4),
                     "event_start_s": round(max(it["ev_start"] - t0, 0.0), 4),
                     "event_end_s": round(min(it["ev_end"] - t0, total), 4),
                     "valid_start_s": round((a - s) / SR, 4), "valid_end_s": round((b - s) / SR, 4)})
    return rows


# ---------- selection ----------

def split_of(group: str, val: int, test: int) -> str:
    h = int(hashlib.md5(group.encode()).hexdigest(), 16) % 100
    return "test" if h < test else "val" if h < test + val else "train"


def take(df: pd.DataFrame, n: int, seed: int) -> pd.DataFrame:
    """Keep INMP441 rows, fill the rest at random; lower k (1st event of a file) first."""
    keep = df[df.dataset == "inmp441"]
    rest = df[df.dataset != "inmp441"].sample(frac=1, random_state=seed).sort_values("k", kind="stable")
    return pd.concat([keep, rest.head(max(n - len(keep), 0))])


def read_marks() -> dict[str, list[float]]:
    if not INMP441_LABELS.exists():
        return {}
    m = pd.read_csv(INMP441_LABELS, encoding="utf-8-sig", dtype=str).fillna("")
    out = {}
    for f, marks in zip(m["file"], m.get("marks_sec", [""] * len(m))):
        vals = [float(x) for x in re.split(r"[;,\s]+", marks.strip()) if x]
        if vals:
            out[str(Path("inmp441/records") / Path(f).with_suffix(".wav"))] = vals
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--clip-s", type=float, default=1.0, help="training window length (default 1.0)")
    ap.add_argument("--margin-s", type=float, default=0.5, help="extra audio each side for shift augmentation")
    ap.add_argument("--max-per-file", type=int, default=3, help="max cough events per file (default 3)")
    ap.add_argument("--other-ratio", type=float, default=1.0, help="non-cough clips per cough clip")
    ap.add_argument("--bg-share", type=float, default=0.5, help="share of non-cough that is background")
    ap.add_argument("--val", type=int, default=10, help="percent of groups for val (default 10)")
    ap.add_argument("--test", type=int, default=10, help="percent of groups for test (default 10)")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--workers", type=int, default=None)
    args = ap.parse_args()

    m = pd.read_csv(MANIFEST)
    missing = [p for p in m.interim_path if not (INTERIM / p).exists()]
    if missing:
        print(f"{len(missing)} interim files missing (e.g. {missing[0]}). Run `make interim`.", file=sys.stderr)
        return 1
    marks = read_marks()
    recs = m.to_dict("records")
    for r in recs:
        r["marks"] = marks.get(r["interim_path"])

    params = {"clip_s": args.clip_s, "margin_s": args.margin_s, "max_per_file": args.max_per_file}
    print(f"pass 1: finding windows in {len(recs)} files", flush=True)
    with ProcessPoolExecutor(args.workers, initializer=init, initargs=(params,)) as pool:
        cand = pd.DataFrame([c for cs in pool.map(candidates, recs, chunksize=64) for c in cs])

    cough_files = m[m.label == "cough"]
    no_event = len(set(cough_files.interim_path) - set(cand[cand.label == "cough"].interim_path))
    print(f"   cough files with no clear event (skipped): {no_event} of {len(cough_files)}")

    cough, speech, bg = (cand[cand.label == lb] for lb in ("cough", "speech", "background"))
    n_other = round(len(cough) * args.other_ratio)
    n_bg = min(round(n_other * args.bg_share), len(bg))
    n_sp = min(n_other - n_bg, len(speech))
    n_bg = min(n_other - n_sp, len(bg))                 # speech short: background fills
    other = pd.concat([take(speech, n_sp, args.seed), take(bg, n_bg, args.seed)])
    if len(other) < n_other:                            # not enough non-cough: drop cough instead
        cough = take(cough, round(len(other) / args.other_ratio), args.seed)
    sel = pd.concat([cough, other], ignore_index=True)

    sel["split"] = [split_of(g, args.val, args.test) for g in sel.group]
    sel["clip_path"] = [
        f"{s}/{lb}/{ds}__{Path(ip).parent.name}__{Path(ip).stem}__{k:02d}.wav"
        for s, lb, ds, ip, k in zip(sel.split, sel.label, sel.dataset, sel.interim_path, sel.k)]
    assert sel.clip_path.is_unique

    if PROCESSED.exists():
        shutil.rmtree(PROCESSED)                        # always rebuilt from interim, cheap
    print(f"pass 2: writing {len(sel)} clips of {args.clip_s + 2 * args.margin_s:.2f} s", flush=True)
    groups = [g.to_dict("records") for _, g in sel.groupby("interim_path")]
    with ProcessPoolExecutor(args.workers, initializer=init, initargs=(params,)) as pool:
        rows = [r for rs in pool.map(write_clips, groups, chunksize=32) for r in rs]

    cols = ["clip_path", "split", "label", "dataset", "category", "group", "interim_path", "src_start_s",
            "center_s", "event_start_s", "event_end_s", "valid_start_s", "valid_end_s", "peak_db"]
    out = pd.DataFrame(rows)[cols].sort_values(["split", "label", "clip_path"])
    out["peak_db"] = out["peak_db"].round(1)
    out.to_csv(CLIPS, index=False)

    print(f"\nwrote {len(out)} clips -> {PROCESSED.relative_to(ROOT)}, {CLIPS.relative_to(ROOT)}")
    print(pd.crosstab(out.label, out.split, margins=True).to_string())
    print()
    print(pd.crosstab(out.dataset, out.label, margins=True).to_string())
    return 0


if __name__ == "__main__":
    sys.exit(main())
