"""Runtime Entry AI inference with permanently zero execution authority."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Tuple

from ai.entry_model import EntryAIModel
from ai.feature_schema import CandidateFeatureBuilder, FEATURE_SCHEMA_VERSION


class ShadowEntryAI:
    execution_authority = False

    def __init__(self, model_path: str | None = None, *, accept_probability: float = 0.60,
                 min_expected_r: float = 0.20, uncertain_band: float = 0.05):
        self.model_path = Path(model_path) if model_path else None
        self.accept_probability = accept_probability
        self.min_expected_r = min_expected_r
        self.uncertain_band = uncertain_band
        self.builder = CandidateFeatureBuilder()
        self._model: Optional[EntryAIModel] = None
        self._model_error: Optional[str] = None
        self._last_key: Optional[Tuple[int, str, str, str]] = None
        self._last_result: Optional[Dict[str, Any]] = None

    @staticmethod
    def unavailable(reason: str = "MODEL_MISSING", *, status: str = "UNAVAILABLE") -> Dict[str, Any]:
        return {"status": status, "decision": "AI_UNCERTAIN", "success_probability": None,
                "expected_r_24": None, "model_version": None, "schema_version": FEATURE_SCHEMA_VERSION,
                "observed_at": int(time.time() * 1000), "reasons": [reason], "execution_authority": False}

    def _load(self) -> Optional[EntryAIModel]:
        if self._model is not None:
            return self._model
        if self.model_path is None or not self.model_path.is_file():
            self._model_error = "MODEL_MISSING"
            return None
        try:
            self._model = EntryAIModel.load(self.model_path)
            return self._model
        except ValueError as exc:
            self._model_error = str(exc)
        except Exception:
            self._model_error = "MODEL_CORRUPT"
        return None

    def evaluate(self, snapshot: Mapping[str, Any], *, candle_closed: bool = True) -> Dict[str, Any]:
        if not candle_closed:
            return self.unavailable("OPEN_5M_CANDLE", status="DEGRADED")
        try:
            key = self.builder.candidate_key(snapshot)
            if key == self._last_key and self._last_result is not None:
                return dict(self._last_result)
            row = self.builder.build(snapshot)
        except ValueError as exc:
            return self.unavailable(str(exc).upper().replace(" ", "_"), status="DEGRADED")
        model = self._load()
        if model is None:
            result = self.unavailable(self._model_error or "MODEL_MISSING")
        else:
            try:
                prediction = model.predict(row)
                probability, expected_r = prediction["success_probability"], prediction["expected_r_24"]
                if abs(probability - self.accept_probability) <= self.uncertain_band:
                    decision, reasons = "AI_UNCERTAIN", ["PROBABILITY_IN_UNCERTAIN_BAND"]
                elif probability >= self.accept_probability and expected_r >= self.min_expected_r:
                    decision, reasons = "AI_ACCEPT", ["SHADOW_THRESHOLDS_MET"]
                else:
                    decision, reasons = "AI_VETO", ["SHADOW_THRESHOLDS_NOT_MET"]
                result = {"status": "AVAILABLE", "decision": decision,
                          "success_probability": probability, "expected_r_24": expected_r,
                          "model_version": model.metadata.get("model_version"),
                          "schema_version": model.metadata.get("schema_version"),
                          "observed_at": int(time.time() * 1000), "reasons": reasons,
                          "execution_authority": False}
            except Exception:
                result = self.unavailable("PREDICTION_ERROR", status="DEGRADED")
        self._last_key, self._last_result = key, dict(result)
        return result
