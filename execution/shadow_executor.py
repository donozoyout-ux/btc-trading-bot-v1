"""Shadow Executor running virtual forward testing on live market feeds without financial risk."""

import uuid
from typing import Optional
from loguru import logger

from core.models import DecisionReport, TradeRecord, Candle
from config.constants import DecisionStatus, TradeDirection
from core.state import BotState
from execution.executor_base import BaseExecutor
from engines.exit_engine import ExitEngine
from journal.journaler import Journaler


class ShadowExecutor(BaseExecutor):
    """
    Executes trades virtually:
    - Places paper orders upon ENTRY decisions.
    - Tracks active virtual positions on incoming candles.
    - Logs realized virtual PnL, MFE, MAE to journal/trades.jsonl.
    """

    def __init__(self, exit_engine: ExitEngine, journaler: Journaler):
        self.exit_engine = exit_engine
        self.journaler = journaler

    def process_decision(self, report: DecisionReport, state: BotState) -> Optional[TradeRecord]:
        """Opens a virtual trade if report indicates entry and risk is accepted."""
        if state.active_position is not None:
            return None

        if report.final_decision not in [DecisionStatus.LONG_ENTRY, DecisionStatus.SHORT_ENTRY]:
            return None

        if not report.risk_assessment:
            return None

        risk = report.risk_assessment
        direction = TradeDirection.LONG if report.final_decision == DecisionStatus.LONG_ENTRY else TradeDirection.SHORT

        trade = TradeRecord(
            trade_id=f"SHADOW-{uuid.uuid4().hex[:8]}",
            symbol=report.symbol,
            setup_type=report.setup,
            direction=direction,
            entry_time=report.timestamp,
            entry_price=risk.entry_price,
            stop_loss=risk.stop_loss,
            tp1=risk.tp1,
            tp2=risk.tp2,
            size_btc=risk.position_size_btc,
            size_usdt=risk.position_size_usdt,
            is_closed=False,
        )

        state.active_position = trade
        logger.info(
            f"[SHADOW ENTRY] {direction.value} {trade.size_btc} BTC @ {trade.entry_price:.2f} | "
            f"SL: {trade.stop_loss:.2f} | TP1: {trade.tp1:.2f} | R:R: {risk.risk_reward:.2f}"
        )
        return trade

    def update_position_candle(self, state: BotState, candle: Candle) -> Optional[TradeRecord]:
        """Evaluates active virtual position against incoming candle."""
        trade = state.active_position
        if not trade or trade.is_closed:
            return None

        is_closed, reason, exit_price = self.exit_engine.evaluate_exit(trade, candle)
        if is_closed and reason:
            finalized = self.exit_engine.finalize_trade(trade, candle.timestamp, exit_price, reason)
            state.register_trade_closed(finalized)
            self.journaler.log_trade(finalized)

            logger.info(
                f"[SHADOW EXIT] {finalized.trade_id} {reason} @ {exit_price:.2f} | "
                f"PnL: ${finalized.pnl_usdt:+.2f} ({finalized.pnl_pct:+.2f}%) | "
                f"R: {finalized.r_multiple:+.2f}R | MFE: {finalized.mfe*100:.2f}% | MAE: {finalized.mae*100:.2f}%"
            )
            return finalized

        return None

    def update_open_positions(self, state: BotState) -> None:
        """Required by BaseExecutor interface."""
        pass
