"""Classifiers on top of log-mel. build_model() picks one from the config."""

from __future__ import annotations

import torch
from torch import nn

from .features import LogMel


class MLP(nn.Module):
    """Baseline: flatten the whole log-mel image into one vector."""

    def __init__(self, n_mels: int, frames: int, n_classes: int, hidden: list[int], dropout: float):
        super().__init__()
        layers: list[nn.Module] = [nn.Flatten()]
        d = n_mels * frames
        for h in hidden:
            layers += [nn.Linear(d, h), nn.BatchNorm1d(h), nn.ReLU(), nn.Dropout(dropout)]
            d = h
        layers.append(nn.Linear(d, n_classes))
        self.net = nn.Sequential(*layers)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.net(x)


class CNN(nn.Module):
    """Small 2-D CNN: conv3x3-BN-ReLU-maxpool blocks, global average pool, linear."""

    def __init__(self, n_mels: int, frames: int, n_classes: int, channels: list[int], dropout: float):
        super().__init__()
        blocks: list[nn.Module] = []
        c_in = 1
        for c in channels:
            blocks += [nn.Conv2d(c_in, c, 3, padding=1, bias=False), nn.BatchNorm2d(c), nn.ReLU(),
                       nn.MaxPool2d(2)]
            c_in = c
        self.features = nn.Sequential(*blocks)
        self.head = nn.Sequential(nn.AdaptiveAvgPool2d(1), nn.Flatten(), nn.Dropout(dropout),
                                  nn.Linear(c_in, n_classes))

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.head(self.features(x.unsqueeze(1)))


class CoughModel(nn.Module):
    """waveform (batch, samples) -> logits. Features live inside the model, so a
    saved model.pt is everything inference needs."""

    def __init__(self, feat: LogMel, net: nn.Module):
        super().__init__()
        self.feat = feat
        self.net = net

    def forward(self, wave: torch.Tensor) -> torch.Tensor:
        return self.net(self.feat(wave))


def build_model(cfg: dict, n_classes: int) -> CoughModel:
    f = cfg["features"]
    feat = LogMel(f["sample_rate"], f["clip_s"], f["n_fft"], f["win_length"], f["hop_length"],
                  f["n_mels"], f["f_min"], f["f_max"], f.get("taper", "hamming"))
    m = cfg["model"]
    if m["name"] == "mlp":
        net = MLP(feat.n_mels, feat.frames, n_classes, m["hidden"], m["dropout"])
    elif m["name"] == "cnn":
        net = CNN(feat.n_mels, feat.frames, n_classes, m["channels"], m["dropout"])
    else:
        raise ValueError(f"unknown model {m['name']!r}")
    return CoughModel(feat, net)


def describe(model: CoughModel) -> dict:
    n = sum(p.numel() for p in model.net.parameters())
    return {
        "classifier": type(model.net).__name__,
        "params": n,
        "size_kb_float32": round(n * 4 / 1024, 1),
        "size_kb_int8": round(n / 1024, 1),
        "input": {"samples": model.feat.clip_samples, "log_mel": [model.feat.n_mels, model.feat.frames]},
        "architecture": str(model.net),
    }
