"""Volume Engine evaluating relative volume (RVOL), spikes, pullback volume decay, and flow."""

from typing import List, Dict, Any
import numpy as np
from core.models import Candle


class VolumeEngine:
    """
    Analyzes volume characteristics:
    RVOL (Relative Volume), volume expansion on impulse,
    volume contraction on pullbacks, and candle buy/sell pressure.
    """

    def __init__(self, sma_period: int = 20):
        self.sma_period = sma_period

    def calculate_rvol(self, candles: List[Candle]) -> float:
        """Calculates Relative Volume (RVOL) = current volume / SMA20 volume."""
        if len(candles) < self.sma_period + 1:
            return 1.0

        volumes = np.array([c.volume for c in candles])
        sma = np.mean(volumes[-self.sma_period - 1 : -1])
        if sma <= 0:
            return 1.0

        return float(volumes[-1] / sma)

    def is_pullback_volume_healthy(self, candles: List[Candle], is_bullish_trend: bool) -> bool:
        """
        Checks if pullback volume is decreasing (healthy continuation pattern).
        Bullish: recent down candles have lower volume than up impulse candles.
        Bearish: recent up candles have lower volume than down impulse candles.
        """
        if len(candles) < 5:
            return True

        recent = candles[-5:]
        impulse_vols = []
        pullback_vols = []

        for c in recent:
            if is_bullish_trend:
                if c.is_bullish:
                    impulse_vols.append(c.volume)
                else:
                    pullback_vols.append(c.volume)
            else:
                if c.is_bearish:
                    impulse_vols.append(c.volume)
                else:
                    pullback_vols.append(c.volume)

        avg_impulse = np.mean(impulse_vols) if impulse_vols else 0.0
        avg_pullback = np.mean(pullback_vols) if pullback_vols else 0.0

        if avg_impulse == 0.0:
            return True

        # Healthy pullback means pullback volume is lower than impulse volume
        return avg_pullback <= avg_impulse * 1.15

    def get_candle_pressure(self, candle: Candle) -> float:
        """
        Computes buy vs sell pressure based on close location within high-low range.
        Returns value from 0.0 (extreme selling pressure) to 1.0 (extreme buying pressure).
        """
        rng = candle.total_range
        if rng <= 0:
            return 0.5
        return float((candle.close - candle.low) / rng)

    def analyze_volume(self, candles: List[Candle], is_bullish_trend: bool = True) -> Dict[str, Any]:
        """Provides full volume profile for current candle."""
        rvol = self.calculate_rvol(candles)
        pullback_healthy = self.is_pullback_volume_healthy(candles, is_bullish_trend)
        last_c = candles[-1] if candles else None
        pressure = self.get_candle_pressure(last_c) if last_c else 0.5

        return {
            "rvol": round(rvol, 2),
            "is_volume_spike": rvol >= 2.0,
            "is_breakout_volume": rvol >= 1.5,
            "is_pullback_healthy": pullback_healthy,
            "candle_pressure": round(pressure, 2),
        }
