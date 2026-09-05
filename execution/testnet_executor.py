"""Fail-closed Binance Futures TESTNET execution orchestration."""

from __future__ import annotations

from typing import Any, Dict, Optional
from loguru import logger

from config.constants import DecisionStatus, RiskDecision, TriggerState
from config.settings import BotSettings
from core.models import DecisionReport, TradeRecord
from core.state import BotState
from data.binance_execution_client import BinanceFuturesExecutionClient, ExecutionError
from execution.executor_base import BaseExecutor
from journal.execution_journal import ExecutionJournal


class TestnetExecutor(BaseExecutor):
    """Connect deterministic decisions to TESTNET, never to production."""

    __test__ = False

    def __init__(self, client: BinanceFuturesExecutionClient, journaler=None, *, settings: Optional[BotSettings] = None, execution_journal: Optional[ExecutionJournal] = None, event_notifier=None):
        self.client = client
        self.journaler = journaler
        self.settings = settings or BotSettings()
        self.execution_journal = execution_journal or ExecutionJournal(self.settings.JOURNAL_DIR)
        self.event_notifier = event_notifier
        self.last_processed_closed_5m_timestamp: Optional[int] = None
        self._processed_decisions: set[str] = set()
        self._known_position: Dict[str, Any] = {"symbol": "BTCUSDT", "position_amt": 0.0, "side": "FLAT"}
        self._protective_orders: list[Dict[str, Any]] = []
        self.last_order: Optional[Dict[str, Any]] = None
        self.last_telegram_event: Optional[Dict[str, Any]] = None
        persisted = self.execution_journal.read_state()
        if persisted:
            self.last_processed_closed_5m_timestamp = persisted.get("last_processed_closed_5m_timestamp")
            self.last_order = persisted.get("last_binance_order")
            self.last_telegram_event = persisted.get("last_telegram_event")
            self._protective_orders = list(persisted.get("protective_orders") or [])
        self.smoke_test_status = str(persisted.get("smoke_test") or "NOT_RUN")
        self.bot_status = str(persisted.get("bot_status") or "STOPPED")
        self.execution_thread = str(persisted.get("execution_thread") or "STOPPED")
        self.last_execution_result = persisted.get("last_execution_result")
        self.last_error = persisted.get("last_error")

    @property
    def orders_enabled(self) -> bool:
        return bool(self.settings.testnet_execution_enabled and self.client.testnet and self.client.configured)

    def _assert_execution_boundary(self) -> None:
        if not self.client.testnet or not self.settings.BINANCE_TESTNET:
            raise ExecutionError("MAINNET_EXECUTION_BLOCKED")
        if self.settings.ENV.strip().lower() != "testnet":
            raise ExecutionError("ENV_NOT_TESTNET")
        if not self.settings.ORDER_SUBMISSION_ENABLED:
            raise ExecutionError("ORDER_SUBMISSION_DISABLED")
        if self.settings.ACCOUNT_READ_ONLY or self.settings.SHADOW_MODE:
            raise ExecutionError("EXECUTION_FLAGS_CONFLICT")
        if not self.client.configured:
            raise ExecutionError("ACCOUNT_UNAVAILABLE")

    def _notify(self, event: str, payload: Dict[str, Any], key: str) -> None:
        if self.event_notifier is None:
            return
        try:
            self.last_telegram_event = self.event_notifier.notify(event, payload, dedupe_key=key)
        except Exception:
            logger.warning("TESTNET Telegram event unavailable")

    def _write_runtime_state(
        self,
        smoke_test: Optional[str] = None,
        *,
        bot_status: Optional[str] = None,
        execution_thread: Optional[str] = None,
        last_execution_result: Optional[str] = None,
        last_error: Optional[str] = None,
    ) -> None:
        if smoke_test is not None:
            self.smoke_test_status = smoke_test
        if bot_status is not None:
            self.bot_status = bot_status
        if execution_thread is not None:
            self.execution_thread = execution_thread
        if last_execution_result is not None:
            self.last_execution_result = last_execution_result
        if last_error is not None:
            self.last_error = last_error or None
        self.execution_journal.write_state({
            "environment": "TESTNET",
            "real_money": "DISABLED",
            "execution_enabled": self.orders_enabled,
            "bot_status": self.bot_status,
            "execution_thread": self.execution_thread,
            "position": self._known_position,
            "remaining_quantity": abs(float(self._known_position.get("position_amt") or 0)),
            "protective_orders": self._protective_orders,
            "last_binance_order": self.last_order,
            "last_telegram_event": self.last_telegram_event,
            "last_execution_result": self.last_execution_result,
            "last_error": self.last_error,
            "smoke_test": self.smoke_test_status,
            "last_processed_closed_5m_timestamp": self.last_processed_closed_5m_timestamp,
        })

    @staticmethod
    def _is_reduce_only(order: Dict[str, Any]) -> bool:
        truthy = lambda value: str(value).strip().lower() in {"1", "true", "yes", "on"}
        return truthy(order.get("reduceOnly") or order.get("reduce_only")) or truthy(order.get("closePosition"))

    def cleanup_flat_reduce_only_orders(self) -> Dict[str, Any]:
        """Cancel only orphan exits that cannot increase a flat TESTNET account."""
        position = self.client.get_position("BTCUSDT")
        if float(position.get("position_amt") or 0) != 0:
            return {"cancelled": 0, "remaining": [], "position": position}

        cancelled = 0
        for order in self.client.get_open_orders("BTCUSDT"):
            if self._is_reduce_only(order) and order.get("orderId") is not None:
                self.client.cancel_order("BTCUSDT", int(order["orderId"]))
                cancelled += 1
        for order in self.client.get_open_algo_orders("BTCUSDT"):
            if self._is_reduce_only(order) and order.get("algoId") is not None:
                self.client.cancel_algo_order(algo_id=int(order["algoId"]))
                cancelled += 1

        remaining = self.client.get_open_orders("BTCUSDT") + self.client.get_open_algo_orders("BTCUSDT")
        if cancelled:
            self._protective_orders = []
            self.execution_journal.record(
                decision_id=None,
                action="STALE_PROTECTION_CANCELLED",
                status="CONFIRMED",
                reason=f"Cancelled {cancelled} orphan reduce-only TESTNET order(s) while flat",
                position_after=position,
            )
        return {"cancelled": cancelled, "remaining": remaining, "position": position}

    def recover_from_exchange(self) -> Dict[str, Any]:
        self._assert_execution_boundary()
        position = self.client.get_position("BTCUSDT")
        regular_orders = self.client.get_open_orders("BTCUSDT")
        algo_orders = self.client.get_open_algo_orders("BTCUSDT")
        orders = regular_orders + algo_orders
        recovered = float(position.get("position_amt") or 0) != 0
        self._known_position = position
        self._protective_orders = [
            {
                "binance_order_id": order.get("algoId"),
                "client_order_id": order.get("clientAlgoId"),
                "type": order.get("orderType"),
                "status": order.get("algoStatus"),
            }
            for order in algo_orders
        ]
        if recovered:
            self.execution_journal.record(decision_id=None, action="RECOVERED_FROM_EXCHANGE", side=position.get("side"), quantity=abs(float(position.get("position_amt") or 0)), price=position.get("entry_price"), status="RECOVERED", position_after=position)
        self._write_runtime_state()
        return {"position": position, "open_orders": orders, "recovered": recovered}

    def reconcile_position(self) -> Dict[str, Any]:
        before = self._known_position
        after = self.client.get_position("BTCUSDT")
        self._known_position = after
        if float(before.get("position_amt") or 0) != 0 and float(after.get("position_amt") or 0) == 0:
            completed: Optional[Dict[str, Any]] = None
            for known in self._protective_orders:
                try:
                    raw = self.client.get_algo_order(algo_id=int(known["binance_order_id"]))
                    if raw.get("actualOrderId") or str(raw.get("algoStatus") or "").upper() in {"TRIGGERED", "FINISHED", "FILLED"}:
                        completed = raw
                        break
                except Exception:
                    continue
            for order in self.client.get_open_orders("BTCUSDT"):
                if order.get("reduceOnly") and order.get("orderId") is not None:
                    try:
                        self.client.cancel_order("BTCUSDT", int(order["orderId"]))
                    except Exception:
                        logger.warning("Unable to cancel stale TESTNET protective order")
            try:
                self.client.cancel_all_algo_open_orders("BTCUSDT")
            except Exception:
                logger.warning("Unable to cancel stale TESTNET algo protection")
            completed_type = str((completed or {}).get("orderType") or "")
            if completed_type == "STOP_MARKET":
                action, event = "STOP_LOSS", "STOP_LOSS"
            elif completed_type == "TAKE_PROFIT_MARKET":
                action, event = "TAKE_PROFIT", "TAKE_PROFIT"
            else:
                action, event = "POSITION_CLOSED", "ORDER_CLOSED"
            self.execution_journal.record(decision_id=None, action=action, status="CONFIRMED", price=(completed or {}).get("actualPrice"), binance_order_id=(completed or {}).get("actualOrderId") or (completed or {}).get("algoId"), position_before=before, position_after=after)
            self._notify(event, {"message": f"BTC TESTNET position closed: {action}", "mode": "TESTNET"}, f"{event}:BTCUSDT:{(completed or {}).get('algoId') or 'EXTERNAL'}")
            self._protective_orders = []
        self._write_runtime_state()
        return after

    def manage_existing_position(self, state: BotState) -> Dict[str, Any]:
        """Reconcile an existing position and verify exchange-side protection."""
        try:
            position = self.reconcile_position()
        except Exception:
            state.activate_emergency_latch("EXCHANGE_RECONCILIATION_FAILURE")
            self.execution_journal.record(decision_id=None, action="RECONCILIATION_FAILURE", status="KILL_SWITCH", reason="Exchange state unavailable")
            self._notify("KILL_SWITCH", {"reason": "TESTNET reconciliation failed; new entries blocked"}, "KILL_SWITCH:RECONCILIATION_FAILURE")
            raise
        if float(position.get("position_amt") or 0) == 0:
            cleanup = self.cleanup_flat_reduce_only_orders()
            remaining_orders = cleanup["remaining"]
            if remaining_orders:
                state.activate_emergency_latch("UNEXPECTED_OPEN_ORDERS")
                self.execution_journal.record(
                    decision_id=None,
                    action="UNEXPECTED_OPEN_ORDERS",
                    status="KILL_SWITCH",
                    reason="Flat account has existing exchange orders; manual review required",
                    position_after=position,
                )
                self._notify(
                    "KILL_SWITCH",
                    {"reason": "Flat TESTNET account has existing orders; new entries blocked"},
                    "KILL_SWITCH:UNEXPECTED_OPEN_ORDERS",
                )
                self._write_runtime_state()
                return {
                    "status": "OPEN_ORDERS_PRESENT",
                    "position": position,
                    "open_orders": remaining_orders,
                }
            return {"status": "FLAT", "position": position, "stale_orders_cancelled": cleanup["cancelled"]}
        orders = self.client.get_open_algo_orders("BTCUSDT")
        protective_types = {
            str(order.get("orderType"))
            for order in orders
            if bool(order.get("reduceOnly")) or bool(order.get("closePosition"))
        }
        protected = "STOP_MARKET" in protective_types and "TAKE_PROFIT_MARKET" in protective_types
        if not protected:
            state.activate_emergency_latch("UNPROTECTED_TESTNET_POSITION")
            self.execution_journal.record(decision_id=None, action="UNPROTECTED_POSITION", status="KILL_SWITCH", reason="Missing exchange-side SL/TP", position_after=position)
            self._notify("KILL_SWITCH", {"reason": "Unprotected TESTNET position; new entries blocked"}, "KILL_SWITCH:UNPROTECTED_POSITION")
            self._write_runtime_state()
            return {"status": "UNPROTECTED_POSITION", "position": position, "open_orders": orders}
        return {"status": "POSITION_MANAGEMENT", "position": position, "open_orders": orders}

    def _record_order(self, decision_id: str, action: str, order: Dict[str, Any], before: Dict[str, Any], after: Dict[str, Any]) -> None:
        self.last_order = order
        self.execution_journal.record(decision_id=decision_id, action=action, side=order.get("side"), quantity=order.get("executed_quantity") or order.get("requested_quantity"), price=order.get("average_fill_price"), binance_order_id=order.get("binance_order_id"), status=str(order.get("status") or "UNKNOWN"), position_before=before, position_after=after)

    def _place_protection(self, decision_id: str, side: str, quantity: float, plan: Dict[str, Any]) -> list[Dict[str, Any]]:
        close_side = "SELL" if side == "BUY" else "BUY"
        orders = [
            self.client.place_protective_order("BTCUSDT", close_side, "STOP_MARKET", quantity, float(plan["stop_loss"])),
            self.client.place_protective_order("BTCUSDT", close_side, "TAKE_PROFIT_MARKET", quantity, float(plan["tp1"])),
            self.client.place_protective_order("BTCUSDT", close_side, "TAKE_PROFIT_MARKET", quantity, float(plan["tp2"])),
        ]
        open_ids = {row.get("algoId") for row in self.client.get_open_algo_orders("BTCUSDT")}
        if not all(order.get("binance_order_id") in open_ids for order in orders):
            raise ExecutionError("PROTECTION_FAILURE")
        self._protective_orders = list(orders)
        for order in orders:
            self._record_order(decision_id, "PROTECTIVE_ORDER", order, self._known_position, self._known_position)
        return orders

    def process_snapshot(self, snapshot: Dict[str, Any], state: BotState) -> Optional[Dict[str, Any]]:
        self._assert_execution_boundary()
        candle_rows = snapshot.get("candles", {}).get("5m", [])
        if not candle_rows:
            raise ExecutionError("STALE_MARKET_DATA")
        candle_ts = int(candle_rows[-1]["time"] * 1000)
        if candle_ts == self.last_processed_closed_5m_timestamp:
            return {"status": "DUPLICATE_CANDLE"}
        self.last_processed_closed_5m_timestamp = candle_ts
        decision_id = str(snapshot.get("decision_id") or snapshot.get("decision", {}).get("evaluation_id") or candle_ts)
        if decision_id in self._processed_decisions:
            return {"status": "DUPLICATE_SIGNAL"}
        self._processed_decisions.add(decision_id)
        open_positions = [
            row
            for row in self.client.get_positions()
            if float(row.get("positionAmt") or row.get("position_amt") or 0) != 0
        ]
        position = self.client.get_position("BTCUSDT")
        self._known_position = position
        if len(open_positions) >= self.settings.MAX_OPEN_POSITIONS or float(position.get("position_amt") or 0) != 0 or state.active_position is not None:
            self._write_runtime_state()
            return {"status": "POSITION_ALREADY_OPEN"}
        decision = snapshot.get("decision", {})
        strategy = snapshot.get("strategy", {})
        system = snapshot.get("system_state", {})
        sources = snapshot.get("sources", {})
        final = str(snapshot.get("final_decision") or decision.get("final_decision"))
        eligible = all([final in (DecisionStatus.LONG_ENTRY.value, DecisionStatus.SHORT_ENTRY.value), strategy.get("eligible") is True, strategy.get("entry_trigger_state") == TriggerState.ENTRY_READY.value, decision.get("risk_status") == RiskDecision.ACCEPT_TRADE.value, not system.get("kill_switch"), sources.get("binance", {}).get("status") == "HEALTHY"])
        if not eligible:
            self._write_runtime_state()
            return {"status": "NO_ELIGIBLE_SIGNAL"}
        plan = strategy.get("trade_plan") or decision.get("trade_plan") or {}
        risk = decision.get("risk_assessment") or {}
        raw_quantity = float(risk.get("position_size_btc") or 0)
        if raw_quantity <= 0:
            raise ExecutionError("INVALID_POSITION_SIZE")
        side = "BUY" if final == DecisionStatus.LONG_ENTRY.value else "SELL"
        quantity = self.client.normalize_quantity("BTCUSDT", raw_quantity, market=True, price=decision.get("price"))
        before = position
        entry = self.client.place_market_order("BTCUSDT", side, quantity, reduce_only=False, client_order_id=f"btc-{decision_id[-24:]}")
        after = self.client.get_position("BTCUSDT")
        if float(entry.get("executed_quantity") or 0) <= 0 or float(after.get("position_amt") or 0) == 0:
            raise ExecutionError("ENTRY_RECONCILIATION_FAILED")
        self._known_position = after
        self._record_order(decision_id, "ENTRY", entry, before, after)
        try:
            protections = self._place_protection(decision_id, side, abs(float(after["position_amt"])), plan)
        except Exception as exc:
            self.client.close_position_market("BTCUSDT")
            final_position = self.client.get_position("BTCUSDT")
            self._known_position = final_position
            try:
                self.client.cancel_all_algo_open_orders("BTCUSDT")
            except Exception:
                logger.warning("Unable to cancel TESTNET protection after flatten")
            self._protective_orders = []
            self.execution_journal.record(decision_id=decision_id, action="PROTECTION_FAILURE", status="POSITION_FLATTENED" if float(final_position.get("position_amt") or 0) == 0 else "FLATTEN_FAILED", reason=getattr(exc, "category", type(exc).__name__), position_before=after, position_after=final_position)
            self._notify("PROTECTION_FAILURE", {"message": "Entry protection failed; TESTNET position flatten attempted"}, f"PROTECTION_FAILURE:{decision_id}")
            raise ExecutionError("PROTECTION_FAILURE") from None
        self._notify("ORDER_OPENED", {"side": after.get("side"), "entry": entry.get("average_fill_price"), "size": abs(float(after.get("position_amt") or 0)), "stop": plan.get("stop_loss"), "tp1": plan.get("tp1"), "tp2": plan.get("tp2")}, f"ORDER_OPENED:{decision_id}")
        self._write_runtime_state()
        return {"status": "OPENED", "entry": entry, "position": after, "protective_orders": protections}

    def process_decision(self, report: DecisionReport, state: BotState) -> Optional[TradeRecord]:
        logger.warning("Full deterministic snapshot required for TESTNET execution")
        return None

    def update_open_positions(self, state: BotState) -> None:
        self.reconcile_position()
