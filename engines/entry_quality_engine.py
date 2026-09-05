"""Deterministic final entry-quality gate; never creates a trade."""

from typing import List, Optional
import numpy as np

from config.constants import SetupType, StructureType, TradeDirection
from core.models import Candle, EntryQualityAssessment, LocationResult, MarketStructure, SetupSignal, TradePlan


class EntryQualityEngine:
    def __init__(self, max_atr_extension: float, location_proximity_pct: float, min_risk_reward: float):
        self.max_atr_extension = max_atr_extension
        self.location_proximity_pct = location_proximity_pct
        self.min_risk_reward = min_risk_reward

    @staticmethod
    def _atr_extension(candles: List[Candle]) -> Optional[float]:
        closed = [c for c in candles if c.is_closed]
        if len(closed) < 20:
            return None
        rows = closed[-20:]
        trs = []
        for index, row in enumerate(rows):
            previous = rows[index - 1].close if index else row.open
            trs.append(max(row.high - row.low, abs(row.high - previous), abs(row.low - previous)))
        atr = float(np.mean(trs))
        return abs(rows[-1].close - float(np.mean([c.close for c in rows]))) / atr if atr > 0 else None

    @staticmethod
    def _rsi(candles: List[Candle]) -> Optional[float]:
        closed = [c for c in candles if c.is_closed]
        if len(closed) < 15:
            return None
        closes = np.array([c.close for c in closed])
        delta = np.diff(closes[-15:])
        gain, loss = np.maximum(delta, 0).mean(), np.maximum(-delta, 0).mean()
        return 100.0 if loss == 0 else float(100 - 100 / (1 + gain / loss))

    def evaluate(self, direction: TradeDirection, plan: TradePlan, setup: SetupSignal,
                 location: LocationResult, structure_5m: MarketStructure,
                 candles_5m: List[Candle], candles_15m: List[Candle],
                 price_basis_deviation_pct: Optional[float] = None) -> EntryQualityAssessment:
        reasons = []
        is_long = direction == TradeDirection.LONG
        opposing_bos = structure_5m.last_bos == ("BEARISH_BOS" if is_long else "BULLISH_BOS")
        opposing_choch = structure_5m.last_choch == ("BEARISH_CHOCH" if is_long else "BULLISH_CHOCH")
        if opposing_bos:
            reasons.append("OPPOSING_5M_BOS")
        if opposing_choch:
            reasons.append("OPPOSING_5M_CHOCH")
        if structure_5m.structure == (StructureType.BEARISH if is_long else StructureType.BULLISH):
            reasons.append("OPPOSING_5M_STRUCTURE")
        ext5 = self._atr_extension(candles_5m)
        ext15 = self._atr_extension(candles_15m)
        if ext5 is not None and ext5 >= self.max_atr_extension:
            reasons.append("CHASE_EXTENDED_5M")
        if ext15 is not None and ext15 >= self.max_atr_extension:
            reasons.append("CHASE_EXTENDED_15M")

        converted_level = (
            setup.setup_type == SetupType.BREAKOUT_RETEST
            and setup.retest_hold
            and not setup.setup_invalidated
            and setup.breakout_level is not None
        )
        converted_resistance = bool(
            converted_level
            and location.nearest_resistance
            and location.nearest_resistance.price_min <= float(setup.breakout_level) <= location.nearest_resistance.price_max
        )
        converted_support = bool(
            converted_level
            and location.nearest_support
            and location.nearest_support.price_min <= float(setup.breakout_level) <= location.nearest_support.price_max
        )
        stop_distance = abs(plan.entry_price - plan.stop_loss)
        if is_long and location.nearest_resistance and not converted_resistance:
            reward_space = location.nearest_resistance.price_min - plan.entry_price
            if location.distance_to_resistance_pct <= self.location_proximity_pct or reward_space < stop_distance * self.min_risk_reward:
                reasons.append("TOO_CLOSE_TO_RESISTANCE")
        if not is_long and location.nearest_support and not converted_support:
            reward_space = plan.entry_price - location.nearest_support.price_max
            if location.distance_to_support_pct <= self.location_proximity_pct or reward_space < stop_distance * self.min_risk_reward:
                reasons.append("TOO_CLOSE_TO_SUPPORT")
        if setup.setup_type == SetupType.BREAKOUT_RETEST and (not setup.retest_hold or setup.setup_invalidated):
            reasons.append("FAILED_BREAKOUT_RETEST")

        return EntryQualityAssessment(
            decision="REJECT" if reasons else "ACCEPT", reason_codes=list(dict.fromkeys(reasons)), direction=direction,
            entry_price=plan.entry_price,
            nearest_support=location.nearest_support.center if location.nearest_support else None,
            nearest_resistance=location.nearest_resistance.center if location.nearest_resistance else None,
            distance_to_support_pct=location.distance_to_support_pct if location.nearest_support else None,
            distance_to_resistance_pct=location.distance_to_resistance_pct if location.nearest_resistance else None,
            atr_extension_5m=ext5, atr_extension_15m=ext15, rsi_5m=self._rsi(candles_5m),
            rsi_15m=self._rsi(candles_15m), opposing_bos=opposing_bos, opposing_choch=opposing_choch,
            price_basis_deviation_pct=price_basis_deviation_pct,
            details={
                "converted_breakout_level": converted_level,
                "converted_nearest_resistance": converted_resistance,
                "converted_nearest_support": converted_support,
            },
        )
