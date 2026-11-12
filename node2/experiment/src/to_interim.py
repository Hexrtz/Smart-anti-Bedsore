"""Convert every raw audio file to mono 16 kHz 16-bit WAV in data/interim/.

One output file per input file, same relative path, extension .wav:
    data/raw/esc50/ESC-50-master/audio/1-100038-A-14.wav
 -> data/interim/esc50/ESC-50-master/audio/1-100038-A-14.wav

This stage does NOT cut clips, assign labels or compute features.
Labels and time ranges belong to the manifest stage; log-mel belongs to training.

Usage (run from node2/experiment):
    uv run python src/to_interim.py --manifest data/manifest.csv --prune   # pipeline mode
    uv run python src/to_interim.py                         # all datasets
    uv run python src/to_interim.py esc50 inmp441           # some datasets
    uv run python src/to_interim.py common_voice_th --sample 20000
    uv run python src/to_interim.py --force                 # redo everything

Re-running is cheap: a file is skipped when its output exists and is newer
than the input, so adding new recordings only converts the new ones.
Failed files go to data/reports/to_interim_errors.csv.

--manifest converts only the raw_path rows of a manifest (from select_data.py).
--prune (with --manifest) deletes interim files that are not in the manifest,
so data/interim always matches the current selection.
"""

from __future__ import annotations

import argparse
import csv
import os
import random
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd
import soundfile as sf
import soxr

ROOT = Path(__file__).resolve().parents[1]  # node2/experiment
RAW = ROOT / "data" / "raw"
INTERIM = ROOT / "data" / "interim"
REPORTS = ROOT / "data" / "reports"

TARGET_SR = 16000
AUDIO_EXT = {".wav", ".flac", ".ogg", ".mp3", ".m4a"}


def out_path(src: Path) -> Path:
    return (INTERIM / src.relative_to(RAW)).with_suffix(".wav")


def convert(src: Path) -> tuple[str, str]:
    """Return (status, message). status: done | skip | fail."""
    dst = out_path(src)
    try:
        if dst.exists() and dst.stat().st_mtime >= src.stat().st_mtime:
            return "skip", ""
        y, sr = sf.read(str(src), dtype="float32", always_2d=True)  # (frames, channels)
        if y.shape[0] == 0:
            return "fail", "empty file"
        y = y.mean(axis=1)                                   # mono
        if sr != TARGET_SR:
            if sr < TARGET_SR:
                return "fail", f"sample rate {sr} < {TARGET_SR}, refusing to upsample"
            y = soxr.resample(y, sr, TARGET_SR, quality="HQ")  # anti-aliased downsample
        y = np.clip(y, -1.0, 1.0)                            # resampling can overshoot slightly
        dst.parent.mkdir(parents=True, exist_ok=True)
        tmp = dst.with_name(dst.name + ".tmp")
        sf.write(str(tmp), y, TARGET_SR, subtype="PCM_16", format="WAV")
        os.replace(tmp, dst)                                 # atomic: no half-written outputs
        return "done", ""
    except Exception as e:
        return "fail", str(e)[:200]


def list_files(dataset: str) -> list[Path]:
    base = RAW / dataset
    return sorted(
        p for p in base.rglob("*")
        if p.suffix.lower() in AUDIO_EXT
        and p.is_file()
        and not any(part.startswith(".") for part in p.relative_to(base).parts)  # skip .complete/ etc.
    )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("datasets", nargs="*", help="folders under data/raw (default: all)")
    ap.add_argument("--manifest", type=Path, help="convert only raw_path rows of this CSV")
    ap.add_argument("--prune", action="store_true", help="with --manifest: delete interim files not in it")
    ap.add_argument("--sample", type=int, help="convert only N random files per dataset (same N files every run)")
    ap.add_argument("--seed", type=int, default=42, help="seed for --sample (default 42)")
    ap.add_argument("--force", action="store_true", help="reconvert even if output is up to date")
    ap.add_argument("--workers", type=int, default=None, help="processes (default: all cores)")
    args = ap.parse_args()

    if args.manifest:
        raw_paths = pd.read_csv(args.manifest, usecols=["raw_path"])["raw_path"]
        if args.datasets:
            raw_paths = raw_paths[raw_paths.str.split("/").str[0].isin(args.datasets)]
        by_ds: dict[str, list[Path]] = {}
        for rp in sorted(raw_paths):
            by_ds.setdefault(rp.split("/", 1)[0], []).append(RAW / rp)
    else:
        if args.prune:
            print("--prune needs --manifest", file=sys.stderr)
            return 2
        names = args.datasets or sorted(p.name for p in RAW.iterdir() if p.is_dir())
        missing = [n for n in names if not (RAW / n).is_dir()]
        if missing:
            print(f"not found under {RAW}: {missing}", file=sys.stderr)
            return 2
        by_ds = {}
        for ds in names:
            files = list_files(ds)
            if args.sample and len(files) > args.sample:
                files = sorted(random.Random(args.seed).sample(files, args.sample))
            by_ds[ds] = files

    errors: list[tuple[str, str]] = []
    for ds, files in by_ds.items():
        if args.force:
            for f in files:
                out_path(f).unlink(missing_ok=True)

        t0 = time.time()
        counts = {"done": 0, "skip": 0, "fail": 0}
        print(f"== {ds}: {len(files)} files", flush=True)
        with ProcessPoolExecutor(args.workers) as pool:
            for i, (src, (status, msg)) in enumerate(zip(files, pool.map(convert, files, chunksize=64)), 1):
                counts[status] += 1
                if status == "fail":
                    errors.append((str(src.relative_to(RAW)), msg))
                if i % 10000 == 0:
                    print(f"   {i}/{len(files)}", flush=True)
        print(f"   converted {counts['done']}, up to date {counts['skip']}, failed {counts['fail']}"
              f"  ({time.time() - t0:.0f} s)", flush=True)

    if args.prune:
        keep = {out_path(f) for files in by_ds.values() for f in files}
        removed = 0
        for f in INTERIM.rglob("*.wav"):
            if f not in keep:
                f.unlink()
                removed += 1
        for d in sorted((d for d in INTERIM.rglob("*") if d.is_dir()), reverse=True):
            if not any(d.iterdir()):
                d.rmdir()
        print(f"pruned {removed} interim files not in {args.manifest.name}")

    REPORTS.mkdir(parents=True, exist_ok=True)
    err_file = REPORTS / "to_interim_errors.csv"
    with open(err_file, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["path", "error"])
        w.writerows(errors)
    if errors:
        print(f"{len(errors)} failures, see {err_file}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
