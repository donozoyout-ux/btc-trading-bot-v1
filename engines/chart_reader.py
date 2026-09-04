"""Deterministic multi-timeframe chart-reading layer.

This module does not open trades. It converts raw OHLCV into explainable chart
observations that can be shown in the dashboard and consumed by the advisory AI.
"""

from __future__ import annotations

from typing import Any, Dict, List

import numpy as np

from core.models import Candle
from engines.regime_engine import MarketRegimeEngine
from engines.volatility_engine import VolatilityEngine


class ChartReader:
    WEIGHTS = {"4h": 4.0, "1h": 3.0, "15m": 2.0, "5m": 1.0}

    @staticmethod
    def _last_valid(series: np.ndarray) -> float | None:
        if series is None or len(series) == 0:
            return None
        for raw in reversed(series.tolist()):
            value = float(raw)
            if np.isfinite(value) and value != 0.0:
                return value
        return None

    @staticmethod
    def _pattern(candles: List[Candle]) -> List[str]:
        if len(candles) < 2:
            return []
        prev = candles[-2]
        cur = candles[-1]
        patterns: List[str] = []

        prev_body_low = min(prev.open, prev.close)
        prev_body_high = max(prev.open, prev.close)
        cur_body_low = min(cur.open, cur.close)
        cur_body_high = max(cur.open, cur.close)

        if cur.close > cur.open and prev.close < prev.open and cur_body_low <= prev_body_low and cur_body_high >= prev_body_high:
            patterns.append("BULLISH_ENGULFING")
        if cur.close < cur.open and prev.close > prev.open and cur_body_low <= prev_body_low and cur_body_high >= prev_body_high:
            patterns.append("BEARISH_ENGULFING")

        rng = max(cur.high - cur.low, 1e-12)
        lower_wick_ratio = (min(cur.open, cur.close) - cur.low) / rng
        upper_wick_ratio = (cur.high - max(cur.open, cur.close)) / rng
        body_ratio = abs(cur.close - cur.open) / rng

        if lower_wick_ratio >= 0.45 and body_ratio <= 0.45:
            patterns.append("BULLISH_REJECTION")
        if upper_wick_ratio >= 0.45 and body_ratio <= 0.45:
            patterns.append("BEARISH_REJECTION")
        if cur.high < prev.high and cur.low > prev.low:
            patterns.append("INSIDE_BAR")
        return patterns

    def analyze_timeframe(self, timeframe: str, candles: List[Candle]) -> Dict[str, Any]:
        if len(candles) < 55:
            return {"timeframe": timeframe, "status": "INSUFFICIENT_DATA"}

        closes = np.array([c.close for c in candles], dtype=float)
        volumes = np.array([c.volume for c in candles], dtype=float)
        ema20 = MarketRegimeEngine.calculate_ema(closes, 20)
        ema50 = MarketRegimeEngine.calculate_ema(closes, 50)
        rsi = MarketRegimeEngine.calculate_rsi(closes, 14)
        adx, plus_di, minus_di = MarketRegimeEngine.calculate_adx_dmi(candles, 14)
        atr = VolatilityEngine().compute_atr_series(candles)

        price = float(closes[-1])
        e20 = self._last_valid(ema20)
        e50 = self._last_valid(ema50)
        rsi14 = self._last_valid(rsi)
        adx14 = self._last_valid(adx)
        plus = self._last_valid(plus_di)
        minus = self._last_valid(minus_di)
        atr14 = self._last_valid(atr)

        volume_avg = float(np.mean(volumes[-21:-1])) if len(volumes) >= 21 else float(np.mean(volumes[:-1]))
        rvol = float(volumes[-1] / volume_avg) if volume_avg > 0 else None

        score = 0.0
        evidence: List[str] = []
        if e20 is not None and e50 is not None:
            if price > e20 > e50:
                score += 2.0
                evidence.append("PRICE_ABOVE_EMA20_EMA50")
            elif price < e20 < e50:
                score -= 2.0
                evidence.append("PRICE_BELOW_EMA20_EMA50")
            elif price > e50:
                score += 0.5
                evidence.append("PRICE_ABOVE_EMA50")
            elif price < e50:
                score -= 0.5
                evidence.append("PRICE_BELOW_EMA50")

        if rsi14 is not None:
            if rsi14 >= 60:
                score += 1.0
                evidence.append("RSI_BULLISH")
            elif rsi14 <= 40:
                score -= 1.0
                evidence.append("RSI_BEARISH")

        if adx14 is not None and adx14 >= 22 and plus is not None and minus is not None:
            if plus > minus:
                score += 1.0
                evidence.append("DMI_BULLISH")
            elif minus > plus:
                score -= 1.0
                evidence.append("DMI_BEARISH")

        previous_high = max(c.high for c in candles[-21:-1])
        previous_low = min(c.low for c in candles[-21:-1])
        breakout = "NONE"
        if price > previous_high:
            breakout = "BREAKOUT_UP"
            score += 1.0
            evidence.append("20_BAR_BREAKOUT_UP")
        elif price < previous_low:
            breakout = "BREAKOUT_DOWN"
            score -= 1.0
            evidence.append("20_BAR_BREAKOUT_DOWN")

        patterns = self._pattern(candles)
        if "BULLISH_ENGULFING" in patterns or "BULLISH_REJECTION" in patterns:
            score += 0.5
        if "BEARISH_ENGULFING" in patterns or "BEARISH_REJECTION" in patterns:
            score -= 0.5

        if score >= 2.0:
            bias = "BULLISH"
        elif score <= -2.0:
            bias = "BEARISH"
        else:
            bias = "NEUTRAL"

        distance_ema20_atr = None
        if e20 is not None and atr14 not in (None, 0.0):
            distance_ema20_atr = (price - e20) / atr14

        return {
            "timeframe": timeframe,
            "status": "OK",
            "bias": bias,
            "score": round(score, 2),
            "price": price,
            "ema20": e20,
            "ema50": e50,
            "rsi14": rsi14,
            "adx14": adx14,
            "plus_di": plus,
            "minus_di": minus,
            "atr14": atr14,
            "distance_from_ema20_atr": round(distance_ema20_atr, 3) if distance_ema20_atr is not None else None,
            "relative_volume": round(rvol, 3) if rvol is not None else None,
            "breakout_state": breakout,
            "patterns": patterns,
            "evidence": evidence,
        }

    def analyze(self, candles_by_timeframe: Dict[str, List[Candle]]) -> Dict[str, Any]:
        rows: Dict[str, Dict[str, Any]] = {}
        weighted = 0.0
        weight_total = 0.0
        alerts: List[str] = []

        for timeframe in ("4h", "1h", "15m", "5m"):
            row = self.analyze_timeframe(timeframe, candles_by_timeframe.get(timeframe, []))
            rows[timeframe] = row
            if row.get("status") != "OK":
                continue
            weight = self.WEIGHTS[timeframe]
            weighted += float(row.get("score", 0.0)) * weight
            weight_total += 4.5 * weight
            for pattern in row.get("patterns") or []:
                alerts.append(f"{timeframe.upper()} {pattern}")
            if row.get("breakout_state") != "NONE":
                alerts.append(f"{timeframe.upper()} {row['breakout_state']}")

        normalized = (weighted / weight_total * 100.0) if weight_total > 0 else 0.0
        if normalized >= 25:
            bias = "BULLISH"
        elif normalized <= -25:
            bias = "BEARISH"
        else:
            bias = "NEUTRAL"

        alignment_values = [
            row.get("bias") for row in rows.values() if row.get("status") == "OK" and row.get("bias") != "NEUTRAL"
        ]
        aligned = bool(alignment_values) and len(set(alignment_values)) == 1
        return {
            "status": "OK" if weight_total > 0 else "INSUFFICIENT_DATA",
            "overall_bias": bias,
            "score": round(normalized, 1),
            "multi_timeframe_aligned": aligned,
            "timeframes": rows,
            "alerts": alerts[:12],
            "method": "OHLCV + EMA/RSI/ADX/DMI/ATR + volume + candle patterns + 20-bar breakout",
            "execution_authority": False,
        }
