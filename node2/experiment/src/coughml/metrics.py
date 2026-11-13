"""Accuracy, macro F1, per-class precision/recall/F1, cough-vs-rest, confusion matrix."""

from __future__ import annotations

import numpy as np


def evaluate(y_true: np.ndarray, y_pred: np.ndarray, classes: list[str], positive: str = "cough") -> dict:
    k = len(classes)
    cm = np.zeros((k, k), dtype=int)                    # rows = true, cols = predicted
    np.add.at(cm, (y_true, y_pred), 1)
    tp = np.diag(cm).astype(float)
    prec = np.divide(tp, cm.sum(0), out=np.zeros(k), where=cm.sum(0) > 0)
    rec = np.divide(tp, cm.sum(1), out=np.zeros(k), where=cm.sum(1) > 0)
    f1 = np.divide(2 * prec * rec, prec + rec, out=np.zeros(k), where=(prec + rec) > 0)
    per_class = {c: {"precision": round(float(prec[i]), 4), "recall": round(float(rec[i]), 4),
                     "f1": round(float(f1[i]), 4), "support": int(cm[i].sum())}
                 for i, c in enumerate(classes)}
    p = classes.index(positive)
    return {
        "accuracy": round(float(tp.sum() / max(cm.sum(), 1)), 4),
        "macro_f1": round(float(f1.mean()), 4),
        "macro_recall": round(float(rec.mean()), 4),
        f"{positive}_recall": per_class[positive]["recall"],
        f"{positive}_precision": per_class[positive]["precision"],
        f"{positive}_f1": per_class[positive]["f1"],
        "per_class": per_class,
        "confusion_matrix": {"labels": classes, "rows_true_cols_pred": cm.tolist()},
    }
