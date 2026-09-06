"""Small deterministic gradient-boosted decision-stump model.

XGBoost is intentionally not required by this repository. This pure-NumPy
implementation keeps Render installation small and deterministic while retaining
a tree-based gradient boosting model for both requested outputs.
"""

from __future__ import annotations

import json
import math
import platform
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

import numpy as np

from ai.feature_schema import CATEGORICAL_FEATURES, FEATURE_COLUMNS, FEATURE_SCHEMA_VERSION


MODEL_VERSION = "entry-ai-v1-gbstump"
RANDOM_SEED = 20260906


def _git_sha() -> Optional[str]:
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], text=True, stderr=subprocess.DEVNULL).strip()
    except Exception:
        return None


class FeatureEncoder:
    def __init__(self):
        self.categories: Dict[str, List[str]] = {}
        self.output_features: List[str] = []

    def fit(self, rows: Sequence[Mapping[str, Any]]) -> "FeatureEncoder":
        self.categories = {
            name: sorted({str(row.get(name, "UNAVAILABLE")) for row in rows})
            for name in CATEGORICAL_FEATURES
        }
        self.output_features = []
        for name in FEATURE_COLUMNS:
            if name in self.categories:
                self.output_features.extend(f"{name}={value}" for value in self.categories[name])
            else:
                self.output_features.extend((name, f"{name}__missing"))
        return self

    def transform(self, rows: Sequence[Mapping[str, Any]]) -> np.ndarray:
        values: List[List[float]] = []
        for row in rows:
            encoded: List[float] = []
            for name in FEATURE_COLUMNS:
                if name in self.categories:
                    current = str(row.get(name, "UNAVAILABLE"))
                    encoded.extend(float(current == category) for category in self.categories[name])
                else:
                    try:
                        value = float(row.get(name))
                        valid = math.isfinite(value)
                    except (TypeError, ValueError):
                        value, valid = 0.0, False
                    encoded.extend((value if valid else 0.0, 0.0 if valid else 1.0))
            values.append(encoded)
        return np.asarray(values, dtype=float)

    def to_dict(self) -> Dict[str, Any]:
        return {"categories": self.categories, "output_features": self.output_features}

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "FeatureEncoder":
        obj = cls()
        obj.categories = {str(k): list(v) for k, v in (payload.get("categories") or {}).items()}
        obj.output_features = list(payload.get("output_features") or [])
        return obj


@dataclass
class Stump:
    feature: int
    threshold: float
    left: float
    right: float

    def predict(self, matrix: np.ndarray) -> np.ndarray:
        return np.where(matrix[:, self.feature] <= self.threshold, self.left, self.right)


class GradientStumpEnsemble:
    def __init__(self, *, task: str, estimators: int = 40, learning_rate: float = 0.08):
        self.task = task
        self.estimators = estimators
        self.learning_rate = learning_rate
        self.base = 0.0
        self.stumps: List[Stump] = []

    @staticmethod
    def _fit_stump(matrix: np.ndarray, residual: np.ndarray) -> Stump:
        best: Optional[Tuple[float, Stump]] = None
        for feature in range(matrix.shape[1]):
            column = matrix[:, feature]
            unique = np.unique(column)
            thresholds = unique if len(unique) <= 16 else np.quantile(unique, np.linspace(0.05, 0.95, 15))
            for threshold in thresholds:
                mask = column <= threshold
                if not mask.any() or mask.all():
                    continue
                left, right = float(residual[mask].mean()), float(residual[~mask].mean())
                prediction = np.where(mask, left, right)
                loss = float(np.mean((residual - prediction) ** 2))
                candidate = Stump(feature, float(threshold), left, right)
                if best is None or loss < best[0]:
                    best = loss, candidate
        return best[1] if best else Stump(0, 0.0, float(residual.mean()), float(residual.mean()))

    def fit(self, matrix: np.ndarray, target: np.ndarray) -> "GradientStumpEnsemble":
        if self.task == "classifier":
            mean = float(np.clip(target.mean(), 1e-5, 1 - 1e-5))
            self.base = math.log(mean / (1 - mean))
        else:
            self.base = float(target.mean())
        raw = np.full(len(target), self.base, dtype=float)
        self.stumps = []
        for _ in range(self.estimators):
            if self.task == "classifier":
                probability = 1.0 / (1.0 + np.exp(-np.clip(raw, -30, 30)))
                residual = target - probability
            else:
                residual = target - raw
            stump = self._fit_stump(matrix, residual)
            raw += self.learning_rate * stump.predict(matrix)
            self.stumps.append(stump)
        return self

    def predict(self, matrix: np.ndarray) -> np.ndarray:
        raw = np.full(matrix.shape[0], self.base, dtype=float)
        for stump in self.stumps:
            raw += self.learning_rate * stump.predict(matrix)
        if self.task == "classifier":
            return 1.0 / (1.0 + np.exp(-np.clip(raw, -30, 30)))
        return raw

    def to_dict(self) -> Dict[str, Any]:
        return {"task": self.task, "estimators": self.estimators, "learning_rate": self.learning_rate,
                "base": self.base, "stumps": [s.__dict__ for s in self.stumps]}

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "GradientStumpEnsemble":
        obj = cls(task=str(payload["task"]), estimators=int(payload["estimators"]), learning_rate=float(payload["learning_rate"]))
        obj.base = float(payload["base"])
        obj.stumps = [Stump(**item) for item in payload.get("stumps", [])]
        return obj


