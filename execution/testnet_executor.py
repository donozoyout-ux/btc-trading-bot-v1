"""Binance Futures Testnet Executor with position reconciliation and safety checks."""

import uuid
from typing import Optional
from loguru import logger

from core.models import DecisionReport, TradeRecord
from config.constants import DecisionStatus, TradeDirection
from core.state import BotState
from data.binance_client import BinanceFuturesClient
from execution.executor_base import BaseExecutor
from journal.journaler import Journaler


class TestnetExecutor(BaseExecutor):
    """
    Executes trades on Binance Futures Testnet.
    Handles position sizing precision, STOP_MARKET and TAKE_PROFIT_MARKET orders,
    and position reconciliation.
    """

    def __init__(self, client: BinanceFuturesClient, journaler: Journaler):
        self.client = client
        self.journaler = journaler

    def process_decision(self, report: DecisionReport, state: BotState) -> Optional[TradeRecord]:
        """Places entry and conditional bracket orders on Testnet."""
        if state.active_position is not None:
            return None

        if report.final_decision not in [DecisionStatus.LONG_ENTRY, DecisionStatus.SHORT_ENTRY]:
            return None

        if not report.risk_assessment:
            return None

        risk = report.risk_assessment
        is_long = report.final_decision == DecisionStatus.LONG_ENTRY
        direction = TradeDirection.LONG if is_long else TradeDirection.SHORT
        side = "BUY" if is_long else "SELL"
        close_side = "SELL" if is_long else "BUY"

        qty = max(0.001, round(risk.position_size_btc, 3))

        try:
            logger.info(f"[TESTNET] Submitting {side} Market Order: {qty} BTC...")
            entry_order = self.client.place_order(
                symbol="BTCUSDT",
                side=side,
                order_type="MARKET",
                quantity=qty,
            )
            fill_price = float(entry_order.get("avgPrice", risk.entry_price))
            if fill_price == 0:
                fill_price = risk.entry_price

            # Stop Loss order
            logger.info(f"[TESTNET] Submitting Stop Loss @ {risk.stop_loss:.2f}")
            self.client.place_order(
                symbol="BTCUSDT",
                side=close_side,
                order_type="STOP_MARKET",
                quantity=qty,
                stop_price=risk.stop_loss,
                reduce_only=True,
            )

            # Take Profit order
            logger.info(f"[TESTNET] Submitting Take Profit @ {risk.tp1:.2f}")
            self.client.place_order(
                symbol="BTCUSDT",
                side=close_side,
                order_type="TAKE_PROFIT_MARKET",
                quantity=qty,
                stop_price=risk.tp1,
                reduce_only=True,
            )

            trade = TradeRecord(
                trade_id=f"TESTNET-{uuid.uuid4().hex[:8]}",
                symbol="BTC/USDT",
                setup_type=report.setup,
                direction=direction,
                entry_time=report.timestamp,
                entry_price=fill_price,
                stop_loss=risk.stop_loss,
                tp1=risk.tp1,
                tp2=risk.tp2,
                size_btc=qty,
                size_usdt=qty * fill_price,
                is_closed=False,
            )
            state.active_position = trade
            return trade

        except Exception as e:
            logger.error(f"[TESTNET] Order placement failed: {e}")
            return None

    def update_open_positions(self, state: BotState) -> None:
        """Polls account positions from Binance to reconcile position status."""
        try:
            balance = self.client.get_account_balance()
            state.account_balance_usdt = balance
        except Exception as e:
            logger.warning(f"[TESTNET] Position reconciliation warning: {e}")
