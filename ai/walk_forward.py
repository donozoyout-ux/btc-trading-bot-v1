"""Chronological expanding-window evaluation; random splits are forbidden."""

from __future__ import annotations

import math
import datetime as dt
from typing import Any, Dict, List, Mapping, Sequence, Tuple

import numpy as np

from ai.entry_model import EntryAIModel


def chronological_folds(rows: Sequence[Mapping[str, Any]], *, folds: int = 3, min_train: int = 24) -> List[Tuple[List[int], List[int]]]:
    order = sorted(range(len(rows)), key=lambda i: int(rows[i]["timestamp"]))
    if len(order) <= min_train:
        return []
    step = max(1, (len(order) - min_train) // folds)
    result = []
    start = min_train
    while start < len(order) and len(result) < folds:
        # Never split candidates from the same closed candle across train/test.
        while start < len(order) and rows[order[start]]["timestamp"] == rows[order[start - 1]]["timestamp"]:
            start += 1
        end = min(start + step, len(order))
        while end < len(order) and rows[order[end]]["timestamp"] == rows[order[end - 1]]["timestamp"]:
            end += 1
        test = order[start:end]
        if test:
            train = order[:start]
            if max(int(rows[i]["timestamp"]) for i in train) >= min(int(rows[i]["timestamp"]) for i in test):
                raise AssertionError("chronological leakage")
            result.append((train, test))
        start = end
    return result


def _auc(y: np.ndarray, score: np.ndarray) -> float | None:
    pos, neg = int(y.sum()), int(len(y) - y.sum())
    if not pos or not neg:
        return None
    order = np.argsort(score)
    ranks = np.empty(len(score), dtype=float)
    ranks[order] = np.arange(1, len(score) + 1)
    return float((ranks[y == 1].sum() - pos * (pos + 1) / 2) / (pos * neg))


def _pr_auc(y: np.ndarray, score: np.ndarray) -> float | None:
    if not y.sum():
        return None
    order = np.argsort(-score)
    ranked = y[order]
    tp = np.cumsum(ranked)
    precision = tp / np.arange(1, len(y) + 1)
    recall = tp / y.sum()
    return float(np.sum((recall - np.r_[0.0, recall[:-1]]) * precision))


def _metrics(predictions: Sequence[Mapping[str, Any]], accept_probability: float,
             min_expected_r: float) -> Dict[str, Any]:
    y = np.asarray([float(item["row"]["ENTRY_SUCCESS_24"]) for item in predictions])
    p = np.asarray([item["success_probability"] for item in predictions])
    expected = np.asarray([item["expected_r_24"] for item in predictions])
    realized = np.asarray([float(item["row"]["EXPECTED_R_24"]) for item in predictions])
    accepted = (p >= accept_probability) & (expected >= min_expected_r)
    return {
        "rows": len(predictions), "roc_auc": _auc(y, p), "pr_auc": _pr_auc(y, p),
        "brier": float(np.mean((p - y) ** 2)),
        "precision": float(y[accepted].mean()) if accepted.any() else None,
        "recall": float(y[accepted].sum() / y.sum()) if y.sum() else None,
        "candidate_coverage": float(accepted.mean()),
        "rule_candidate_success_rate": float(y.mean()),
        "ai_shadow_accept_success_rate": float(y[accepted].mean()) if accepted.any() else None,
        "rule_candidate_avg_r": float(realized.mean()),
        "ai_shadow_accept_avg_r": float(realized[accepted].mean()) if accepted.any() else None,
    }


def evaluate_walk_forward(rows: Sequence[Mapping[str, Any]], *, accept_probability: float = 0.60,
                          min_expected_r: float = 0.20) -> Dict[str, Any]:
    predictions: List[Dict[str, Any]] = []
    folds = chronological_folds(rows)
    for fold_number, (train_idx, test_idx) in enumerate(folds, start=1):
        model = EntryAIModel().fit([rows[i] for i in train_idx])
        for index in test_idx:
            prediction = model.predict(rows[index])
            predictions.append({**prediction, "fold": fold_number, "row": rows[index]})
    if not predictions:
        return {"status": "NO_REAL_DATA", "fold_count": 0}
    result = {"status": "PASS", "fold_count": len(folds), "oos_rows": len(predictions),
              **_metrics(predictions, accept_probability, min_expected_r)}
    # Slice reporting is emitted only with enough OOS observations to avoid
    # presenting tiny, misleading samples as evidence.
    slice_fields = {
        "direction": "direction", "setup": "setup_type", "regime": "regime",
        "volatility": "volatility",
    }
    slices: Dict[str, Dict[str, Any]] = {}
    for section, field in slice_fields.items():
        groups: Dict[str, List[Mapping[str, Any]]] = {}
        for item in predictions:
            groups.setdefault(str(item["row"].get(field, "UNAVAILABLE")), []).append(item)
        slices[section] = {
            name: _metrics(group, accept_probability, min_expected_r)
            for name, group in groups.items() if len(group) >= 8
        }
    month_groups: Dict[str, List[Mapping[str, Any]]] = {}
    for item in predictions:
        month = dt.datetime.fromtimestamp(int(item["row"]["timestamp"]) / 1000, tz=dt.timezone.utc).strftime("%Y-%m")
        month_groups.setdefault(month, []).append(item)
    slices["month_utc"] = {
        name: _metrics(group, accept_probability, min_expected_r)
        for name, group in month_groups.items() if len(group) >= 8
    }
    result["slices"] = slices
    return result
