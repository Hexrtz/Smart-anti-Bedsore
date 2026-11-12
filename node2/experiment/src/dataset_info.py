"""Summarise how much audio each dataset has: files, hours, share of total.

Usage (run from node2/experiment):
    uv run python src/dataset_info.py raw          # everything downloaded
    uv run python src/dataset_info.py interim      # after select + to_interim
    uv run python src/dataset_info.py processed
    uv run python src/dataset_info.py raw interim  # compare before / after sampling

interim files are matched to data/manifest.csv, processed clips to
data/clips.csv, so the report also shows the split per label
(cough / speech / background) and a dataset x label table in hours. That is
where to check balance. processed also shows clips per split x label.
Raw files have no label until select_data.py runs, so raw shows datasets only.
"""

from __future__ import annotations

import argparse
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import pandas as pd
import soundfile as sf

ROOT = Path(__file__).resolve().parents[1]  # node2/experiment
DATA = ROOT / "data"
MANIFEST = DATA / "manifest.csv"
CLIPS = DATA / "clips.csv"
STAGES = ("raw", "interim", "processed")
AUDIO_EXT = {".wav", ".flac", ".ogg", ".mp3", ".m4a"}


def seconds(path: Path) -> float | None:
    try:
        return sf.info(str(path)).duration
    except Exception:
        return None


def hms(sec: float) -> str:
    h, rem = divmod(int(round(sec)), 3600)
    return f"{h}:{rem // 60:02d}:{rem % 60:02d}"


def table(df: pd.DataFrame, key: str) -> str:
    g = df.groupby(key).agg(files=("path", "size"), sec=("seconds", "sum"))
    g = g.sort_values("sec", ascending=False)
    total_files, total_sec = g["files"].sum(), g["sec"].sum()
    g.loc["TOTAL"] = [total_files, total_sec]
    out = pd.DataFrame({
        "files": g["files"].astype(int),
        "files %": (100 * g["files"] / total_files).round(1),
        "hours": (g["sec"] / 3600).round(2),
        "h:mm:ss": g["sec"].map(hms),
        "hours %": (100 * g["sec"] / total_sec).round(1),
        "avg s/file": (g["sec"] / g["files"]).round(2),
    })
    return out.to_string()


def report(stage: str, workers: int | None) -> int:
    base = DATA / stage
    if not base.is_dir():
        print(f"\n### {stage}: {base} does not exist yet")
        return 1
    files = [p for p in base.rglob("*")
             if p.suffix.lower() in AUDIO_EXT and p.is_file()
             and not any(part.startswith(".") for part in p.relative_to(base).parts)]
    print(f"\n### {stage}: {len(files)} audio files in {base.relative_to(ROOT)}", flush=True)
    if not files:
        return 1
    with ProcessPoolExecutor(workers) as pool:
        secs = list(pool.map(seconds, files, chunksize=256))

    df = pd.DataFrame({"path": [str(p.relative_to(base)) for p in files], "seconds": secs})
    if stage == "processed":  # processed/<split>/<label>/<dataset>__...wav
        df["dataset"] = df["path"].str.split("/").str[-1].str.split("__").str[0]
    else:
        df["dataset"] = df["path"].str.split("/").str[0]
    bad = df["seconds"].isna().sum()
    if bad:
        print(f"   {bad} unreadable files left out")
        df = df.dropna(subset=["seconds"])

    print("\nby dataset")
    print(table(df, "dataset"))

    index = {"interim": (MANIFEST, "interim_path"), "processed": (CLIPS, "clip_path")}.get(stage)
    if index and index[0].exists():
        csv_file, key = index
        cols = [key, "label"] + (["split"] if stage == "processed" else [])
        m = pd.read_csv(csv_file, usecols=cols)
        df = df.merge(m, left_on="path", right_on=key, how="left")
        df["label"] = df["label"].fillna(f"NOT IN {csv_file.name}")
        print("\nby label")
        print(table(df, "label"))
        hours = pd.crosstab(df["dataset"], df["label"], values=df["seconds"] / 3600,
                            aggfunc="sum", margins=True, margins_name="TOTAL").fillna(0).round(2)
        print("\nhours, dataset x label")
        print(hours.to_string())
        if stage == "processed":
            print("\nclips, label x split")
            print(pd.crosstab(df["label"], df["split"], margins=True, margins_name="TOTAL").to_string())
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("stages", nargs="*", default=["raw"], choices=STAGES, help="default: raw")
    ap.add_argument("--workers", type=int, default=None, help="processes (default: all cores)")
    args = ap.parse_args()
    rc = 0
    for stage in args.stages:
        rc |= report(stage, args.workers)
    return rc


if __name__ == "__main__":
    sys.exit(main())
