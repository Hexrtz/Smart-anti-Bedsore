"""Pick which raw files go into training and write data/manifest.csv.

Rule:
  cough  = ALL COUGHVID files with cough_detected >= --cough-min (default 0.8)
           + ESC-50 'coughing' + INMP441 recordings labelled cough
  other  = the same number of files, made of
           - every INMP441 non-cough recording (real mic, real room: keep all)
           - every ESC-50 non-cough clip (background + hard negatives)
           - every Speech Commands _background_noise_ file
           - the rest filled by random Speech Commands words and Common Voice
             Thai clips, split by --cv-share (default 0.5)

Output data/manifest.csv, one row per file:
  interim_path  path under data/interim (what training reads)
  raw_path      path under data/raw     (what to_interim converts)
  dataset       source dataset
  label         cough | speech | background
  category      original class in the source (ESC category, word, ...)
  group         speaker / recording id, for splitting train/val/test
  seconds       file length (from data/reports/audio_info.csv)
  cough_detected  COUGHVID score, empty for other datasets

Balance is by file count. Files differ in length (COUGHVID ~10 s, Speech
Commands 1 s), so the clip stage may need to balance again.

Usage (run from node2/experiment):
    uv run python src/select_data.py
    uv run python src/select_data.py --cough-min 0.9 --cv-share 0.7 --seed 1
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]  # node2/experiment
RAW = ROOT / "data" / "raw"
AUDIO_INFO = ROOT / "data" / "reports" / "audio_info.csv"
MANIFEST = ROOT / "data" / "manifest.csv"

COUGHVID = RAW / "coughvid" / "public_dataset"
ESC50 = RAW / "esc50" / "ESC-50-master"
SPEECH_COMMANDS = RAW / "speech_commands"
CV_DIR = next((RAW / "common_voice_th").glob("cv-corpus-*/th"), None)
INMP441 = RAW / "inmp441" / "records"

MIN_SECONDS = 1.0  # shorter clips cannot fill one 1 s training window

COLUMNS = ["raw_path", "dataset", "label", "category", "group", "cough_detected"]


def rel(p: Path) -> str:
    return str(p.relative_to(RAW))


def rows(df: pd.DataFrame) -> pd.DataFrame:
    for c in COLUMNS:
        if c not in df:
            df[c] = None
    return df[COLUMNS]


def coughvid(cough_min: float) -> pd.DataFrame:
    m = pd.read_csv(COUGHVID / "metadata_compiled.csv")
    m = m[m["cough_detected"] >= cough_min]
    m = m[[(COUGHVID / f"{u}.wav").exists() for u in m["uuid"]]]
    return rows(pd.DataFrame({
        "raw_path": [rel(COUGHVID / f"{u}.wav") for u in m["uuid"]],
        "dataset": "coughvid", "label": "cough", "category": "cough",
        "group": "coughvid-" + m["uuid"], "cough_detected": m["cough_detected"].values,
    }))


def esc50() -> pd.DataFrame:
    m = pd.read_csv(ESC50 / "meta" / "esc50.csv")
    return rows(pd.DataFrame({
        "raw_path": [rel(ESC50 / "audio" / f) for f in m["filename"]],
        "dataset": "esc50",
        "label": ["cough" if c == "coughing" else "background" for c in m["category"]],
        "category": m["category"].values,
        "group": "esc50-" + m["src_file"].astype(str),  # takes A/B/C of one source clip stay together
    }))


def speech_commands() -> tuple[pd.DataFrame, pd.DataFrame]:
    words, noise = [], []
    for p in sorted(SPEECH_COMMANDS.glob("*/*.wav")):
        if p.parent.name == "_background_noise_":
            noise.append({"raw_path": rel(p), "category": p.stem, "group": f"sc-noise-{p.stem}"})
        else:
            speaker = p.name.split("_nohash_")[0]
            words.append({"raw_path": rel(p), "category": p.parent.name, "group": f"sc-{speaker}"})
    w = pd.DataFrame(words).assign(dataset="speech_commands", label="speech")
    n = pd.DataFrame(noise).assign(dataset="speech_commands", label="background")
    return rows(w), rows(n)


def common_voice() -> pd.DataFrame:
    if CV_DIR is None:
        return rows(pd.DataFrame())
    m = pd.read_csv(CV_DIR / "validated.tsv", sep="\t", usecols=["client_id", "path"], quoting=3)
    return rows(pd.DataFrame({
        "raw_path": [rel(CV_DIR / "clips" / p) for p in m["path"]],
        "dataset": "common_voice_th", "label": "speech", "category": "read_speech_th",
        "group": "cv-" + m["client_id"].str[:16],
    }))


def inmp441() -> pd.DataFrame:
    labels_csv = INMP441 / "labels.csv"
    if labels_csv.exists():
        m = pd.read_csv(labels_csv, encoding="utf-8-sig")
        m = m[[(INMP441 / f).exists() for f in m["file"]]]
        df = pd.DataFrame({
            "raw_path": [rel(INMP441 / f) for f in m["file"]],
            "label": m["label"].values,
            "group": ["inmp441-" + Path(f).stem for f in m["file"]],  # one speaker so far: split by recording
        })
    else:  # fall back to folder name = label
        ps = sorted(INMP441.glob("*/*.wav"))
        df = pd.DataFrame({"raw_path": [rel(p) for p in ps], "label": [p.parent.name for p in ps],
                           "group": "inmp441-unknown"})
    df["category"] = df["label"]
    df["dataset"] = "inmp441"
    # any non-cough label from the recorder (speech, silence, motor, ...) maps to speech/background
    df.loc[~df["label"].isin(["cough", "speech"]), "label"] = "background"
    return rows(df)


def sample(df: pd.DataFrame, n: int, seed: int) -> pd.DataFrame:
    return df if len(df) <= n else df.sample(n=n, random_state=seed)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cough-min", type=float, default=0.8, help="min COUGHVID cough_detected (default 0.8)")
    ap.add_argument("--cv-share", type=float, default=0.5,
                    help="share of the random speech fill taken from Common Voice; rest from Speech Commands")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--out", type=Path, default=MANIFEST)
    args = ap.parse_args()

    sc_words, sc_noise = speech_commands()
    inmp = inmp441()
    esc = esc50()
    cv = common_voice()

    cough = pd.concat([coughvid(args.cough_min), esc[esc.label == "cough"], inmp[inmp.label == "cough"]])
    fixed_other = pd.concat([inmp[inmp.label != "cough"], esc[esc.label != "cough"], sc_noise])

    # drop unreadable or too-short random-pool files, using the check_audio report
    if AUDIO_INFO.exists():
        info = pd.read_csv(AUDIO_INFO, usecols=["path", "seconds", "error"])
        good = info[info["error"].isna() & (info["seconds"] >= MIN_SECONDS)]
        seconds = dict(zip(info["path"], info["seconds"]))
        cv = cv[cv.raw_path.isin(good.path)]
        sc_words = sc_words[sc_words.raw_path.isin(good.path)]
    else:
        print(f"warning: {AUDIO_INFO} missing, run check_audio.py first; no length filter", file=sys.stderr)
        seconds = {}

    need = max(len(cough) - len(fixed_other), 0)
    n_cv = min(round(need * args.cv_share), len(cv))
    n_sc = min(need - n_cv, len(sc_words))
    n_cv = min(need - n_sc, len(cv))  # if Speech Commands ran short, Common Voice fills the gap
    other = pd.concat([fixed_other, sample(cv, n_cv, args.seed), sample(sc_words, n_sc, args.seed)])

    out = pd.concat([cough, other], ignore_index=True)
    out.insert(0, "interim_path", [str(Path(p).with_suffix(".wav")) for p in out.raw_path])
    out.insert(out.columns.get_loc("group") + 1, "seconds", out.raw_path.map(seconds))
    out = out.sort_values(["label", "dataset", "raw_path"]).reset_index(drop=True)
    assert out.interim_path.is_unique, "two raw files map to the same interim path"

    args.out.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(args.out, index=False)

    n_cough = (out.label == "cough").sum()
    print(f"cough {n_cough}  other {len(out) - n_cough}  total {len(out)}  -> {args.out}")
    print(pd.crosstab(out.dataset, out.label, margins=True).to_string())
    if len(out) - n_cough < n_cough:
        print("note: not enough non-cough files to fully balance", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
