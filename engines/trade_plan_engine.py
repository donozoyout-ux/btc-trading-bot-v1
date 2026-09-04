"""Trade Plan Engine establishing pre-trade entry, structural stop, targets, and R:R."""

from typing import Optional
from core.models import SetupSignal, TriggerResult, TradePlan
from config.constants import TradeDirection


class TradePlanEngine:
    """
    Creates deterministic pre-trade plan prior to risk sizing and execution.
    Establishes:
    - Entry Price
    - Structural Stop Loss (below/above invalidation level + ATR buffer)
    - Take Profit 1 (TP1)
    - Take Profit 2 (TP2)
    - Invalidation Price
    - Pre-calculated Risk:Reward ratio
    """

    def __init__(self, atr_buffer_factor: float = 0.20):
        self.atr_buffer_factor = atr_buffer_factor

    def generate_plan(
        self,
        setup: SetupSignal,
        trigger: TriggerResult,
        current_atr: float,
    ) -> TradePlan:
        """Generates a complete TradePlan from confirmed setup and entry trigger."""
        entry_price = trigger.trigger_price
        direction = setup.direction
        atr_buffer = (current_atr * self.atr_buffer_factor) if current_atr > 0 else (entry_price * 0.001)

        if direction == TradeDirection.LONG:
            raw_sl = setup.invalidation_level - atr_buffer
            if raw_sl >= entry_price:
                raw_sl = entry_price - (current_atr * 1.5)
            stop_loss = raw_sl
            stop_dist = entry_price - stop_loss

            tp1 = setup.target_level
            if tp1 <= entry_price:
                tp1 = entry_price + (stop_dist * 1.5)

            reward_dist = tp1 - entry_price
            tp2 = entry_price + (stop_dist * 2.5)

        elif direction == TradeDirection.SHORT:
            raw_sl = setup.invalidation_level + atr_buffer
            if raw_sl <= entry_price:
                raw_sl = entry_price + (current_atr * 1.5)
            stop_loss = raw_sl
            stop_dist = stop_loss - entry_price

            tp1 = setup.target_level
            if tp1 >= entry_price:
                tp1 = entry_price - (stop_dist * 1.5)

            reward_dist = entry_price - tp1
            tp2 = entry_price - (stop_dist * 2.5)

        else:
            return TradePlan(
                setup_type=setup.setup_type,
                direction=TradeDirection.WAIT,
                entry_price=entry_price,
                stop_loss=0.0,
                tp1=0.0,
                tp2=0.0,
                invalidation=0.0,
                risk_reward=0.0,
                is_valid=False,
                invalidation_reason="Invalid trade direction for trade plan",
            )

        if stop_dist <= 0:
            return TradePlan(
                setup_type=setup.setup_type,
                direction=direction,
                entry_price=entry_price,
                stop_loss=stop_loss,
                tp1=tp1,
                tp2=tp2,
                invalidation=setup.invalidation_level,
                risk_reward=0.0,
                is_valid=False,
                invalidation_reason="Stop distance is zero or negative",
            )

        rr = reward_dist / stop_dist

        return TradePlan(
            setup_type=setup.setup_type,
            direction=direction,
            entry_price=round(entry_price, 2),
            stop_loss=round(stop_loss, 2),
            tp1=round(tp1, 2),
            tp2=round(tp2, 2),
            invalidation=round(setup.invalidation_level, 2),
            risk_reward=round(rr, 2),
            is_valid=True,
            invalidation_reason="",
        )