class EntryAIModel:
    def __init__(self):
        self.encoder = FeatureEncoder()
        self.classifier = GradientStumpEnsemble(task="classifier")
        self.regressor = GradientStumpEnsemble(task="regressor")
        self.metadata: Dict[str, Any] = {}

    def fit(self, rows: Sequence[Mapping[str, Any]]) -> "EntryAIModel":
        if len(rows) < 8:
            raise ValueError("at least 8 chronological rows are required")
        matrix = self.encoder.fit(rows).transform(rows)
        classifier_target = np.asarray([float(row["ENTRY_SUCCESS_24"]) for row in rows])
        regression_target = np.asarray([float(row["EXPECTED_R_24"]) for row in rows])
        self.classifier.fit(matrix, classifier_target)
        self.regressor.fit(matrix, regression_target)
        train_probability = self.classifier.predict(matrix)
        train_expected_r = self.regressor.predict(matrix)
        timestamps = [int(row["timestamp"]) for row in rows]
        self.metadata = {
            "model_version": MODEL_VERSION, "schema_version": FEATURE_SCHEMA_VERSION,
            "training_start": min(timestamps), "training_end": max(timestamps),
            "feature_list": list(FEATURE_COLUMNS), "encoded_feature_list": self.encoder.output_features,
            "training_row_count": len(rows), "git_commit": _git_sha(),
            "metrics": {
                "training_brier": float(np.mean((train_probability - classifier_target) ** 2)),
                "training_regression_mae": float(np.mean(np.abs(train_expected_r - regression_target))),
                "note": "Training diagnostics only; claims require chronological out-of-sample metrics.",
            },
            "random_seed": RANDOM_SEED, "library_versions": {"numpy": np.__version__, "python": platform.python_version()},
        }
        return self

    def predict(self, row: Mapping[str, Any]) -> Dict[str, float]:
        if self.metadata.get("schema_version") != FEATURE_SCHEMA_VERSION:
            raise ValueError("FEATURE_SCHEMA_MISMATCH")
        matrix = self.encoder.transform([row])
        return {"success_probability": float(self.classifier.predict(matrix)[0]),
                "expected_r_24": float(self.regressor.predict(matrix)[0])}

    def save(self, path: str | Path) -> None:
        payload = {"metadata": self.metadata, "encoder": self.encoder.to_dict(),
                   "classifier": self.classifier.to_dict(), "regressor": self.regressor.to_dict()}
        Path(path).write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")

    @classmethod
    def load(cls, path: str | Path) -> "EntryAIModel":
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        obj = cls()
        obj.metadata = dict(payload["metadata"])
        if obj.metadata.get("schema_version") != FEATURE_SCHEMA_VERSION:
            raise ValueError("FEATURE_SCHEMA_MISMATCH")
        obj.encoder = FeatureEncoder.from_dict(payload["encoder"])
        obj.classifier = GradientStumpEnsemble.from_dict(payload["classifier"])
        obj.regressor = GradientStumpEnsemble.from_dict(payload["regressor"])
        return obj
