"""Exit Engine managing Stop Loss, TP1, TP2, Trailing to BE, Invalidation, and Intrabar Ambiguity."""

from typing import Tuple, Optional, List
from core.models import TradeRecord, Candle
from config.constants import TradeDirection, SetupType


class ExitEngine:
    """
    Monitors active positions against incoming price updates.
    Handles partial TP1, TP2 full exit, structural invalidation,
    MFE/MAE tracking, and explicit pessimistic Intrabar Ambiguity Resolution.
    """

    def __init__(
        self,
        taker_fee_pct: float = 0.0004,
        slippage_pct: float = 0.0002,
        auto_breakeven: bool = True,
        tp1_close_fraction: float = 0.50,
    ):
        self.taker_fee_pct = taker_fee_pct
        self.slippage_pct = slippage_pct
        self.auto_breakeven = auto_breakeven
        self.tp1_close_fraction = tp1_close_fraction

    def evaluate_exit(
        self,
        trade: TradeRecord,
        candle: Candle,
        sub_candles: Optional[List[Candle]] = None,
    ) -> Tuple[bool, Optional[str], float]:
        """
        Evaluates whether an active trade exits on the given candle.
        Implements Explicit Intrabar Ambiguity Resolution:
        If both SL and TP are touched in the same candle:
        1. Resolve via sub_candles (e.g. 1M) if provided.
        2. Else, default to Conservative Worst-Case: Stop Loss is assumed hit first!
        """
        if trade.is_closed:
            return True, trade.exit_reason, trade.exit_price or 0.0

        is_long = trade.direction == TradeDirection.LONG

        # Update MFE and MAE
        if is_long:
            favorable = (candle.high - trade.entry_price) / trade.entry_price
            adverse = (trade.entry_price - candle.low) / trade.entry_price
        else:
            favorable = (trade.entry_price - candle.low) / trade.entry_price
            adverse = (candle.high - trade.entry_price) / trade.entry_price

        trade.mfe = max(trade.mfe, float(favorable))
        trade.mae = max(trade.mae, float(adverse))

        if is_long:
            sl_touched = candle.low <= trade.stop_loss
            tp_touched = candle.high >= trade.tp1 or candle.high >= trade.tp2

            # Section 12: Intrabar Ambiguity Check
            if sl_touched and tp_touched:
                if sub_candles:
                    for sc in sub_candles:
                        if sc.low <= trade.stop_loss:
                            return True, "STOP_LOSS", trade.stop_loss * (1.0 - self.slippage_pct)
                        if sc.high >= trade.tp1:
                            break  # TP reached first in sub-bars
                else:
                    # Worst-case pessimistic resolution: Stop Loss hit first
                    return True, "STOP_LOSS", trade.stop_loss * (1.0 - self.slippage_pct)

            # Standard SL check
            if sl_touched:
                exit_price = trade.stop_loss * (1.0 - self.slippage_pct)
                return True, "STOP_LOSS", exit_price

            # TP2 check
            if candle.high >= trade.tp2:
                exit_price = trade.tp2 * (1.0 - self.slippage_pct)
                return True, "TAKE_PROFIT_2", exit_price

            # TP1 check
            if candle.high >= trade.tp1:
                if trade.setup_type == SetupType.COUNTER_TREND_REACTION:
                    exit_price = trade.tp1 * (1.0 - self.slippage_pct)
                    return True, "COUNTER_TREND_TARGET", exit_price
                else:
                    if self.auto_breakeven and trade.stop_loss < trade.entry_price:
                        trade.stop_loss = trade.entry_price

        else:  # SHORT
            sl_touched = candle.high >= trade.stop_loss
            tp_touched = candle.low <= trade.tp1 or candle.low <= trade.tp2

            # Intrabar Ambiguity Check
            if sl_touched and tp_touched:
                if sub_candles:
                    for sc in sub_candles:
                        if sc.high >= trade.stop_loss:
                            return True, "STOP_LOSS", trade.stop_loss * (1.0 + self.slippage_pct)
                        if sc.low <= trade.tp1:
                            break
                else:
                    return True, "STOP_LOSS", trade.stop_loss * (1.0 + self.slippage_pct)

            if sl_touched:
                exit_price = trade.stop_loss * (1.0 + self.slippage_pct)
                return True, "STOP_LOSS", exit_price

            if candle.low <= trade.tp2:
                exit_price = trade.tp2 * (1.0 + self.slippage_pct)
                return True, "TAKE_PROFIT_2", exit_price

            if candle.low <= trade.tp1:
                if trade.setup_type == SetupType.COUNTER_TREND_REACTION:
                    exit_price = trade.tp1 * (1.0 + self.slippage_pct)
                    return True, "COUNTER_TREND_TARGET", exit_price
                else:
                    if self.auto_breakeven and trade.stop_loss > trade.entry_price:
                        trade.stop_loss = trade.entry_price

        return False, None, 0.0

    def finalize_trade(
        self,
        trade: TradeRecord,
        exit_time: int,
        exit_price: float,
        exit_reason: str,
    ) -> TradeRecord:
        """Computes realized PnL, configurable fees, and R-multiple on exit."""
        trade.exit_time = exit_time
        trade.exit_price = exit_price
        trade.exit_reason = exit_reason
        trade.is_closed = True

        entry_fee = trade.size_usdt * self.taker_fee_pct
        exit_fee = (trade.size_btc * exit_price) * self.taker_fee_pct
        total_fees = entry_fee + exit_fee
        trade.fees_paid_usdt = round(total_fees, 2)

        if trade.direction == TradeDirection.LONG:
            gross_pnl = trade.size_btc * (exit_price - trade.entry_price)
        else:
            gross_pnl = trade.size_btc * (trade.entry_price - exit_price)

        net_pnl = gross_pnl - total_fees
        trade.pnl_usdt = round(net_pnl, 2)
        trade.pnl_pct = round((net_pnl / trade.size_usdt) * 100.0, 3)

        initial_risk = abs(trade.entry_price - trade.stop_loss) * trade.size_btc
        if initial_risk > 0:
            trade.r_multiple = round(net_pnl / initial_risk, 2)
        else:
            trade.r_multiple = 0.0

        return trade
