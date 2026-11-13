"""Load processed clips into RAM and cut 1 s training windows from them.

Each processed clip is longer than the training window (default 2 s for a 1 s
window). val/test take the window centred at center_s. Train takes a random
window: for cough it must still contain the whole cough event, for other
labels anywhere inside the real (non-padded) audio. That is the time-shift
augmentation, done without padding silence.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd
import soundfile as sf
import torch

TIME_COLS = ["center_s", "event_start_s", "event_end_s", "valid_start_s", "valid_end_s"]


class Split:
    """All clips of one split as an int16 array (n, clip_samples) plus metadata."""

    def __init__(self, meta: pd.DataFrame, audio: np.ndarray, classes: list[str], sr: int):
        self.meta = meta.reset_index(drop=True)
        self.audio = audio
        self.sr = sr
        self.total_s = audio.shape[1] / sr
        self.y = self.meta["label"].map({c: i for i, c in enumerate(classes)}).to_numpy()
        self.t = {c: self.meta[c].to_numpy(dtype=np.float64) for c in TIME_COLS}
        self.is_cough = (self.meta["label"] == "cough").to_numpy()

    def __len__(self) -> int:
        return len(self.y)

    def starts(self, idx: np.ndarray, clip_s: float, train: bool, rng: np.random.Generator) -> np.ndarray:
        """Window start (seconds) for each clip in idx."""
        t = {k: v[idx] for k, v in self.t.items()}
        hi_lim = self.total_s - clip_s
        centred = np.clip(t["center_s"] - clip_s / 2, 0, hi_lim)
        if not train:
            return centred
        cough = self.is_cough[idx]
        lo = np.where(cough, np.maximum(t["event_end_s"] - clip_s, t["valid_start_s"]), t["valid_start_s"])
        hi = np.where(cough, np.minimum(t["event_start_s"], t["valid_end_s"] - clip_s), t["valid_end_s"] - clip_s)
        lo, hi = np.clip(lo, 0, hi_lim), np.clip(hi, 0, hi_lim)
        s = lo + rng.random(len(idx)) * np.maximum(hi - lo, 0)
        return np.where(lo <= hi, s, centred)          # event longer than the window: stay centred

    def windows(self, idx: np.ndarray, clip_s: float, train: bool, rng: np.random.Generator) -> np.ndarray:
        """int16 array (len(idx), clip_samples)."""
        n = int(round(clip_s * self.sr))
        s = np.round(self.starts(idx, clip_s, train, rng) * self.sr).astype(np.int64)
        s = np.clip(s, 0, self.audio.shape[1] - n)
        return self.audio[idx[:, None], s[:, None] + np.arange(n)[None, :]]


def load_splits(clips_csv: Path, processed_dir: Path, classes: list[str], sr: int,
                workers: int = 16) -> dict[str, Split]:
    meta = pd.read_csv(clips_csv)
    unknown = set(meta["label"]) - set(classes)
    if unknown:
        raise ValueError(f"labels {unknown} in {clips_csv} are not in config classes {classes}")

    def read(p: str) -> np.ndarray:
        y, file_sr = sf.read(str(processed_dir / p), dtype="int16")
        assert file_sr == sr, f"{p}: {file_sr} Hz, expected {sr}"
        return y

    with ThreadPoolExecutor(workers) as pool:
        waves = list(pool.map(read, meta["clip_path"]))
    lengths = {len(w) for w in waves}
    if len(lengths) != 1:
        raise ValueError(f"clips have different lengths {sorted(lengths)[:5]}; re-run make process")
    audio = np.stack(waves)
    out = {}
    for name in ("train", "val", "test"):
        mask = (meta["split"] == name).to_numpy()
        out[name] = Split(meta[mask], audio[mask], classes, sr)
    return out


def to_float(x: np.ndarray, device: torch.device) -> torch.Tensor:
    return torch.from_numpy(x).to(device, non_blocking=True).float() / 32768.0


def augment(x: torch.Tensor, noise: torch.Tensor | None, cfg: dict, gen: torch.Generator) -> torch.Tensor:
    """Random gain, then mix a background window at a random SNR (on the GPU)."""
    b = x.shape[0]
    dev = x.device
    if cfg.get("gain_db", 0):
        g = (torch.rand(b, 1, device=dev, generator=gen) * 2 - 1) * cfg["gain_db"]
        x = x * 10 ** (g / 20)
    if noise is not None and cfg.get("noise_prob", 0) > 0:
        use = torch.rand(b, 1, device=dev, generator=gen) < cfg["noise_prob"]
        lo, hi = cfg["snr_db"]
        snr = lo + torch.rand(b, 1, device=dev, generator=gen) * (hi - lo)
        sig = x.pow(2).mean(1, keepdim=True).clamp_min(1e-10)
        nz = noise.pow(2).mean(1, keepdim=True).clamp_min(1e-10)
        scale = torch.sqrt(sig / (nz * 10 ** (snr / 10)))
        x = x + torch.where(use, scale, torch.zeros_like(scale)) * noise
    return x.clamp(-1, 1)
