"""Volatility Engine measuring normalized ATR percentiles over the last 90 4H candles."""

import os
from typing import List, Tuple
import numpy as np
from core.models import Candle
from config.constants import VolatilityLevel


# Canonical volatility percentile boundaries (Phase 1 baseline). Overridable
# ONLY via environment for controlled Phase 2C sensitivity experiments;
# production defaults are unchanged when the variables are unset.
CANONICAL_VOL_LOW_MAX = 20.0
CANONICAL_VOL_HIGH_MIN = 80.0
CANONICAL_VOL_EXTREME_MIN = 95.0


def get_volatility_boundaries() -> Tuple[float, float, float]:
    """Returns (low_max, high_min, extreme_min) percentile boundaries."""
    return (
        float(os.environ.get("PHASE2_VOL_LOW_MAX", str(CANONICAL_VOL_LOW_MAX))),
        float(os.environ.get("PHASE2_VOL_HIGH_MIN", str(CANONICAL_VOL_HIGH_MIN))),
        float(os.environ.get("PHASE2_VOL_EXTREME_MIN", str(CANONICAL_VOL_EXTREME_MIN))),
    )


class VolatilityEngine:
    """
    Computes ATR14 / Current Price and determines historical percentile
    across the last 90 4H closed candles.
    """

    def __init__(self, atr_period: int = 14, percentile_window: int = 90):
        self.atr_period = atr_period
        self.percentile_window = percentile_window

    def compute_true_ranges(self, candles: List[Candle]) -> np.ndarray:
        """Computes true ranges for candle series."""
        n = len(candles)
        if n == 0:
            return np.array([])
        tr = np.zeros(n)
        tr[0] = candles[0].high - candles[0].low
        for i in range(1, n):
            h = candles[i].high
            l = candles[i].low
            prev_c = candles[i - 1].close
            tr[i] = max(h - l, abs(h - prev_c), abs(l - prev_c))
        return tr

    def compute_atr_series(self, candles: List[Candle]) -> np.ndarray:
        """Computes ATR using standard Wilder smoothing."""
        n = len(candles)
        if n < self.atr_period:
            return np.zeros(n)

        tr = self.compute_true_ranges(candles)
        atr = np.zeros(n)
        # Initial ATR: simple average
        atr[self.atr_period - 1] = np.mean(tr[: self.atr_period])

        for i in range(self.atr_period, n):
            atr[i] = (atr[i - 1] * (self.atr_period - 1) + tr[i]) / self.atr_period

        return atr

    def evaluate_volatility(self, candles_4h: List[Candle]) -> Tuple[VolatilityLevel, float, float]:
        """
        Evaluates volatility level and returns (VolatilityLevel, current_atr, percentile).
        Requires at least atr_period + percentile_window candles.
        """
        if len(candles_4h) < self.atr_period + 1:
            return VolatilityLevel.NORMAL, 0.0, 50.0

        atr_series = self.compute_atr_series(candles_4h)
        closes = np.array([c.close for c in candles_4h])

        # Avoid zero division
        closes = np.maximum(closes, 1e-8)
        norm_atr = atr_series / closes

        # Slice window
        window_norm_atr = norm_atr[-self.percentile_window :]
        current_norm = norm_atr[-1]
        current_atr = atr_series[-1]

        # Calculate percentile rank
        percentile = float(np.sum(window_norm_atr <= current_norm) / len(window_norm_atr) * 100.0)

        low_max, high_min, extreme_min = get_volatility_boundaries()
        if percentile < low_max:
            level = VolatilityLevel.LOW
        elif percentile < high_min:
            level = VolatilityLevel.NORMAL
        elif percentile < extreme_min:
            level = VolatilityLevel.HIGH
        else:
            level = VolatilityLevel.EXTREME

        return level, float(current_atr), percentile
