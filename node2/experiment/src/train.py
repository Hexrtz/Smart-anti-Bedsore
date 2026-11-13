"""Train a cough / speech / background classifier on log-mel features.

Usage (run from node2/experiment):
    uv run python src/train.py --config configs/baseline_mlp.yaml
    uv run python src/train.py --config configs/cnn.yaml --set train.epochs=5 --set model.dropout=0.1

Reads data/clips.csv + data/processed (from `make process`).
Every epoch prints train loss and val accuracy / macro F1 / cough recall / cough F1.
The best epoch (by train.select_metric on val) is kept and scored on test.

Output: runs/<config name>/<YYYYmmdd-HHMMSS>/
  model.pt       state_dict + config + classes (everything infer.load_run needs)
  config.json    the exact config used (after --set) + data / code / environment info
  model.json     classifier, parameter count, size, input shape, architecture
  metrics.json   best epoch, val and test scores, per class, confusion matrices
  history.csv    one row per epoch
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import platform
import random
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
import torch
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
from coughml.data import Split, augment, load_splits, to_float  # noqa: E402
from coughml.metrics import evaluate  # noqa: E402
from coughml.models import build_model, describe  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]  # node2/experiment


def apply_overrides(cfg: dict, sets: list[str]) -> dict:
    """--set a.b.c=value, value parsed as YAML (numbers, lists, true/false)."""
    for s in sets:
        key, _, val = s.partition("=")
        node = cfg
        *parents, leaf = key.split(".")
        for k in parents:
            node = node.setdefault(k, {})
        node[leaf] = yaml.safe_load(val)
    return cfg


def seed_all(seed: int) -> None:
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    torch.cuda.manual_seed_all(seed)


def git_info() -> dict:
    def run(*a):
        return subprocess.run(["git", *a], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    return {"commit": run("rev-parse", "--short", "HEAD"), "dirty": bool(run("status", "--porcelain", "."))}


@torch.no_grad()
def predict(model, split: Split, clip_s: float, device, batch: int) -> np.ndarray:
    model.eval()
    rng = np.random.default_rng(0)
    out = []
    for i in range(0, len(split), batch):
        idx = np.arange(i, min(i + batch, len(split)))
        x = to_float(split.windows(idx, clip_s, train=False, rng=rng), device)
        out.append(model(x).argmax(1).cpu().numpy())
    return np.concatenate(out)


@torch.no_grad()
def fit_normaliser(model, split: Split, clip_s: float, device, batch: int) -> None:
    """Per-mel-bin mean/std of training log-mel (centred windows, no augmentation)."""
    rng = np.random.default_rng(0)
    s = s2 = n = 0
    for i in range(0, len(split), batch):
        idx = np.arange(i, min(i + batch, len(split)))
        f = model.feat.raw(to_float(split.windows(idx, clip_s, train=False, rng=rng), device))
        s = s + f.sum(dim=(0, 2))
        s2 = s2 + f.pow(2).sum(dim=(0, 2))
        n += f.shape[0] * f.shape[2]
    mean = s / n
    std = (s2 / n - mean.pow(2)).clamp_min(1e-8).sqrt()
    model.feat.mean.copy_(mean[:, None])
    model.feat.std.copy_(std[:, None])


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", type=Path, required=True)
    ap.add_argument("--set", action="append", default=[], metavar="KEY=VALUE", help="override a config value")
    args = ap.parse_args()

    cfg = apply_overrides(yaml.safe_load(args.config.read_text()), args.set)
    seed_all(cfg["seed"])
    tr, fc, dc = cfg["train"], cfg["features"], cfg["data"]
    classes = dc["classes"]
    device = torch.device(tr.get("device", "cuda") if torch.cuda.is_available() else "cpu")
    clip_s, sr = fc["clip_s"], fc["sample_rate"]

    clips_csv, processed = ROOT / dc["clips_csv"], ROOT / dc["processed_dir"]
    if not clips_csv.exists():
        print(f"{clips_csv} not found, run `make process` first", file=sys.stderr)
        return 1
    t0 = time.time()
    splits = load_splits(clips_csv, processed, classes, sr)
    train, val, test = splits["train"], splits["val"], splits["test"]
    print(f"loaded clips in {time.time() - t0:.0f} s: train {len(train)}, val {len(val)}, test {len(test)}"
          f"  on {device}", flush=True)

    model = build_model(cfg, len(classes)).to(device)
    fit_normaliser(model, train, clip_s, device, tr["batch_size"])
    info = describe(model)
    print(f"{info['classifier']}: {info['params']:,} params, {info['size_kb_int8']} KB at int8", flush=True)

    counts = np.bincount(train.y, minlength=len(classes))
    weight = None
    if tr.get("class_weights") == "balanced":
        weight = torch.tensor(counts.sum() / (len(classes) * np.maximum(counts, 1)), dtype=torch.float32,
                              device=device)
    loss_fn = torch.nn.CrossEntropyLoss(weight=weight, label_smoothing=tr.get("label_smoothing", 0.0))
    opt = torch.optim.AdamW(model.parameters(), lr=tr["lr"], weight_decay=tr["weight_decay"])
    steps = tr["epochs"] * int(np.ceil(len(train) / tr["batch_size"]))
    sched = torch.optim.lr_scheduler.OneCycleLR(opt, max_lr=tr["lr"], total_steps=steps)

    aug = cfg.get("augment", {})
    bg_idx = np.flatnonzero(train.y == classes.index("background")) if "background" in classes else []
    rng = np.random.default_rng(cfg["seed"])
    gen = torch.Generator(device=device).manual_seed(cfg["seed"])
    metric = tr.get("select_metric", "macro_f1")
    best, best_state, best_epoch, bad, history = -1.0, None, 0, 0, []

    for epoch in range(1, tr["epochs"] + 1):
        model.train()
        t_ep, total, nb = time.time(), 0.0, 0
        order = rng.permutation(len(train))
        for i in range(0, len(order), tr["batch_size"]):
            idx = order[i:i + tr["batch_size"]]
            x = to_float(train.windows(idx, clip_s, train=aug.get("random_crop", True), rng=rng), device)
            noise = None
            if len(bg_idx) and aug.get("noise_prob", 0) > 0:
                nidx = rng.choice(bg_idx, size=len(idx))
                noise = to_float(train.windows(nidx, clip_s, train=True, rng=rng), device)
            x = augment(x, noise, aug, gen)
            y = torch.from_numpy(train.y[idx]).to(device)
            loss = loss_fn(model(x), y)
            opt.zero_grad(set_to_none=True)
            loss.backward()
            opt.step()
            sched.step()
            total += loss.item()
            nb += 1

        v = evaluate(val.y, predict(model, val, clip_s, device, tr["batch_size"]), classes)
        row = {"epoch": epoch, "train_loss": round(total / nb, 4), "lr": round(sched.get_last_lr()[0], 6),
               **{f"val_{k}": v[k] for k in ("accuracy", "macro_f1", "cough_recall", "cough_precision", "cough_f1")},
               "seconds": round(time.time() - t_ep, 1)}
        history.append(row)
        flag = ""
        if v[metric] > best:
            best, best_epoch, bad = v[metric], epoch, 0
            best_state = copy.deepcopy(model.state_dict())
            flag = "  *best"
        else:
            bad += 1
        print(f"epoch {epoch:3d}  loss {row['train_loss']:.4f}  val acc {v['accuracy']:.4f}  "
              f"macroF1 {v['macro_f1']:.4f}  cough R {v['cough_recall']:.4f}  cough F1 {v['cough_f1']:.4f}"
              f"  ({row['seconds']} s){flag}", flush=True)
        if bad >= tr.get("early_stop_patience", 10**9):
            print(f"early stop: no {metric} gain for {bad} epochs")
            break

    model.load_state_dict(best_state)
    val_best = evaluate(val.y, predict(model, val, clip_s, device, tr["batch_size"]), classes)
    test_m = evaluate(test.y, predict(model, test, clip_s, device, tr["batch_size"]), classes)

    run_dir = ROOT / "runs" / cfg["name"] / datetime.now().strftime("%Y%m%d-%H%M%S")
    run_dir.mkdir(parents=True)
    torch.save({"state_dict": model.state_dict(), "config": cfg, "classes": classes}, run_dir / "model.pt")
    meta = pd.read_csv(clips_csv)
    run_cfg = {
        "config": cfg,
        "config_file": str(args.config),
        "overrides": args.set,
        "data": {
            "clips_csv_md5": hashlib.md5(clips_csv.read_bytes()).hexdigest(),
            "clips": pd.crosstab(meta["label"], meta["split"]).to_dict(),
            "datasets": sorted(meta["dataset"].unique()),
            "normaliser": {"mean": model.feat.mean.squeeze(1).tolist(), "std": model.feat.std.squeeze(1).tolist()},
        },
        "git": git_info(),
        "env": {"python": platform.python_version(), "torch": torch.__version__, "device": str(device),
                "gpu": torch.cuda.get_device_name(0) if device.type == "cuda" else None},
        "created": datetime.now().isoformat(timespec="seconds"),
    }
    (run_dir / "config.json").write_text(json.dumps(run_cfg, indent=2, ensure_ascii=False))
    (run_dir / "model.json").write_text(json.dumps(info, indent=2))
    (run_dir / "metrics.json").write_text(json.dumps(
        {"select_metric": metric, "best_epoch": best_epoch, "epochs_run": len(history),
         "val_best": val_best, "test": test_m}, indent=2))
    pd.DataFrame(history).to_csv(run_dir / "history.csv", index=False)

    print(f"\nbest epoch {best_epoch} ({metric} {best:.4f})")
    for name, m in (("val", val_best), ("test", test_m)):
        print(f"{name:5s} acc {m['accuracy']:.4f}  macroF1 {m['macro_f1']:.4f}  "
              f"cough recall {m['cough_recall']:.4f}  precision {m['cough_precision']:.4f}  F1 {m['cough_f1']:.4f}")
    print("test confusion (rows true, cols pred):", classes)
    for c, r in zip(classes, test_m["confusion_matrix"]["rows_true_cols_pred"]):
        print(f"  {c:10s} {r}")
    print(f"saved {run_dir.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
