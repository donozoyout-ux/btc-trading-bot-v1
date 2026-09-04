"""Risk Engine and Position Sizer evaluating TradePlan compliance against configured thresholds.

Every rejection returns an explicit RiskReasonCode and GuardType.
Every acceptance returns an empty reason_code and OTHER guard type.
"""

import uuid
from typing import Optional
from core.models import RiskAssessment, TradePlan, RiskReasonCode, GuardType
from config.constants import RiskDecision, TradeDirection, SetupType
from config.settings import BotSettings
from core.state import BotState


class RiskEngine:
    def __init__(self, settings: BotSettings):
        self.settings = settings

    def evaluate_risk(
        self,
        trade_plan: TradePlan,
        state: BotState,
        candidate_id: str = "",
    ) -> RiskAssessment:
        """Evaluates TradePlan compliance and calculates position sizing.
        Returns explicit reason_code and guard_type on every rejection.
        """
        # 1. Kill Switch / Guard Check — explicit guard type attribution
        if state.check_kill_switch(self.settings.MAX_DAILY_LOSS_PCT, self.settings.MAX_CONSECUTIVE_LOSSES):
            guard_type = state.guard_type
            reason_code = RiskReasonCode(guard_type.value) if guard_type in (
                RiskReasonCode.DAILY_LOSS_GUARD,
                RiskReasonCode.CONSECUTIVE_LOSS_GUARD,
                RiskReasonCode.EMERGENCY_LATCH,
            ) else RiskReasonCode.OTHER
            return RiskAssessment(
                decision=RiskDecision.REJECT_TRADE,
                direction=trade_plan.direction,
                entry_price=trade_plan.entry_price,
                stop_loss=trade_plan.stop_loss,
                tp1=trade_plan.tp1,
                tp2=trade_plan.tp2,
                risk_reward=trade_plan.risk_reward,
                rejection_reason=f"{guard_type.value}: {state.kill_switch_reason}",
                reason_code=reason_code,
                guard_type=guard_type,
                candidate_id=candidate_id,
                trade_plan=trade_plan,
            )

        # 2. Existing Position Check
        if state.active_position is not None:
            return RiskAssessment(
                decision=RiskDecision.REJECT_TRADE,
                direction=trade_plan.direction,
                entry_price=trade_plan.entry_price,
                stop_loss=trade_plan.stop_loss,
                tp1=trade_plan.tp1,
                tp2=trade_plan.tp2,
                risk_reward=trade_plan.risk_reward,
                rejection_reason="Already holding an active position",
                reason_code=RiskReasonCode.POSITION_ALREADY_OPEN,
                guard_type=GuardType.POSITION_STATE_BLOCK,
                candidate_id=candidate_id,
                trade_plan=trade_plan,
            )

        # 3. Trade Plan Validity Check
        if not trade_plan.is_valid:
            return RiskAssessment(
                decision=RiskDecision.REJECT_TRADE,
                direction=trade_plan.direction,
                entry_price=trade_plan.entry_price,
                stop_loss=trade_plan.stop_loss,
                tp1=trade_plan.tp1,
                tp2=trade_plan.tp2,
                risk_reward=trade_plan.risk_reward,
                rejection_reason=f"Invalid trade plan: {trade_plan.invalidation_reason}",
                reason_code=RiskReasonCode.INVALID_TRADE_PLAN,
                guard_type=GuardType.OTHER_RISK_CONTROL_BLOCK,
                candidate_id=candidate_id,
                trade_plan=trade_plan,
            )

        # 4. Configured Risk / Reward Filter
        if trade_plan.risk_reward < self.settings.MIN_RISK_REWARD:
            return RiskAssessment(
                decision=RiskDecision.REJECT_TRADE,
                direction=trade_plan.direction,
                entry_price=trade_plan.entry_price,
                stop_loss=trade_plan.stop_loss,
                tp1=trade_plan.tp1,
                tp2=trade_plan.tp2,
                risk_reward=trade_plan.risk_reward,
                rejection_reason=f"Insufficient R:R ({trade_plan.risk_reward:.2f} < {self.settings.MIN_RISK_REWARD:.2f})",
                reason_code=RiskReasonCode.BAD_RISK_REWARD,
                guard_type=GuardType.OTHER_RISK_CONTROL_BLOCK,
                candidate_id=candidate_id,
                trade_plan=trade_plan,
            )

        # 5. Position Sizing
        risk_pct = (
            self.settings.COUNTER_TREND_RISK_PCT
            if trade_plan.setup_type == SetupType.COUNTER_TREND_REACTION
            else self.settings.TREND_RISK_PCT
        )

        account_balance = state.account_balance_usdt
        stop_distance = abs(trade_plan.entry_price - trade_plan.stop_loss)

        if stop_distance <= 0:
            return RiskAssessment(
                decision=RiskDecision.REJECT_TRADE,
                direction=trade_plan.direction,
                entry_price=trade_plan.entry_price,
                stop_loss=trade_plan.stop_loss,
                tp1=trade_plan.tp1,
                tp2=trade_plan.tp2,
                risk_reward=trade_plan.risk_reward,
                rejection_reason="Stop distance is zero or negative",
                reason_code=RiskReasonCode.INVALID_STOP,
                guard_type=GuardType.OTHER_RISK_CONTROL_BLOCK,
                candidate_id=candidate_id,
                trade_plan=trade_plan,
            )

        risk_amount_usdt = account_balance * risk_pct
        position_size_btc = risk_amount_usdt / stop_distance
        position_size_usdt = position_size_btc * trade_plan.entry_price

        max_position_usdt = account_balance * self.settings.MAX_ACCOUNT_LEVERAGE
        if position_size_usdt > max_position_usdt:
            position_size_usdt = max_position_usdt
            position_size_btc = position_size_usdt / trade_plan.entry_price
            risk_amount_usdt = position_size_btc * stop_distance

        if position_size_btc <= 0:
            return RiskAssessment(
                decision=RiskDecision.REJECT_TRADE,
                direction=trade_plan.direction,
                entry_price=trade_plan.entry_price,
                stop_loss=trade_plan.stop_loss,
                tp1=trade_plan.tp1,
                tp2=trade_plan.tp2,
                risk_reward=trade_plan.risk_reward,
                rejection_reason="Calculated position size is zero or negative",
                reason_code=RiskReasonCode.INVALID_POSITION_SIZE,
                guard_type=GuardType.OTHER_RISK_CONTROL_BLOCK,
                candidate_id=candidate_id,
                trade_plan=trade_plan,
            )

        return RiskAssessment(
            decision=RiskDecision.ACCEPT_TRADE,
            direction=trade_plan.direction,
            entry_price=trade_plan.entry_price,
            stop_loss=trade_plan.stop_loss,
            tp1=trade_plan.tp1,
            tp2=trade_plan.tp2,
            risk_reward=trade_plan.risk_reward,
            position_size_btc=round(position_size_btc, 4),
            position_size_usdt=round(position_size_usdt, 2),
            risk_amount_usdt=round(risk_amount_usdt, 2),
            risk_pct_used=round(risk_pct, 4),
            rejection_reason="",
            reason_code=RiskReasonCode.OTHER,
            guard_type=GuardType.OTHER_RISK_CONTROL_BLOCK,
            candidate_id=candidate_id,
            trade_plan=trade_plan,
        )

    def generate_candidate_id(self) -> str:
        return f"CAND-{uuid.uuid4().hex[:12]}"
