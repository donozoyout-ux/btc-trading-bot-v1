"""Market Regime Engine evaluating 4H macro regime, indicators, scoring, range override, and stability."""

import numpy as np
from typing import List, Tuple, Dict, Any, Optional
from loguru import logger

from core.models import Candle, MarketStructure, RegimeResult
from config.constants import MarketRegime, StructureType, VolatilityLevel
from engines.volatility_engine import VolatilityEngine


class MarketRegimeEngine:
    """
    Computes 4H indicators, evaluates Regime Score (-100 to +100),
    checks Range Override, Overextended states, and Regime Stability.
    """

    def __init__(self, volatility_engine: Optional[VolatilityEngine] = None):
        self.vol_engine = volatility_engine or VolatilityEngine()

    @staticmethod
    def calculate_ema(values: np.ndarray, period: int) -> np.ndarray:
        """Calculates Exponential Moving Average."""
        n = len(values)
        ema = np.zeros(n)
        if n < period:
            return ema

        alpha = 2.0 / (period + 1.0)
        ema[period - 1] = np.mean(values[:period])
        for i in range(period, n):
            ema[i] = alpha * values[i] + (1 - alpha) * ema[i - 1]
        return ema

    @staticmethod
    def calculate_rsi(closes: np.ndarray, period: int = 14) -> np.ndarray:
        """Calculates Relative Strength Index using Wilder smoothing."""
        n = len(closes)
        rsi = np.zeros(n)
        if n <= period:
            return rsi

        deltas = np.diff(closes)
        gains = np.maximum(deltas, 0)
        losses = np.maximum(-deltas, 0)

        avg_gain = np.mean(gains[:period])
        avg_loss = np.mean(losses[:period])

        if avg_loss == 0:
            rsi[period] = 100.0
        else:
            rs = avg_gain / avg_loss
            rsi[period] = 100.0 - (100.0 / (1.0 + rs))

        for i in range(period + 1, n):
            gain = gains[i - 1]
            loss = losses[i - 1]
            avg_gain = (avg_gain * (period - 1) + gain) / period
            avg_loss = (avg_loss * (period - 1) + loss) / period

            if avg_loss == 0:
                rsi[i] = 100.0
            else:
                rs = avg_gain / avg_loss
                rsi[i] = 100.0 - (100.0 / (1.0 + rs))

        return rsi

    @staticmethod
    def calculate_adx_dmi(candles: List[Candle], period: int = 14) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Calculates ADX, +DI, and -DI."""
        n = len(candles)
        adx = np.zeros(n)
        plus_di = np.zeros(n)
        minus_di = np.zeros(n)

        if n <= period * 2:
            return adx, plus_di, minus_di

        tr = np.zeros(n)
        plus_dm = np.zeros(n)
        minus_dm = np.zeros(n)

        for i in range(1, n):
            h = candles[i].high
            l = candles[i].low
            prev_h = candles[i - 1].high
            prev_l = candles[i - 1].low
            prev_c = candles[i - 1].close

            tr[i] = max(h - l, abs(h - prev_c), abs(l - prev_c))
            up_move = h - prev_h
            down_move = prev_l - l

            if up_move > down_move and up_move > 0:
                plus_dm[i] = up_move
            if down_move > up_move and down_move > 0:
                minus_dm[i] = down_move

        # Initial Wilder sums
        smooth_tr = np.sum(tr[1 : period + 1])
        smooth_plus = np.sum(plus_dm[1 : period + 1])
        smooth_minus = np.sum(minus_dm[1 : period + 1])

        dx = np.zeros(n)
        if smooth_tr > 0:
            plus_di[period] = (smooth_plus / smooth_tr) * 100.0
            minus_di[period] = (smooth_minus / smooth_tr) * 100.0
            di_sum = plus_di[period] + minus_di[period]
            if di_sum > 0:
                dx[period] = (abs(plus_di[period] - minus_di[period]) / di_sum) * 100.0

        for i in range(period + 1, n):
            smooth_tr = smooth_tr - (smooth_tr / period) + tr[i]
            smooth_plus = smooth_plus - (smooth_plus / period) + plus_dm[i]
            smooth_minus = smooth_minus - (smooth_minus / period) + minus_dm[i]

            if smooth_tr > 0:
                plus_di[i] = (smooth_plus / smooth_tr) * 100.0
                minus_di[i] = (smooth_minus / smooth_tr) * 100.0
                di_sum = plus_di[i] + minus_di[i]
                if di_sum > 0:
                    dx[i] = (abs(plus_di[i] - minus_di[i]) / di_sum) * 100.0

        # ADX is smoothed DX
        adx[period * 2] = np.mean(dx[period + 1 : period * 2 + 1])
        for i in range(period * 2 + 1, n):
            adx[i] = (adx[i - 1] * (period - 1) + dx[i]) / period

        return adx, plus_di, minus_di

    def evaluate_regime(
        self,
        candles_4h: List[Candle],
        structure_4h: MarketStructure,
        structure_1h: MarketStructure,
        recent_regimes_4h: Optional[List[MarketRegime]] = None,
    ) -> RegimeResult:
        """
        Calculates all sub-scores and determines final Market Regime.
        """
        if len(candles_4h) < 50:
            # Not enough bars for base indicators (EMA50, ADX, ATR, RSI)
            return RegimeResult(
                regime=MarketRegime.RANGE,
                score=0.0,
                confidence="LOW",
                volatility=VolatilityLevel.NORMAL,
                details={"reason": "Insufficient candles for base indicators (minimum 50 required)"},
            )

        closes = np.array([c.close for c in candles_4h])
        price = closes[-1]

        # Calculate EMAs
        ema20 = self.calculate_ema(closes, 20)
        ema50 = self.calculate_ema(closes, 50)
        ema200_period = 200 if len(closes) >= 200 else len(closes)
        ema200 = self.calculate_ema(closes, ema200_period)

        current_ema20 = ema20[-1]
        current_ema50 = ema50[-1]
        current_ema200 = ema200[-1]

        # Calculate ATR
        atr_series = self.vol_engine.compute_atr_series(candles_4h)
        current_atr = max(atr_series[-1], 1e-6)

        # Calculate ADX and DMI
        adx, plus_di, minus_di = self.calculate_adx_dmi(candles_4h, 14)
        current_adx = adx[-1]
        current_plus_di = plus_di[-1]
        current_minus_di = minus_di[-1]

        # Calculate RSI
        rsi_series = self.calculate_rsi(closes, 14)
        current_rsi = rsi_series[-1]

        # Volatility Percentile
        vol_level, _, _ = self.vol_engine.evaluate_volatility(candles_4h)

        # 1. Structure Score (+30, 0, -30)
        structure_score = 0
        if structure_4h.structure == StructureType.BULLISH:
            structure_score = 30
        elif structure_4h.structure == StructureType.BEARISH:
            structure_score = -30

        # 2. EMA Structure Score (+25, +15, 0, -15, -25)
        ema_score = 0
        if price > current_ema20 > current_ema50 > current_ema200:
            ema_score = 25
        elif current_ema20 > current_ema50 and price > current_ema50:
            ema_score = 15
        elif price < current_ema20 < current_ema50 < current_ema200:
            ema_score = -25
        elif current_ema20 < current_ema50 and price < current_ema50:
            ema_score = -15

        # 3. ADX + DMI Score
        adx_score = 0
        if current_adx >= 22:
            if current_plus_di > current_minus_di:
                adx_score = 15 if current_adx >= 30 else 10
            elif current_minus_di > current_plus_di:
                adx_score = -15 if current_adx >= 30 else -10

        # 4. EMA50 Slope Score (3-bar normalized delta)
        ema50_delta = (ema50[-1] - ema50[-4]) / current_atr if len(ema50) >= 4 else 0.0
        slope_score = 0
        if ema50_delta > 0.15:
            slope_score = 10
        elif ema50_delta < -0.15:
            slope_score = -10

        # 5. RSI Score
        rsi_score = 0
        if current_rsi >= 60:
            rsi_score = 10
        elif current_rsi >= 55:
            rsi_score = 5
        elif current_rsi <= 40:
            rsi_score = -10
        elif current_rsi <= 45:
            rsi_score = -5

        # 6. 1H Confirmation Score (+10, 0, -10)
        confirm_1h_score = 0
        if structure_1h.structure == StructureType.BULLISH:
            confirm_1h_score = 10
        elif structure_1h.structure == StructureType.BEARISH:
            confirm_1h_score = -10

        # Total Raw Score
        raw_score = structure_score + ema_score + adx_score + slope_score + rsi_score + confirm_1h_score
        raw_score = max(-100.0, min(100.0, float(raw_score)))

        # Section 14: Base Regime Classification
        if raw_score >= 70:
            tentative_regime = MarketRegime.STRONG_BULL
        elif raw_score >= 30:
            tentative_regime = MarketRegime.BULL
        elif raw_score <= -70:
            tentative_regime = MarketRegime.STRONG_BEAR
        elif raw_score <= -30:
            tentative_regime = MarketRegime.BEAR
        else:
            tentative_regime = MarketRegime.RANGE

        # Section 15: Range Override Check
        ema_diff = abs(current_ema20 - current_ema50)
        range_override_triggered = False

        if current_adx < 20 and ema_diff < (0.35 * current_atr):
            # Check crosses in last 20 4H bars
            last_20_closes = closes[-20:]
            last_20_ema50 = ema50[-20:]
            crosses = 0
            for i in range(1, len(last_20_closes)):
                prev_diff = last_20_closes[i - 1] - last_20_ema50[i - 1]
                curr_diff = last_20_closes[i] - last_20_ema50[i]
                if (prev_diff > 0 and curr_diff < 0) or (prev_diff < 0 and curr_diff > 0):
                    crosses += 1

            if structure_4h.structure == StructureType.MIXED or crosses >= 4:
                tentative_regime = MarketRegime.RANGE
                range_override_triggered = True

        # Section 17: Overextended Check
        overextended_up = bool((price - current_ema20 > 2.0 * current_atr) or (current_rsi > 75.0))
        overextended_down = bool((current_ema20 - price > 2.0 * current_atr) or (current_rsi < 25.0))

        # Section 18: Regime Stability Filter
        # Requires 2 consecutive closed 4H candles to switch, unless emergency flip (opposite BOS + |score| >= 70)
        final_regime = tentative_regime
        stability_confirmed = True

        if recent_regimes_4h and len(recent_regimes_4h) > 0:
            last_active = recent_regimes_4h[-1]
            if tentative_regime != last_active:
                # Emergency flip check
                is_emergency_flip = False
                if abs(raw_score) >= 70:
                    if tentative_regime in [MarketRegime.STRONG_BULL, MarketRegime.BULL] and structure_4h.last_bos == "BULLISH_BOS":
                        is_emergency_flip = True
                    elif tentative_regime in [MarketRegime.STRONG_BEAR, MarketRegime.BEAR] and structure_4h.last_bos == "BEARISH_BOS":
                        is_emergency_flip = True

                if not is_emergency_flip:
                    # Check if previous bar also had tentative_regime
                    if len(recent_regimes_4h) >= 2 and recent_regimes_4h[-2] == tentative_regime:
                        final_regime = tentative_regime
                        stability_confirmed = True
                    else:
                        # Hold previous regime until second confirmation
                        final_regime = last_active
                        stability_confirmed = False

        # Confidence assessment
        confidence = "MEDIUM"
        if abs(raw_score) >= 75 and stability_confirmed:
            confidence = "HIGH"
        elif abs(raw_score) < 30 or not stability_confirmed:
            confidence = "LOW"

        details = {
            "raw_score": raw_score,
            "structure_score": structure_score,
            "ema_score": ema_score,
            "adx_score": adx_score,
            "slope_score": slope_score,
            "rsi_score": rsi_score,
            "confirm_1h_score": confirm_1h_score,
            "ema50_delta_atr": round(ema50_delta, 3),
            "current_adx": round(current_adx, 2),
            "current_rsi": round(current_rsi, 2),
            "current_atr": round(current_atr, 2),
            "range_override": range_override_triggered,
        }

        return RegimeResult(
            regime=final_regime,
            score=raw_score,
            confidence=confidence,
            volatility=vol_level,
            is_transition=(tentative_regime != final_regime),
            overextended_up=overextended_up,
            overextended_down=overextended_down,
            stability_confirmed=stability_confirmed,
            details=details,
        )
