"""Download raw audio datasets into data/raw/<name>/.

Usage (run from node2/experiment):
    uv run python src/download_data.py --list
    uv run python src/download_data.py                      # default set
    uv run python src/download_data.py esc50 speech_commands
    uv run python src/download_data.py common_voice_th      # 9 GB, needs .env key
    uv run python src/download_data.py musan coswara        # large, opt-in

Each dataset lands in data/raw/<name>/. A ".done" file marks a finished
dataset, so running the script again skips it (use --force to redo).
data/raw/SOURCES.md records URL + license of every dataset for the report.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tarfile
import urllib.request
import zipfile
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]  # node2/experiment
RAW = ROOT / "data" / "raw"

DATASETS = {
    "coughvid": {
        "kind": "kaggle",
        "handle": "nasrulhakim86/coughvid-wav",
        "size": "> 3.5 GB",
        "license": "CC BY 4.0 (COUGHVID, Orlandic et al. 2021)",
        "use": "cough",
        "default": True,
    },
    "speech_commands": {
        "kind": "url",
        "url": "http://download.tensorflow.org/data/speech_commands_v0.02.tar.gz",
        "size": "2.4 GB",
        "license": "CC BY 4.0 (Warden 2018)",
        "use": "speech + _background_noise_",
        "default": True,
    },
    "esc50": {
        "kind": "url",
        "url": "https://github.com/karolpiczak/ESC-50/archive/refs/heads/master.zip",
        "size": "~600 MB",
        "license": "CC BY-NC 3.0 (Piczak 2015)",
        "use": "background + hard negatives; class 'coughing' goes to cough, not background",
        "default": True,
    },
    "common_voice_th": {
        "kind": "mdc",
        "dataset_id": "cmu5x5tj000f2nq0755h8qcy5",
        "url": "https://datacollective.mozillafoundation.org (Common Voice Scripted Speech 27.0 - Thai)",
        "size": "9.0 GB",
        "license": "CC0 1.0 (Mozilla Common Voice 27.0)",
        "use": "Thai speech (MP3, 48 kHz, resample to 16 kHz)",
        "default": False,
    },
    "musan": {
        "kind": "url",
        "url": "https://www.openslr.org/resources/17/musan.tar.gz",
        "size": "11 GB",
        "license": "CC BY 4.0 (Snyder et al. 2015)",
        "use": "noise / music for augmentation",
        "default": False,
    },
    "coswara": {
        "kind": "coswara",
        "url": "https://github.com/iiscleap/Coswara-Data.git",
        "size": "~16 GB repo",
        "license": "CC BY 4.0 (Sharma et al. 2020)",
        "use": "cough + breathing + counting speech from same people",
        "default": False,
    },
}

MANUAL = {
    "fsd50k": "https://zenodo.org/records/4060432 (multi-part zip, ~30 GB, per-file licenses)",
}


def download(url: str, dest: Path) -> None:
    """Stream url to dest via a .part file so an interrupted run never looks finished."""
    part = dest.with_name(dest.name + ".part")
    req = urllib.request.Request(url, headers={"User-Agent": "dataset-downloader"})
    with urllib.request.urlopen(req) as resp, open(part, "wb") as f:
        total = int(resp.headers.get("Content-Length") or 0)
        done, last_pct = 0, -1
        while chunk := resp.read(1 << 20):
            f.write(chunk)
            done += len(chunk)
            if total:
                pct = done * 100 // total
                if pct != last_pct and pct % 5 == 0:
                    print(f"  {pct:3d}%  {done / 1e9:.2f} / {total / 1e9:.2f} GB", flush=True)
                    last_pct = pct
    part.rename(dest)


def safe_unzip(archive: Path, out: Path) -> None:
    """Extract a zip, refusing members that would escape out/."""
    out = out.resolve()
    with zipfile.ZipFile(archive) as z:
        for m in z.namelist():
            if not (out / m).resolve().is_relative_to(out):
                raise RuntimeError(f"unsafe path in zip: {m}")
        z.extractall(out)


def extract(archive: Path, out: Path) -> None:
    print(f"  extracting {archive.name}")
    if archive.suffix == ".zip":
        safe_unzip(archive, out)
    else:
        with tarfile.open(archive) as t:
            t.extractall(out, filter="data")  # blocks absolute paths, .., device files


def get_url(name: str, info: dict, out: Path, keep: bool) -> None:
    archive = out / info["url"].rsplit("/", 1)[-1]
    if not archive.exists():
        print(f"  downloading {info['url']}")
        download(info["url"], archive)
    extract(archive, out)
    if not keep:
        archive.unlink()


def get_kaggle(name: str, info: dict, out: Path, keep: bool) -> None:
    import kagglehub  # needs ~/.kaggle/kaggle.json or KAGGLE_API_TOKEN

    path = kagglehub.dataset_download(info["handle"], output_dir=str(out))
    print(f"  kagglehub: {path}")


def get_coswara(name: str, info: dict, out: Path, keep: bool) -> None:
    """Coswara stores each date folder as split tar.gz parts (.aa, .ab, ...)."""
    repo = out / "_repo"
    if not repo.exists():
        print(f"  git clone {info['url']} (large, takes a while)")
        subprocess.run(["git", "clone", "--depth", "1", info["url"], str(repo)], check=True)
    extracted = out / "extracted"
    extracted.mkdir(exist_ok=True)
    for day in sorted(p for p in repo.iterdir() if p.is_dir() and p.name.isdigit()):
        if (extracted / day.name).exists():
            continue
        parts = sorted(day.glob(f"{day.name}.tar.gz.*"))
        if not parts:
            continue
        joined = out / f"{day.name}.tar.gz"
        with open(joined, "wb") as f:
            for p in parts:
                with open(p, "rb") as src:
                    shutil.copyfileobj(src, f)
        extract(joined, extracted)
        joined.unlink()
    for csv in repo.glob("*.csv"):
        shutil.copy(csv, out / csv.name)  # metadata (labels per participant)
    if not keep:
        shutil.rmtree(repo)


def get_mdc(name: str, info: dict, out: Path, keep: bool) -> None:
    """Mozilla Data Collective. The SDK reads MDC_API_KEY; .env holds DATACOLLECTIVE_KEY."""
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")  # explicit path: works from any working directory
    if not os.getenv("MDC_API_KEY") and os.getenv("DATACOLLECTIVE_KEY"):
        os.environ["MDC_API_KEY"] = os.environ["DATACOLLECTIVE_KEY"]
    if not os.getenv("MDC_API_KEY"):
        raise RuntimeError("set DATACOLLECTIVE_KEY (or MDC_API_KEY) in node2/experiment/.env")

    from datacollective import download_dataset  # import after the key is set

    archive = download_dataset(info["dataset_id"], download_directory=str(out), extract=False)
    extract(Path(archive), out)
    if not keep:
        Path(archive).unlink()


HANDLERS = {"url": get_url, "kaggle": get_kaggle, "coswara": get_coswara, "mdc": get_mdc}


def record_source(name: str, info: dict) -> None:
    sources = RAW / "SOURCES.md"
    if not sources.exists():
        sources.write_text("# Raw audio sources\n\n| dataset | source | license | used for | downloaded |\n|---|---|---|---|---|\n")
    text = sources.read_text()
    if f"| {name} |" in text:
        return
    src = info.get("url") or f"kaggle:{info['handle']}"
    if "dataset_id" in info:
        src = f"{src}, id {info['dataset_id']}"
    with open(sources, "a") as f:
        f.write(f"| {name} | {src} | {info['license']} | {info['use']} | {date.today()} |\n")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("names", nargs="*", help="datasets to fetch (default: the default set)")
    ap.add_argument("--list", action="store_true", help="show datasets and exit")
    ap.add_argument("--force", action="store_true", help="redo even if .done exists")
    ap.add_argument("--keep-archives", action="store_true", help="keep .tar.gz/.zip after extracting")
    args = ap.parse_args()

    if args.list:
        for n, i in DATASETS.items():
            flag = "default" if i["default"] else "opt-in "
            print(f"{n:16} {flag}  {i['size']:>12}  {i['license']}")
        print("\nmanual download:")
        for n, how in MANUAL.items():
            print(f"{n:16} {how}")
        return 0

    names = args.names or [n for n, i in DATASETS.items() if i["default"]]
    unknown = [n for n in names if n not in DATASETS]
    if unknown:
        print(f"unknown dataset(s): {unknown}. Use --list.", file=sys.stderr)
        return 2

    RAW.mkdir(parents=True, exist_ok=True)
    failed = []
    for name in names:
        info = DATASETS[name]
        out = RAW / name
        marker = out / ".done"
        if marker.exists() and not args.force:
            print(f"[skip] {name} (already done)")
            continue
        print(f"[get ] {name}  {info['size']}")
        out.mkdir(parents=True, exist_ok=True)
        try:
            HANDLERS[info["kind"]](name, info, out, args.keep_archives)
        except Exception as e:  # keep going with the other datasets
            print(f"[fail] {name}: {e}", file=sys.stderr)
            failed.append(name)
            continue
        marker.touch()
        record_source(name, info)
        print(f"[done] {name}")

    if failed:
        print(f"failed: {failed}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
