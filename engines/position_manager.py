"""Pure deterministic active-position classification; never places orders."""

from typing import Optional

from config.constants import ManagementProfile, MarketRegime, PositionManagementState, StructureType, TradeDirection, VolatilityLevel
from core.models import PositionManagementDecision


class PositionManager:
    def __init__(self, *, recovery_wait_enabled: bool = True, early_exit_enabled: bool = True,
                 breakeven_min_r: float = 1.0, stop_tighten_min_r: float = 1.5,
                 stop_lock_r: float = 0.25, target_replan_enabled: bool = True,
                 target_replan_min_r: float = 1.5, target_replan_cooldown_bars: int = 3,
                 max_target_replans: int = 2):
        self.recovery_wait_enabled = recovery_wait_enabled
        self.early_exit_enabled = early_exit_enabled
        self.breakeven_min_r = breakeven_min_r
        self.stop_tighten_min_r = stop_tighten_min_r
        self.stop_lock_r = stop_lock_r
        self.target_replan_enabled = target_replan_enabled
        self.target_replan_min_r = target_replan_min_r
        self.target_replan_cooldown_bars = target_replan_cooldown_bars
        self.max_target_replans = max_target_replans

    @staticmethod
    def normalize_momentum(direction: TradeDirection, trend: Optional[str]) -> tuple[bool, bool, bool]:
        """Return (supportive, opposing, available) for Chart Reader V3 trend vocabulary."""
        normalized = str(trend or "UNAVAILABLE").strip().upper()
        if normalized not in {"UP", "DOWN", "RANGE"}:
            return False, False, False
        supportive = normalized == ("UP" if direction == TradeDirection.LONG else "DOWN")
        opposing = normalized == ("DOWN" if direction == TradeDirection.LONG else "UP")
        return supportive, opposing, True

    @staticmethod
    def normalize_volume(volume_state: Optional[str]) -> tuple[bool, bool]:
        """Return (supportive_or_acceptable, available) for Chart Reader V3 volume vocabulary."""
        normalized = str(volume_state or "UNAVAILABLE").strip().upper()
        if normalized not in {"EXPANSION", "NORMAL", "CONTRACTION"}:
            return False, False
        return normalized in {"EXPANSION", "NORMAL"}, True

    def evaluate(self, *, direction: TradeDirection, entry: float, initial_stop: float, current_stop: float,
                 mark: float, initial_size: float, current_size: float, structure: StructureType,
                 last_bos: Optional[str], last_choch: Optional[str], regime: MarketRegime,
                 volatility: VolatilityLevel = VolatilityLevel.NORMAL, momentum_support: bool = True,
                 momentum_opposing: bool = False, momentum_available: bool = True,
                 volume_support: bool = True, volume_available: bool = True,
                 data_healthy: bool = True, candle_closed: bool = True,
                 candle_timestamp: Optional[int] = None, current_tp2: Optional[float] = None,
                 candidate_tp2: Optional[float] = None, target_replan_count: int = 0,
                 last_target_replan_at: Optional[int] = None, mfe_r: float = 0.0,
                 mae_r: float = 0.0, management_profile: ManagementProfile = ManagementProfile.BALANCED) -> PositionManagementDecision:
        risk = abs(entry - initial_stop)
        if risk <= 0 or not candle_closed or not data_healthy:
            return self._decision(PositionManagementState.NO_CHANGE, ["MARKET_ANALYSIS_UNAVAILABLE_KEEP_EXISTING_PROTECTION"], 0, mfe_r, mae_r,
                                  True, True, True, momentum_support, momentum_opposing, momentum_available,
                                  volume_support, volume_available, management_profile, target_replan_count, last_target_replan_at)
        current_r = ((mark - entry) if direction == TradeDirection.LONG else (entry - mark)) / risk
        opposite_structure = StructureType.BEARISH if direction == TradeDirection.LONG else StructureType.BULLISH
        opposite_bos = "BEARISH_BOS" if direction == TradeDirection.LONG else "BULLISH_BOS"
        opposite_choch = "BEARISH_CHOCH" if direction == TradeDirection.LONG else "BULLISH_CHOCH"
        opposite_regimes = {MarketRegime.BEAR, MarketRegime.STRONG_BEAR} if direction == TradeDirection.LONG else {MarketRegime.BULL, MarketRegime.STRONG_BULL}
        opposite_bos_confirmed = structure == opposite_structure and last_bos == opposite_bos
        opposite_choch_seen = last_choch == opposite_choch
        structure_valid = not opposite_bos_confirmed
        regime_support = regime not in opposite_regimes
        invalidation_hit = mark <= initial_stop if direction == TradeDirection.LONG else mark >= initial_stop
        size_increased = current_size > initial_size * (1 + 1e-9)

        if size_increased:
            return self._decision(PositionManagementState.NO_CHANGE, ["POSITION_SIZE_INCREASE_DETECTED", "AVERAGING_DOWN_BLOCKED"], current_r, mfe_r, mae_r,
                                  False, structure_valid, regime_support, momentum_support, momentum_opposing, momentum_available,
                                  volume_support, volume_available, management_profile, target_replan_count, last_target_replan_at)
        stop_widened = current_stop < initial_stop if direction == TradeDirection.LONG else current_stop > initial_stop
        if stop_widened:
            return self._decision(PositionManagementState.PROTECT, ["STOP_WIDENING_DETECTED", "RESTORE_INITIAL_MAX_LOSS"], current_r, mfe_r, mae_r,
                                  True, structure_valid, regime_support, momentum_support, momentum_opposing, momentum_available,
                                  volume_support, volume_available, management_profile, target_replan_count, last_target_replan_at,
                                  stop_action={"action": "TIGHTEN_STOP", "new_stop": initial_stop, "quantity_increase": 0})
        invalid_reasons = []
        if invalidation_hit:
            invalid_reasons.append("STRUCTURAL_INVALIDATION_HIT")
        if opposite_bos_confirmed:
            invalid_reasons.append("CONFIRMED_OPPOSITE_STRUCTURE_BREAK")
        if opposite_choch_seen and (not regime_support or momentum_opposing):
            invalid_reasons.append("OPPOSITE_CHOCH_WITH_CONFIRMATION")
        if not regime_support and (opposite_bos_confirmed or opposite_choch_seen) and momentum_opposing:
            invalid_reasons.append("REGIME_FLIP_CONFIRMED")
        if invalid_reasons and self.early_exit_enabled:
            return self._decision(PositionManagementState.EXIT_EARLY, invalid_reasons, current_r, mfe_r, mae_r,
                                  False, structure_valid, regime_support, momentum_support, momentum_opposing, momentum_available,
                                  volume_support, volume_available, management_profile, target_replan_count, last_target_replan_at,
                                  target_action={"action": "CLOSE_FULL", "quantity_increase": 0})
        if invalid_reasons:
            return self._decision(PositionManagementState.NO_CHANGE, invalid_reasons + ["EARLY_EXIT_DISABLED_KEEP_EXCHANGE_STOP"], current_r, mfe_r, mae_r,
                                  False, structure_valid, regime_support, momentum_support, momentum_opposing, momentum_available,
                                  volume_support, volume_available, management_profile, target_replan_count, last_target_replan_at)
        if current_r < 0 and current_r > -1 and structure_valid and (regime_support or momentum_support):
            state = PositionManagementState.RECOVERY_WAIT if self.recovery_wait_enabled else PositionManagementState.HOLD
            reasons = ["TEMPORARY_ADVERSE_MOVE", "THESIS_STILL_VALID", "STRUCTURE_INTACT"]
            if momentum_opposing:
                reasons.append("MOMENTUM_OPPOSING_BUT_NOT_STRUCTURALLY_CONFIRMED")
            if opposite_choch_seen:
                reasons.append("THESIS_WEAKENING_CHOCH")
            return self._decision(state, reasons, current_r, mfe_r, mae_r,
                                  True, True, regime_support, momentum_support, momentum_opposing, momentum_available,
                                  volume_support, volume_available, management_profile, target_replan_count, last_target_replan_at)

        strong_regime = regime in ({MarketRegime.STRONG_BULL} if direction == TradeDirection.LONG else {MarketRegime.STRONG_BEAR})
        cooldown_ok = last_target_replan_at is None or candle_timestamp is None or candle_timestamp - last_target_replan_at >= self.target_replan_cooldown_bars * 300_000
        target_extends = current_tp2 is not None and candidate_tp2 is not None and (candidate_tp2 > current_tp2 if direction == TradeDirection.LONG else candidate_tp2 < current_tp2)
        if (self.target_replan_enabled and current_r >= self.target_replan_min_r and strong_regime and momentum_support and volume_support
                and not opposite_choch_seen
                and target_extends and cooldown_ok and target_replan_count < self.max_target_replans):
            return self._decision(PositionManagementState.TARGET_REPLAN, ["STRONG_TREND_CONTINUATION", "CLOSED_5M_CONFIRMATION", "NEXT_STRUCTURE_AVAILABLE"], current_r, mfe_r, mae_r,
                                  True, True, True, True, False, True, True, True, management_profile, target_replan_count + 1, candle_timestamp,
                                  target_action={"action": "REPLACE_TP2", "new_tp2": candidate_tp2, "quantity_increase": 0}, target_replan_reason="STRONG_TREND_CONTINUATION")

        if current_r >= self.breakeven_min_r and structure_valid:
            desired = entry
            reason = "BREAKEVEN_OBJECTIVE_PROGRESS"
            state = PositionManagementState.PROTECT
            if current_r >= self.stop_tighten_min_r and momentum_support:
                desired = entry + risk * self.stop_lock_r * (1 if direction == TradeDirection.LONG else -1)
                reason, state = "LOCK_PROFIT_AFTER_STRUCTURE_PROGRESS", PositionManagementState.TIGHTEN_STOP
            safe_stop = max(current_stop, desired) if direction == TradeDirection.LONG else min(current_stop, desired)
            if safe_stop != current_stop:
                reasons = [reason, "STOP_NEVER_WIDENS"]
                if opposite_choch_seen:
                    reasons.append("THESIS_WEAKENING_CHOCH")
                return self._decision(state, reasons, current_r, mfe_r, mae_r, True, True, regime_support,
                                      momentum_support, momentum_opposing, momentum_available, volume_support, volume_available,
                                      management_profile, target_replan_count, last_target_replan_at,
                                      stop_action={"action": "TIGHTEN_STOP", "new_stop": safe_stop, "quantity_increase": 0})
        hold_reasons = ["THESIS_STILL_VALID", "NO_OBJECTIVE_MANAGEMENT_CHANGE"]
        if opposite_choch_seen:
            hold_reasons.append("THESIS_WEAKENING_CHOCH")
        return self._decision(PositionManagementState.HOLD, hold_reasons, current_r, mfe_r, mae_r,
                              True, structure_valid, regime_support, momentum_support, momentum_opposing, momentum_available,
                              volume_support, volume_available, management_profile, target_replan_count, last_target_replan_at)

    @staticmethod
    def _decision(state, reasons, current_r, mfe_r, mae_r, thesis, structure, regime,
                  momentum, momentum_opposing, momentum_available, volume, volume_available,
                  profile, replan_count, last_replan, target_action=None, stop_action=None, target_replan_reason=None):
        return PositionManagementDecision(
            state=state, reason_codes=reasons,
            confidence="HIGH" if thesis and structure and "THESIS_WEAKENING_CHOCH" not in reasons else "MEDIUM",
            current_r=round(current_r, 4), mfe_r=mfe_r, mae_r=mae_r, thesis_valid=thesis,
            structure_valid=structure, regime_support=regime, momentum_support=momentum,
            momentum_opposing=momentum_opposing, momentum_available=momentum_available,
            volume_support=volume, volume_available=volume_available,
            target_action=target_action or {}, stop_action=stop_action or {},
            management_profile=profile, target_replan_count=replan_count,
            last_target_replan_at=last_replan, target_replan_reason=target_replan_reason,
        )
