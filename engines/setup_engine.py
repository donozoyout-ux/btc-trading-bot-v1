"""Setup Detection Engine for Setup A (Trend Pullback), Setup B (Breakout+Retest), and Setup C (Counter-Trend)."""

from typing import List, Optional, Tuple
import numpy as np
from loguru import logger

from core.models import Candle, MarketStructure, ConfluenceZone, RegimeResult, LocationResult, SetupSignal
from config.constants import (
    MarketRegime,
    StructureType,
    TradeDirection,
    SetupType,
    LocationQuality,
)
from engines.volume_engine import VolumeEngine


class SetupEngine:
    """
    Detects candidates for:
    - Setup A: Trend Pullback
    - Setup B: Breakout + Retest
    - Setup C: Counter-Trend Reaction
    """

    def __init__(
        self,
        volume_engine: Optional[VolumeEngine] = None,
        location_proximity_pct: float = 0.005,
        counter_trend_rsi_oversold: float = 30.0,
        counter_trend_rsi_overbought: float = 70.0,
        counter_trend_adx_veto: float = 35.0,
        bollinger_period: int = 20,
        bollinger_std_dev: float = 2.0,
    ):
        self.vol_engine = volume_engine or VolumeEngine()
        self.location_proximity_pct = location_proximity_pct
        self.counter_trend_rsi_oversold = counter_trend_rsi_oversold
        self.counter_trend_rsi_overbought = counter_trend_rsi_overbought
        self.counter_trend_adx_veto = counter_trend_adx_veto
        self.bollinger_period = bollinger_period
        self.bollinger_std_dev = bollinger_std_dev

    @staticmethod
    def calculate_bollinger_bands(closes: np.ndarray, period: int = 20, std_dev: float = 2.0) -> Tuple[float, float, float]:
        """Calculates middle, upper, and lower Bollinger Bands for the latest bar."""
        if len(closes) < period:
            return float(closes[-1]), float(closes[-1]), float(closes[-1])
        window = closes[-period:]
        mid = float(np.mean(window))
        std = float(np.std(window))
        upper = mid + std_dev * std
        lower = mid - std_dev * std
        return mid, upper, lower

    @staticmethod
    def calculate_rsi_quick(closes: np.ndarray, period: int = 14) -> float:
        """Computes current RSI for 5M candles."""
        if len(closes) <= period:
            return 50.0
        deltas = np.diff(closes[-period - 1:])
        gains = np.maximum(deltas, 0)
        losses = np.maximum(-deltas, 0)
        avg_gain = float(np.mean(gains))
        avg_loss = float(np.mean(losses))
        if avg_loss == 0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    def detect_setup_a_trend_pullback(
        self,
        regime: RegimeResult,
        struct_1h: MarketStructure,
        candles_15m: List[Candle],
        location: LocationResult,
    ) -> Optional[SetupSignal]:
        """
        Setup A — Trend Pullback (Section 22):
        LONG: 4H Bull, 1H Bullish, 15M pullback to support, healthy volume, not overextended.
        SHORT: 4H Bear, 1H Bearish, 15M pullback to resistance.
        """
        if len(candles_15m) < 10:
            return None

        current_price = candles_15m[-1].close

        # Check LONG
        if regime.regime in [MarketRegime.BULL, MarketRegime.STRONG_BULL]:
            if struct_1h.structure == StructureType.BULLISH:
                if location.quality in [LocationQuality.GOOD_LONG_LOCATION, LocationQuality.STRONG_LONG_LOCATION]:
                    if not regime.overextended_up:
                        pullback_healthy = self.vol_engine.is_pullback_volume_healthy(candles_15m, is_bullish_trend=True)
                        if pullback_healthy:
                            sup = location.nearest_support
                            inv_price = (sup.price_min * 0.998) if sup else (min(c.low for c in candles_15m[-5:]) * 0.998)
                            res = location.nearest_resistance
                            target_price = res.center if res else (current_price * 1.02)

                            return SetupSignal(
                                setup_type=SetupType.TREND_PULLBACK,
                                direction=TradeDirection.LONG,
                                detected=True,
                                timeframe="15m",
                                invalidation_level=inv_price,
                                target_level=target_price,
                                zone=sup,
                                reason="Setup A: 4H & 1H Bullish trend pullback into support with healthy volume decay",
                            )

        # Check SHORT
        if regime.regime in [MarketRegime.BEAR, MarketRegime.STRONG_BEAR]:
            if struct_1h.structure == StructureType.BEARISH:
                if location.quality in [LocationQuality.GOOD_SHORT_LOCATION, LocationQuality.STRONG_SHORT_LOCATION]:
                    if not regime.overextended_down:
                        pullback_healthy = self.vol_engine.is_pullback_volume_healthy(candles_15m, is_bullish_trend=False)
                        if pullback_healthy:
                            res = location.nearest_resistance
                            inv_price = (res.price_max * 1.002) if res else (max(c.high for c in candles_15m[-5:]) * 1.002)
                            sup = location.nearest_support
                            target_price = sup.center if sup else (current_price * 0.98)

                            return SetupSignal(
                                setup_type=SetupType.TREND_PULLBACK,
                                direction=TradeDirection.SHORT,
                                detected=True,
                                timeframe="15m",
                                invalidation_level=inv_price,
                                target_level=target_price,
                                zone=res,
                                reason="Setup A: 4H & 1H Bearish trend pullback into resistance with healthy volume decay",
                            )

        return None

    def detect_setup_b_breakout_retest(
        self,
        regime: RegimeResult,
        candles_15m: List[Candle],
        zones: List[ConfluenceZone],
    ) -> Optional[SetupSignal]:
        """
        Setup B — Breakout + Retest (Section 23):
        Resistance broken on volume -> retested and holding as support.
        """
        if len(candles_15m) < 15:
            return None

        # The engine accepts only closed bars. This keeps both the breakout and
        # retest chronological and prevents an in-progress/future bar leak.
        last_10 = [c for c in candles_15m if c.is_closed][-10:]
        if len(last_10) < 10:
            return None
        current_price = last_10[-1].close

        # For LONG Breakout Retest
        if regime.regime in [MarketRegime.BULL, MarketRegime.STRONG_BULL]:
            # Look for a zone that was recently broken to the upside and is currently retested
            for z in zones:
                # Breakout condition: a candle in last 10 closed above z.price_max
                broke_above = any(c.close > z.price_max for c in last_10[:-3])
                # Retest condition: price dipped back to z and held (low <= z.price_max * 1.002 and close >= z.price_min)
                retested = any(c.low <= z.price_max * 1.002 and c.close >= z.price_min for c in last_10[-3:])

                if broke_above and retested and current_price >= z.price_min:
                    inv_price = z.price_min * 0.997
                    target_price = current_price * 1.025

                    return SetupSignal(
                        setup_type=SetupType.BREAKOUT_RETEST,
                        direction=TradeDirection.LONG,
                        detected=True,
                        timeframe="15m",
                        invalidation_level=inv_price,
                        target_level=target_price,
                        zone=z,
                        reason=f"Setup B: Breakout and retest of level {z.center:.1f} holding as support",
                    )

        # For SHORT Breakdown Retest. The breakdown is confined to the
        # historical portion and therefore must precede every retest bar.
        if regime.regime in [MarketRegime.BEAR, MarketRegime.STRONG_BEAR]:
            for z in zones:
                broke_below = any(c.close < z.price_min for c in last_10[:-3])
                retested = any(
                    c.high >= z.price_min * 0.998
                    and c.low <= z.price_max
                    and c.close < z.price_min
                    for c in last_10[-3:]
                )
                if broke_below and retested and current_price < z.price_min:
                    return SetupSignal(
                        setup_type=SetupType.BREAKOUT_RETEST,
                        direction=TradeDirection.SHORT,
                        detected=True,
                        timeframe="15m",
                        invalidation_level=z.price_max * 1.003,
                        target_level=current_price * 0.975,
                        zone=z,
                        reason=f"Setup B: Breakdown and retest of level {z.center:.1f} holding as resistance",
                    )

        return None

    def detect_setup_c_counter_trend(
        self,
        regime: RegimeResult,
        candles_5m: List[Candle],
        location: LocationResult,
    ) -> Optional[SetupSignal]:
        """
        Setup C — Counter-Trend Reaction (Sections 24, 25, 26):
        Trend Bearish, but price reaches Strong Confluence Support + 5M Lower BB stretch + RSI oversold.
        Target: BB middle / nearest resistance.
        """
        if len(candles_5m) < 25:
            return None

        closes_5m = np.array([c.close for c in candles_5m])
        current_price = closes_5m[-1]

        # Counter-trend LONG when market is Bearish
        if regime.regime in [MarketRegime.BEAR, MarketRegime.STRONG_BEAR]:
            # Section 25 filter: If 4H ADX >= 35, reject counter-trend long
            adx_val = regime.details.get("current_adx", 20.0)
            if adx_val >= self.counter_trend_adx_veto:
                return None  # Trend too aggressive to counter-trade

            # Confluence check: Must be at strong support (strength >= 2)
            sup = location.nearest_support
            if not sup or sup.strength < 2 or location.distance_to_support_pct > self.location_proximity_pct:
                return None

            # Bollinger Bands 5M check (Section 26)
            mid_bb, _, lower_bb = self.calculate_bollinger_bands(closes_5m, self.bollinger_period, self.bollinger_std_dev)
            rsi_5m = self.calculate_rsi_quick(closes_5m, 14)

            # Lower band stretch: price touching or under lower band
            is_bb_stretch = current_price <= lower_bb * 1.002
            is_rsi_oversold = rsi_5m < self.counter_trend_rsi_oversold

            if is_bb_stretch and is_rsi_oversold:
                inv_price = sup.price_min * 0.997
                target_price = min(mid_bb, sup.center * 1.015)  # Quick mean-reversion target

                return SetupSignal(
                    setup_type=SetupType.COUNTER_TREND_REACTION,
                    direction=TradeDirection.LONG,
                    detected=True,
                    timeframe="5m",
                    invalidation_level=inv_price,
                    target_level=target_price,
                    zone=sup,
                    reason=(
                        f"Setup C: Counter-Trend Reaction at major support ({sup.strength} confluences) "
                        f"with 5M BB lower stretch ({lower_bb:.1f}) & RSI oversold ({rsi_5m:.1f})"
                    ),
                )

        # Symmetric counter-trend SHORT at major resistance in a bull regime.
        if regime.regime in [MarketRegime.BULL, MarketRegime.STRONG_BULL]:
            adx_val = regime.details.get("current_adx", 20.0)
            if adx_val >= self.counter_trend_adx_veto:
                return None
            res = location.nearest_resistance
            if not res or res.strength < 2 or location.distance_to_resistance_pct > self.location_proximity_pct:
                return None
            mid_bb, upper_bb, _ = self.calculate_bollinger_bands(closes_5m, self.bollinger_period, self.bollinger_std_dev)
            rsi_5m = self.calculate_rsi_quick(closes_5m, 14)
            is_bb_stretch = current_price >= upper_bb * 0.998
            is_rsi_overbought = rsi_5m > self.counter_trend_rsi_overbought
            if is_bb_stretch and is_rsi_overbought:
                return SetupSignal(
                    setup_type=SetupType.COUNTER_TREND_REACTION,
                    direction=TradeDirection.SHORT,
                    detected=True,
                    timeframe="5m",
                    invalidation_level=res.price_max * 1.003,
                    target_level=max(mid_bb, res.center * 0.985),
                    zone=res,
                    reason=(
                        f"Setup C: Counter-Trend Reaction at major resistance ({res.strength} confluences) "
                        f"with 5M BB upper stretch ({upper_bb:.1f}) & RSI overbought ({rsi_5m:.1f})"
                    ),
                )

        return None

    def evaluate_setups(
        self,
        regime: RegimeResult,
        struct_1h: MarketStructure,
        candles_15m: List[Candle],
        candles_5m: List[Candle],
        location: LocationResult,
        zones: List[ConfluenceZone],
    ) -> SetupSignal:
        """
        Runs candidate setup detection in prioritized order:
        1. Setup A: Trend Pullback (preferred)
        2. Setup B: Breakout + Retest
        3. Setup C: Counter-Trend Reaction
        """
        if location.is_bad_location:
            return SetupSignal(
                setup_type=SetupType.NONE,
                direction=TradeDirection.WAIT,
                detected=False,
                reason="Bad trade location filtered",
            )

        # 1. Setup A
        setup_a = self.detect_setup_a_trend_pullback(regime, struct_1h, candles_15m, location)
        if setup_a:
            return setup_a

        # 2. Setup B
        setup_b = self.detect_setup_b_breakout_retest(regime, candles_15m, zones)
        if setup_b:
            return setup_b

        # 3. Setup C
        setup_c = self.detect_setup_c_counter_trend(regime, candles_5m, location)
        if setup_c:
            return setup_c

        return SetupSignal(
            setup_type=SetupType.NONE,
            direction=TradeDirection.WAIT,
            detected=False,
            reason="No active setup detected across current market state",
        )
