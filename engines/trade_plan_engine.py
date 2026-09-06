"""Deterministic context-aware trade planning with a legacy parity mode."""

from typing import List, Optional, Tuple

from config.constants import ManagementProfile, MarketRegime, SetupType, TradeDirection, VolatilityLevel
from core.models import SetupSignal, TargetContext, TradePlan, TriggerResult


class TradePlanEngine:
    """Build immutable entry/stop/target geometry before risk sizing."""

    def __init__(self, atr_buffer_factor: float = 0.20, *, dynamic_targets_enabled: bool = True,
                 minimum_rr: float = 1.50, fallback_tp1_r: float = 1.50,
                 conservative_tp2_r: float = 2.00, balanced_tp2_r: float = 2.50,
                 trend_tp2_r: float = 3.00):
        self.atr_buffer_factor = atr_buffer_factor
        self.dynamic_targets_enabled = dynamic_targets_enabled
        self.minimum_rr = minimum_rr
        self.fallback_tp1_r = fallback_tp1_r
        self.conservative_tp2_r = conservative_tp2_r
        self.balanced_tp2_r = balanced_tp2_r
        self.trend_tp2_r = trend_tp2_r

    @staticmethod
    def _ahead(levels: List[float], entry: float, direction: TradeDirection) -> List[float]:
        valid = {float(level) for level in levels if float(level) > 0}
        if direction == TradeDirection.LONG:
            return sorted(level for level in valid if level > entry)
        return sorted((level for level in valid if level < entry), reverse=True)

    def _mode_profile(self, setup: SetupSignal, context: TargetContext) -> Tuple[str, ManagementProfile, float]:
        strong = context.regime in {MarketRegime.STRONG_BULL, MarketRegime.STRONG_BEAR}
        trend = context.regime in {MarketRegime.BULL, MarketRegime.BEAR, MarketRegime.STRONG_BULL, MarketRegime.STRONG_BEAR}
        if setup.setup_type == SetupType.COUNTER_TREND_REACTION:
            return "COUNTER_TREND_CONSERVATIVE", ManagementProfile.CONSERVATIVE, self.conservative_tp2_r
        if context.regime == MarketRegime.RANGE:
            return "RANGE_TARGETS", ManagementProfile.CONSERVATIVE, self.conservative_tp2_r
        if context.volatility == VolatilityLevel.LOW:
            return "VOLATILITY_COMPRESSED", ManagementProfile.CONSERVATIVE, self.conservative_tp2_r
        if context.volatility in {VolatilityLevel.HIGH, VolatilityLevel.EXTREME}:
            profile = ManagementProfile.TREND_RUNNER if strong else ManagementProfile.BALANCED
            return "VOLATILITY_EXPANDED", profile, self.trend_tp2_r if strong else self.balanced_tp2_r
        if strong and context.regime_confidence.upper() == "HIGH":
            return "TREND_EXPANSION", ManagementProfile.TREND_RUNNER, self.trend_tp2_r
        if trend:
            return "STRUCTURE_TARGETS", ManagementProfile.BALANCED, self.balanced_tp2_r
        return "STRUCTURE_TARGETS", ManagementProfile.BALANCED, self.balanced_tp2_r

    def generate_plan(self, setup: SetupSignal, trigger: TriggerResult, current_atr: float,
                      context: Optional[TargetContext] = None) -> TradePlan:
        entry = float(trigger.trigger_price)
        direction = setup.direction
        atr_buffer = current_atr * self.atr_buffer_factor if current_atr > 0 else entry * 0.001
        if direction == TradeDirection.LONG:
            stop = setup.invalidation_level - atr_buffer
            if stop >= entry:
                stop = entry - current_atr * 1.5
        elif direction == TradeDirection.SHORT:
            stop = setup.invalidation_level + atr_buffer
            if stop <= entry:
                stop = entry + current_atr * 1.5
        else:
            return self._invalid(setup, direction, entry, 0, 0, 0, "Invalid trade direction for trade plan")

        risk = abs(entry - stop)
        if risk <= 0:
            return self._invalid(setup, direction, entry, stop, 0, 0, "Stop distance is zero or negative")

        # Context-free calls retain the established STATIC_EXIT_BASELINE.
        if context is None or not self.dynamic_targets_enabled:
            tp1 = float(setup.target_level)
            if (direction == TradeDirection.LONG and tp1 <= entry) or (direction == TradeDirection.SHORT and tp1 >= entry):
                tp1 = entry + risk * self.fallback_tp1_r * (1 if direction == TradeDirection.LONG else -1)
            tp2 = entry + risk * self.balanced_tp2_r * (1 if direction == TradeDirection.LONG else -1)
            return self._build(setup, entry, stop, tp1, tp2, "STATIC_EXIT_BASELINE", "SETUP_TARGET", "R_FALLBACK", ManagementProfile.BALANCED, ["LEGACY_PARITY"])

        mode, profile, tp2_r = self._mode_profile(setup, context)
        mapped = context.resistance_levels_by_timeframe if direction == TradeDirection.LONG else context.support_levels_by_timeframe
        levels = list(context.resistance_levels if direction == TradeDirection.LONG else context.support_levels)
        for timeframe_levels in mapped.values():
            levels.extend(timeframe_levels)
        structural = self._ahead(levels, entry, direction)
        setup_target = float(setup.target_level)
        if (direction == TradeDirection.LONG and setup_target > entry) or (direction == TradeDirection.SHORT and setup_target < entry):
            structural = self._ahead(structural + [setup_target], entry, direction)

        reasons = [mode, f"VOLATILITY_{context.volatility.value}"]
        if structural:
            tp1, tp1_source = structural[0], "NEAREST_STRUCTURE"
            reasons.append("TP1_FIRST_STRUCTURAL_OBSTACLE")
        else:
            tp1 = entry + risk * self.fallback_tp1_r * (1 if direction == TradeDirection.LONG else -1)
            tp1_source = "ATR_R_FALLBACK"
            reasons.append("TP1_FALLBACK_NO_STRUCTURE")

        beyond = [level for level in structural[1:] if (level >= tp1 if direction == TradeDirection.LONG else level <= tp1)]
        fallback_tp2 = entry + risk * tp2_r * (1 if direction == TradeDirection.LONG else -1)
        if beyond:
            tp2, tp2_source = beyond[0], "NEXT_HIGHER_TIMEFRAME_STRUCTURE"
            reasons.append("TP2_NEXT_STRUCTURE")
        else:
            tp2, tp2_source = fallback_tp2, "ATR_R_FALLBACK"
            reasons.append("TP2_FALLBACK_NO_NEXT_STRUCTURE")

        tp2 = max(tp1, tp2) if direction == TradeDirection.LONG else min(tp1, tp2)
        valid = stop < entry < tp1 <= tp2 if direction == TradeDirection.LONG else stop > entry > tp1 >= tp2
        if not valid:
            return self._invalid(setup, direction, entry, stop, tp1, tp2, "Invalid adaptive target ordering")
        return self._build(setup, entry, stop, tp1, tp2, mode, tp1_source, tp2_source, profile, reasons)

    def _build(self, setup, entry, stop, tp1, tp2, mode, tp1_source, tp2_source, profile, reasons) -> TradePlan:
        risk = abs(entry - stop)
        rr1, rr2 = abs(tp1 - entry) / risk, abs(tp2 - entry) / risk
        return TradePlan(
            setup_type=setup.setup_type, direction=setup.direction, entry_price=round(entry, 2),
            stop_loss=round(stop, 2), tp1=round(tp1, 2), tp2=round(tp2, 2),
            invalidation=round(setup.invalidation_level, 2), risk_reward=round(rr1, 2),
            risk_reward_tp1=round(rr1, 2), risk_reward_tp2=round(rr2, 2), target_mode=mode,
            tp1_source=tp1_source, tp2_source=tp2_source, stop_source="STRUCTURAL_INVALIDATION_ATR_BUFFER",
            target_confidence="HIGH" if "STRUCTURE" in tp1_source else "MEDIUM",
            target_reasons=reasons, management_profile=profile, is_valid=True,
        )

    @staticmethod
    def _invalid(setup, direction, entry, stop, tp1, tp2, reason) -> TradePlan:
        return TradePlan(setup_type=setup.setup_type, direction=direction, entry_price=entry, stop_loss=stop,
                         tp1=tp1, tp2=tp2, invalidation=setup.invalidation_level, risk_reward=0.0,
                         is_valid=False, invalidation_reason=reason)
