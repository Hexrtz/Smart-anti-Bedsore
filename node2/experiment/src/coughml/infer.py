"""Load a training run and slide it over a recording."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import soundfile as sf
import soxr
import torch

from .models import build_model


def load_run(run_dir: Path, device: str = "cpu"):
    ckpt = torch.load(Path(run_dir) / "model.pt", map_location=device, weights_only=False)
    model = build_model(ckpt["config"], len(ckpt["classes"]))
    model.load_state_dict(ckpt["state_dict"])
    return model.to(device).eval(), ckpt["config"], ckpt["classes"]


def load_audio(path: Path, sr: int) -> np.ndarray:
    """Any audio file -> mono float32 at sr (same conversion as to_interim.py)."""
    y, file_sr = sf.read(str(path), dtype="float32", always_2d=True)
    y = y.mean(axis=1)
    if file_sr != sr:
        y = soxr.resample(y, file_sr, sr, quality="HQ")
    return np.clip(y, -1, 1).astype(np.float32)


@torch.no_grad()
def sliding_predict(model, y: np.ndarray, classes: list[str], sr: int, win_s: float = 1.0,
                    hop_s: float = 0.5, batch: int = 256) -> pd.DataFrame:
    """One row per window: start_s, end_s, mid_s, pred, p_<class>.
    The Hamming taper (if configured) is applied inside model.feat, same as training."""
    n, h = int(round(win_s * sr)), int(round(hop_s * sr))
    if len(y) < n:
        y = np.pad(y, (0, n - len(y)))
    starts = np.arange(0, len(y) - n + 1, h)
    if starts[-1] + n < len(y):                          # cover the tail too
        starts = np.append(starts, len(y) - n)
    dev = next(model.parameters()).device
    probs = []
    for i in range(0, len(starts), batch):
        idx = starts[i:i + batch, None] + np.arange(n)[None, :]
        x = torch.from_numpy(y[idx]).to(dev)
        probs.append(torch.softmax(model(x), dim=1).cpu().numpy())
    p = np.concatenate(probs)
    df = pd.DataFrame({"start_s": starts / sr, "end_s": (starts + n) / sr})
    df["mid_s"] = (df.start_s + df.end_s) / 2
    df["pred"] = [classes[i] for i in p.argmax(1)]
    for j, c in enumerate(classes):
        df[f"p_{c}"] = p[:, j].round(4)
    return df


def list_runs(runs_dir: Path) -> pd.DataFrame:
    rows = []
    for m in sorted(Path(runs_dir).glob("*/*/metrics.json")):
        if m.parts[-3].startswith("_"):            # runs/_archive... is not a live run
            continue
        d = json.loads(m.read_text())
        t, v = d.get("test", {}), d.get("val_best", {})
        rows.append({"run": str(m.parent.relative_to(runs_dir)), "best_epoch": d.get("best_epoch"),
                     "val_macro_f1": v.get("macro_f1"), "test_acc": t.get("accuracy"),
                     "test_macro_f1": t.get("macro_f1"), "test_cough_recall": t.get("cough_recall"),
                     "test_cough_f1": t.get("cough_f1"), "path": str(m.parent)})
    return pd.DataFrame(rows)
