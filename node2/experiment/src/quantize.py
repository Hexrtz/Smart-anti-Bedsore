"""Post-training int8 quantization of a trained run, and how much it costs on val.

PyTorch eager-mode static quantization (x86 engine): weights int8 per channel,
activations int8 per tensor, calibrated on training windows. The log-mel
front end stays float (on the ESP32 it is computed before the network).
This approximates the TFLite int8 model the board will run; the exact number
for the device comes after conversion to TFLite.

Usage (run from node2/experiment):
    uv run python src/quantize.py                                 # latest run of every config
    uv run python src/quantize.py --run runs/cnn/20261112-175155
    uv run python src/quantize.py --calib 4096

Writes into each run folder:
  quant.json       float vs int8 on val (and test): accuracy, macro F1, cough
                   recall/F1, the drop in percentage points, prediction
                   agreement, model size
  model_int8.pt    quantized classifier as TorchScript (input: normalised log-mel)
"""

from __future__ import annotations

import argparse
import copy
import warnings
import io
import json
import sys
from pathlib import Path

import numpy as np
import torch
from torch import nn
from torch.ao import quantization as tq

sys.path.insert(0, str(Path(__file__).resolve().parent))
from coughml.data import load_splits, to_float  # noqa: E402
from coughml.infer import load_run  # noqa: E402
from coughml.metrics import evaluate  # noqa: E402

# torch.ao eager quantization works but warns that it moves to torchao; keep the output readable
warnings.filterwarnings("ignore", module=r"torch\.ao\..*")
warnings.filterwarnings("ignore", message=r".*torchao.*")
warnings.filterwarnings("ignore", category=FutureWarning, module=r"torch\.jit\..*")
warnings.filterwarnings("ignore", message=r".*quantize_per_channel.*|.*_aminmax.*|.*reduce_range.*")

ROOT = Path(__file__).resolve().parents[1]  # node2/experiment
KEYS = ("accuracy", "macro_f1", "cough_recall", "cough_precision", "cough_f1")


def latest_runs() -> list[Path]:
    out = []
    for cfg_dir in sorted(p for p in (ROOT / "runs").iterdir() if p.is_dir() and not p.name.startswith("_")):
        runs = sorted(d for d in cfg_dir.iterdir() if (d / "model.pt").exists())
        if runs:
            out.append(runs[-1])
    return out


def fuse_plan(seq: nn.Sequential, prefix: str) -> list[list[str]]:
    """Conv-BN-ReLU, Linear-BN (ReLU stays separate) inside one Sequential."""
    mods, plan, i = list(seq.children()), [], 0
    while i < len(mods):
        a = mods[i]
        b = mods[i + 1] if i + 1 < len(mods) else None
        c = mods[i + 2] if i + 2 < len(mods) else None
        if isinstance(a, nn.Conv2d) and isinstance(b, nn.BatchNorm2d) and isinstance(c, nn.ReLU):
            plan.append([f"{prefix}{i}", f"{prefix}{i + 1}", f"{prefix}{i + 2}"]); i += 3
        elif isinstance(a, nn.Linear) and isinstance(b, nn.BatchNorm1d):
            plan.append([f"{prefix}{i}", f"{prefix}{i + 1}"]); i += 2
        else:
            i += 1
    return plan


def quantize(net: nn.Module, calib: torch.Tensor, engine: str) -> nn.Module:
    torch.backends.quantized.engine = engine
    q = tq.QuantWrapper(copy.deepcopy(net)).eval()
    plan = []
    for name, m in q.module.named_children():
        if isinstance(m, nn.Sequential):
            plan += fuse_plan(m, f"module.{name}.")
            for sub_name, sub in m.named_children():  # MLP keeps its layers in net.net
                if isinstance(sub, nn.Sequential):
                    plan += fuse_plan(sub, f"module.{name}.{sub_name}.")
    if plan:
        tq.fuse_modules(q, plan, inplace=True)
    q.qconfig = tq.get_default_qconfig(engine)
    tq.prepare(q, inplace=True)
    with torch.no_grad():
        for i in range(0, len(calib), 256):
            q(calib[i:i + 256])
    tq.convert(q, inplace=True)
    return q


