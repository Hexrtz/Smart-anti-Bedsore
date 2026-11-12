"""Check sample rate / channels / length of every audio file under data/raw.

Reads only file headers (fast), using all CPU cores.

Usage (run from node2/experiment):
    uv run python src/check_audio.py                    # all datasets
    uv run python src/check_audio.py esc50 inmp441      # some datasets
    uv run python src/check_audio.py --target 16000 --csv data/reports/audio_info.csv

Files with sample rate BELOW --target are the problem: resampling up cannot
recover frequencies the recording never had. Files at or above the target are
fine, prep will resample them down.
"""

from __future__ import annotations

import argparse
import csv
import statistics
import sys
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import soundfile as sf

ROOT = Path(__file__).resolve().parents[1]  # node2/experiment
RAW = ROOT / "data" / "raw"
AUDIO_EXT = {".wav", ".flac", ".ogg", ".mp3", ".webm", ".m4a"}


def probe(path: Path) -> dict:
    row = {"dataset": path.relative_to(RAW).parts[0], "path": str(path.relative_to(RAW))}
    try:
        info = sf.info(str(path))
        row.update(sr=info.samplerate, channels=info.channels, seconds=round(info.duration, 3),
                   subtype=info.subtype, error="")
    except Exception as e:  # unreadable / unsupported format (e.g. .webm)
        row.update(sr=None, channels=None, seconds=None, subtype=None, error=str(e)[:200])
    return row


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("datasets", nargs="*", help="folders under data/raw (default: all)")
    ap.add_argument("--target", type=int, default=16000, help="required sample rate in Hz (default 16000)")
    ap.add_argument("--csv", type=Path, help="write one row per file to this CSV")
    ap.add_argument("--workers", type=int, default=None, help="processes (default: all cores)")
    args = ap.parse_args()

    names = args.datasets or sorted(p.name for p in RAW.iterdir() if p.is_dir())
    missing = [n for n in names if not (RAW / n).is_dir()]
    if missing:
        print(f"not found under {RAW}: {missing}", file=sys.stderr)
        return 2

    files = [p for n in names for p in (RAW / n).rglob("*")
             if p.suffix.lower() in AUDIO_EXT and p.is_file()]
    print(f"checking {len(files)} files in {names} ...", flush=True)

    with ProcessPoolExecutor(args.workers) as pool:
        rows = list(pool.map(probe, files, chunksize=256))

    by_ds: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_ds[r["dataset"]].append(r)

    any_low = False
    for ds in names:
        rs = by_ds.get(ds, [])
        ok = [r for r in rs if not r["error"]]
        errs = [r for r in rs if r["error"]]
        low = [r for r in ok if r["sr"] < args.target]
        any_low |= bool(low)
        srs = Counter(r["sr"] for r in ok)
        ch = Counter(r["channels"] for r in ok)
        secs = [r["seconds"] for r in ok]

        print(f"\n== {ds}: {len(rs)} files")
        if not rs:
            continue
        print("  sample rate : " + ", ".join(f"{sr} Hz x{n}" for sr, n in sorted(srs.items())))
        print("  channels    : " + ", ".join(f"{c}ch x{n}" for c, n in sorted(ch.items())))
        if secs:
            print(f"  seconds     : min {min(secs):.2f}  median {statistics.median(secs):.2f}  max {max(secs):.2f}")
        verdict = "OK, all >= target" if not low else f"BELOW {args.target} Hz: {len(low)} files"
        print(f"  verdict     : {verdict}")
        for r in low[:5]:
            print(f"     {r['sr']} Hz  {r['path']}")
        if errs:
            print(f"  unreadable  : {len(errs)} files, e.g. {errs[0]['path']}: {errs[0]['error']}")

    if args.csv:
        args.csv.parent.mkdir(parents=True, exist_ok=True)
        with open(args.csv, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=["dataset", "path", "sr", "channels", "seconds", "subtype", "error"])
            w.writeheader()
            w.writerows(rows)
        print(f"\nwrote {args.csv}")

    return 1 if any_low else 0


if __name__ == "__main__":
    sys.exit(main())
