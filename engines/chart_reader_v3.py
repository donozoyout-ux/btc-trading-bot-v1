"""Deterministic, closed-candle multi-timeframe price-action intelligence."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import numpy as np

from core.models import Candle, MarketStructure
from engines.regime_engine import MarketRegimeEngine, get_overextended_params
from engines.structure_engine import MarketStructureEngine
from engines.volatility_engine import VolatilityEngine


class ChartReadingEngineV3:
    """Analyze real OHLCV without screenshots or forward-looking swing exposure."""

    TIMEFRAMES = ("4h", "1h", "15m", "5m")

    def __init__(
        self,
        volume_expansion_threshold: float = 1.5,
        wick_rejection_ratio: float = 0.35,
        directional_body_ratio: float = 0.65,
    ):
        self.structure_engine = MarketStructureEngine(left_bars=2, right_bars=2)
        self.volatility_engine = VolatilityEngine(atr_period=14)
        self.volume_expansion_threshold = volume_expansion_threshold
        self.wick_rejection_ratio = wick_rejection_ratio
        self.directional_body_ratio = directional_body_ratio

    @staticmethod
    def _last(series: np.ndarray, minimum: int) -> Optional[float]:
        if len(series) < minimum:
            return None
        value = float(series[-1])
        return value if np.isfinite(value) and value != 0.0 else None

    @staticmethod
    def _round(value: Optional[float], digits: int = 6) -> Optional[float]:
        return round(value, digits) if value is not None else None

    @staticmethod
    def _trend(ema20: Optional[float], ema50: Optional[float], ema200: Optional[float]) -> str:
        if None in (ema20, ema50, ema200):
            return "UNAVAILABLE"
        if ema20 > ema50 > ema200:
            return "UP"
        if ema20 < ema50 < ema200:
            return "DOWN"
        return "RANGE"

    def _patterns(self, candles: List[Candle]) -> List[str]:
        if not candles:
            return []
        current = candles[-1]
        previous = candles[-2] if len(candles) > 1 else None
        patterns: List[str] = []
        body_ratio = current.body_size / current.total_range
        if current.lower_wick / current.total_range >= self.wick_rejection_ratio and current.is_bullish:
            patterns.append("BULLISH_WICK_REJECTION")
        if current.upper_wick / current.total_range >= self.wick_rejection_ratio and current.is_bearish:
            patterns.append("BEARISH_WICK_REJECTION")
        if body_ratio >= self.directional_body_ratio:
            patterns.append("BULLISH_DIRECTIONAL" if current.is_bullish else "BEARISH_DIRECTIONAL")
        if previous:
            if current.high < previous.high and current.low > previous.low:
                patterns.append("INSIDE_BAR")
            if (
                current.is_bullish
                and previous.is_bearish
                and current.open <= previous.close
                and current.close >= previous.open
            ):
                patterns.append("BULLISH_ENGULFING")
            if (
                current.is_bearish
                and previous.is_bullish
                and current.open >= previous.close
                and current.close <= previous.open
            ):
                patterns.append("BEARISH_ENGULFING")
        return patterns

    @staticmethod
    def _nearest_levels(structure: MarketStructure, price: float) -> tuple[Optional[float], Optional[float]]:
        levels = [s.price for s in structure.swing_highs + structure.swing_lows]
        supports = [level for level in levels if level <= price]
        resistances = [level for level in levels if level >= price]
        return (max(supports) if supports else None, min(resistances) if resistances else None)

    @staticmethod
    def _breakout_state(
        candles: List[Candle], structure: MarketStructure
    ) -> tuple[str, str, bool]:
        if len(candles) < 2:
            return "UNAVAILABLE", "UNAVAILABLE", False
        current, previous = candles[-1], candles[-2]
        last_high = structure.swing_highs[-1].price if structure.swing_highs else None
        last_low = structure.swing_lows[-1].price if structure.swing_lows else None
        breakout = "NONE"
        retest = "NONE"
        fake_breakout = False
        if last_high is not None:
            if previous.close <= last_high < current.close:
                breakout = "BULLISH_BREAKOUT"
            elif previous.close > last_high and current.low <= last_high <= current.close:
                retest = "BULLISH_RETEST_HOLD"
            if current.high > last_high and current.close <= last_high:
                fake_breakout = True
        if last_low is not None:
            if previous.close >= last_low > current.close:
                breakout = "BEARISH_BREAKOUT"
            elif previous.close < last_low and current.high >= last_low >= current.close:
                retest = "BEARISH_RETEST_HOLD"
            if current.low < last_low and current.close >= last_low:
                fake_breakout = True
        return breakout, retest, fake_breakout

    def analyze_timeframe(self, timeframe: str, candles: List[Candle]) -> Dict[str, Any]:
        # Open/incomplete candles are discarded before every calculation.
        closed = [c for c in candles if c.is_closed]
        if not closed:
            return {"timeframe": timeframe, "status": "UNAVAILABLE", "closed_candles": 0}

        interval = self.structure_engine.get_candle_interval_ms(closed, timeframe)
        as_of_time = closed[-1].timestamp + interval
        structure = self.structure_engine.analyze_structure(timeframe, closed, as_of_time=as_of_time)
        closes = np.asarray([c.close for c in closed], dtype=float)
        volumes = np.asarray([c.volume for c in closed], dtype=float)
        ema20_s = MarketRegimeEngine.calculate_ema(closes, 20)
        ema50_s = MarketRegimeEngine.calculate_ema(closes, 50)
        ema200_s = MarketRegimeEngine.calculate_ema(closes, 200)
        rsi_s = MarketRegimeEngine.calculate_rsi(closes, 14)
        adx_s, plus_di_s, minus_di_s = MarketRegimeEngine.calculate_adx_dmi(closed, 14)
        atr_s = self.volatility_engine.compute_atr_series(closed)
        ema20 = self._last(ema20_s, 20)
        ema50 = self._last(ema50_s, 50)
        ema200 = self._last(ema200_s, 200)
        rsi = self._last(rsi_s, 15)
        adx = self._last(adx_s, 29)
        plus_di = self._last(plus_di_s, 29)
        minus_di = self._last(minus_di_s, 29)
        atr = self._last(atr_s, 14)

        bb_mid = bb_upper = bb_lower = None
        if len(closes) >= 20:
            window = closes[-20:]
            bb_mid = float(np.mean(window))
            std = float(np.std(window))
            bb_upper, bb_lower = bb_mid + 2.0 * std, bb_mid - 2.0 * std

        relative_volume = None
        if len(volumes) >= 21:
            baseline = float(np.mean(volumes[-21:-1]))
            relative_volume = float(volumes[-1] / baseline) if baseline > 0 else None
        volume_state = "UNAVAILABLE"
        if relative_volume is not None:
            if relative_volume >= self.volume_expansion_threshold:
                volume_state = "EXPANSION"
            elif relative_volume <= 1.0 / self.volume_expansion_threshold:
                volume_state = "CONTRACTION"
            else:
                volume_state = "NORMAL"

        price = closed[-1].close
        atr_distance = (price - ema20) / atr if ema20 is not None and atr else None
        oe_atr_mult, _, _ = get_overextended_params()
        overextended = bool(atr_distance is not None and abs(atr_distance) >= oe_atr_mult)
        support, resistance = self._nearest_levels(structure, price)
        breakout, retest, fake_breakout = self._breakout_state(closed, structure)

        return {
            "timeframe": timeframe,
            "status": "AVAILABLE",
            "closed_candles": len(closed),
            "last_closed_at": closed[-1].timestamp + interval,
            "structure": structure.structure.value,
            "trend": self._trend(ema20, ema50, ema200),
            "hh": structure.recent_hh,
            "hl": structure.recent_hl,
            "lh": structure.recent_lh,
            "ll": structure.recent_ll,
            "swing_highs": [s.model_dump() for s in structure.swing_highs[-5:]],
            "swing_lows": [s.model_dump() for s in structure.swing_lows[-5:]],
            "support_zones": [
                {"center": self._round(s.price), "confirmed_at": s.confirmed_at}
                for s in structure.swing_lows[-5:] if s.price <= price
            ],
            "resistance_zones": [
                {"center": self._round(s.price), "confirmed_at": s.confirmed_at}
                for s in structure.swing_highs[-5:] if s.price >= price
            ],
            "bos": structure.last_bos,
            "choch": structure.last_choch,
            "nearest_support": self._round(support),
            "nearest_resistance": self._round(resistance),
            "breakout_state": breakout,
            "retest_state": retest,
            "fake_breakout": fake_breakout,
            "ema20": self._round(ema20),
            "ema50": self._round(ema50),
            "ema200": self._round(ema200),
            "rsi": self._round(rsi, 3),
            "adx": self._round(adx, 3),
            "plus_di": self._round(plus_di, 3),
            "minus_di": self._round(minus_di, 3),
            "atr": self._round(atr),
            "bollinger": {
                "upper": self._round(bb_upper),
                "mid": self._round(bb_mid),
                "lower": self._round(bb_lower),
            },
            "relative_volume": self._round(relative_volume, 3),
            "volume_state": volume_state,
            "wick_rejection": any("WICK_REJECTION" in p for p in self._patterns(closed)),
            "overextension_atr": self._round(atr_distance, 3),
            "overextended": overextended,
            "patterns": self._patterns(closed),
        }

    def analyze(self, candles_by_timeframe: Dict[str, List[Candle]]) -> Dict[str, Any]:
        timeframes = {
            tf: self.analyze_timeframe(tf, candles_by_timeframe.get(tf, []))
            for tf in self.TIMEFRAMES
        }
        return {"status": "AVAILABLE" if all(v.get("status") == "AVAILABLE" for v in timeframes.values()) else "DEGRADED", "timeframes": timeframes}


class MultiTimeframeInterpreter:
    """Produce a deterministic bias; it never creates a trade decision."""

    WEIGHTS = {"4h": 4, "1h": 3, "15m": 2, "5m": 1}

    def interpret(self, chart: Dict[str, Any]) -> Dict[str, Any]:
        frames = chart.get("timeframes", {})
        score = 0
        conflicts: List[str] = []
        directions: Dict[str, int] = {}
        for tf, weight in self.WEIGHTS.items():
            item = frames.get(tf, {})
            direction = 0
            if item.get("structure") == "BULLISH" or item.get("trend") == "UP":
                direction = 1
            if item.get("structure") == "BEARISH" or item.get("trend") == "DOWN":
                direction = -1
            directions[tf] = direction
            score += direction * weight
        macro = directions.get("4h", 0)
        for tf in ("1h", "15m", "5m"):
            if macro and directions.get(tf) and directions[tf] != macro:
                conflicts.append(f"4H_{'BULLISH' if macro > 0 else 'BEARISH'}_VS_{tf.upper()}_{'BULLISH' if directions[tf] > 0 else 'BEARISH'}")
        if score >= 8:
            bias = "STRONG_LONG"
        elif score >= 3:
            bias = "LONG"
        elif score <= -8:
            bias = "STRONG_SHORT"
        elif score <= -3:
            bias = "SHORT"
        else:
            bias = "NEUTRAL"
        trigger = frames.get("5m", {})
        trigger_ready = bool(trigger.get("bos") or trigger.get("choch") or trigger.get("retest_state") not in (None, "NONE", "UNAVAILABLE"))
        return {
            "overall_bias": bias,
            "score": score,
            "conflicts": conflicts,
            "state": "TRIGGER_READY" if trigger_ready else "WAIT_TRIGGER",
            "execution_authority": False,
        }