def size_kb(m: nn.Module) -> float:
    buf = io.BytesIO()
    torch.save(m.state_dict(), buf)
    return round(buf.getbuffer().nbytes / 1024, 1)


@torch.no_grad()
def features(model, split, clip_s: float) -> torch.Tensor:
    rng = np.random.default_rng(0)
    out = []
    for i in range(0, len(split), 512):
        idx = np.arange(i, min(i + 512, len(split)))
        out.append(model.feat(to_float(split.windows(idx, clip_s, train=False, rng=rng), torch.device("cpu"))))
    return torch.cat(out)


@torch.no_grad()
def preds(net: nn.Module, x: torch.Tensor) -> np.ndarray:
    return torch.cat([net(x[i:i + 512]).argmax(1) for i in range(0, len(x), 512)]).numpy()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run", type=Path, action="append", help="run folder (repeatable); default: latest per config")
    ap.add_argument("--calib", type=int, default=2048, help="training windows used for calibration")
    ap.add_argument("--engine", default="x86", help="x86 | fbgemm | qnnpack")
    args = ap.parse_args()

    runs = [r if r.is_absolute() else ROOT / r for r in (args.run or latest_runs())]
    if not runs:
        print("no runs found, run `make train` first", file=sys.stderr)
        return 1
    torch.set_num_threads(8)
    cache = {}
    for run in runs:
        model, cfg, classes = load_run(run, "cpu")
        key = (cfg["data"]["clips_csv"], cfg["data"]["processed_dir"])
        if key not in cache:
            cache[key] = load_splits(ROOT / key[0], ROOT / key[1], classes, cfg["features"]["sample_rate"])
        splits = cache[key]
        clip_s = cfg["features"]["clip_s"]

        rng = np.random.default_rng(cfg["seed"])
        tr = splits["train"]
        cidx = np.sort(rng.choice(len(tr), size=min(args.calib, len(tr)), replace=False))
        calib = model.feat(to_float(tr.windows(cidx, clip_s, train=False, rng=rng), torch.device("cpu")))
        qnet = quantize(model.net, calib, args.engine)

        report = {"engine": args.engine, "calibration_windows": len(cidx),
                  "size_kb": {"float32": size_kb(model.net), "int8": size_kb(qnet)}}
        for split in ("val", "test"):
            x = features(model, splits[split], clip_s)
            y = splits[split].y
            pf, pq = preds(model.net.eval(), x), preds(qnet, x)
            mf, mq = evaluate(y, pf, classes), evaluate(y, pq, classes)
            report[split] = {
                "float32": {k: mf[k] for k in KEYS},
                "int8": {k: mq[k] for k in KEYS},
                "drop_pp": {k: round((mf[k] - mq[k]) * 100, 2) for k in KEYS},
                "prediction_agreement": round(float((pf == pq).mean()), 4),
                "int8_confusion": mq["confusion_matrix"],
            }
        (run / "quant.json").write_text(json.dumps(report, indent=2))
        try:
            ts = torch.jit.trace(qnet, calib[:1])
            ts.save(str(run / "model_int8.pt"))
        except Exception as e:  # noqa: BLE001  saving is optional, the numbers are the point
            print(f"   (could not save TorchScript: {e})")

        v = report["val"]
        print(f"\n{run.relative_to(ROOT)}  [{cfg['model']['name']}]  "
              f"{report['size_kb']['float32']} KB -> {report['size_kb']['int8']} KB")
        print(f"   {'val':6s}{'float32':>10s}{'int8':>10s}{'drop (pp)':>11s}")
        for k in KEYS:
            print(f"   {k:16s}{v['float32'][k]:>10.4f}{v['int8'][k]:>10.4f}{v['drop_pp'][k]:>11.2f}")
        print(f"   prediction agreement float vs int8: {v['prediction_agreement']:.4f}"
              f"   (test acc drop {report['test']['drop_pp']['accuracy']:.2f} pp)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
