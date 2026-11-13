"""Log-mel features. The same module runs in training and in sliding-window
inference, so the taper and STFT settings can never drift apart."""

from __future__ import annotations

import torch
import torchaudio


class LogMel(torch.nn.Module):
    """(batch, samples) waveform -> (batch, n_mels, frames) log-mel, normalised.

    taper: "hamming" multiplies the whole 1 s window by a Hamming window before
    the STFT, so audio at the window edges fades in and out. With 50% overlap
    in sliding inference every moment still lands near the middle of some window.
    mean/std: per-mel-bin statistics from the training set (set by train.py).
    """

    def __init__(self, sample_rate: int, clip_s: float, n_fft: int, win_length: int, hop_length: int,
                 n_mels: int, f_min: float, f_max: float, taper: str = "hamming", eps: float = 1e-6):
        super().__init__()
        self.clip_samples = int(round(sample_rate * clip_s))
        self.mel = torchaudio.transforms.MelSpectrogram(
            sample_rate=sample_rate, n_fft=n_fft, win_length=win_length, hop_length=hop_length,
            f_min=f_min, f_max=f_max, n_mels=n_mels, center=False, power=2.0)
        if taper == "hamming":
            w = torch.hamming_window(self.clip_samples, periodic=False)
        elif taper in (None, "none"):
            w = torch.ones(self.clip_samples)
        else:
            raise ValueError(f"unknown taper {taper!r}")
        self.register_buffer("taper", w)
        self.register_buffer("mean", torch.zeros(n_mels, 1))
        self.register_buffer("std", torch.ones(n_mels, 1))
        self.eps = eps
        self.n_mels = n_mels
        self.frames = 1 + (self.clip_samples - n_fft) // hop_length  # center=False frames span n_fft

    def raw(self, x: torch.Tensor) -> torch.Tensor:
        """Log-mel without normalisation (used to compute mean/std)."""
        return torch.log(self.mel(x * self.taper) + self.eps)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return (self.raw(x) - self.mean) / self.std
