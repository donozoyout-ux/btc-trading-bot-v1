"""End-to-end TESTNET smoke test and closed-5m execution loop."""

from __future__ import annotations

import time
from typing import Any, Dict, Optional

from config.settings import BotSettings, get_settings
from core.state import BotState
from data.binance_execution_client import BinanceFuturesExecutionClient, ExecutionError
from execution.safer_testnet_executor import SaferTestnetExecutor
from journal.execution_journal import ExecutionJournal
from notifications.telegram_client import TelegramClient
from notifications.telegram_notifier import TelegramEventNotifier


class TestnetExecutionRuntime:
    __test__ = False

    FATAL_EXECUTION_ERRORS = {
        "MAINNET_EXECUTION_BLOCKED", "ENV_NOT_TESTNET", "ORDER_SUBMISSION_DISABLED",
        "EXECUTION_FLAGS_CONFLICT", "ACCOUNT_UNAVAILABLE", "INVALID_API_KEY", "INVALID_SIGNATURE",
    }

    def __init__(self, settings: Optional[BotSettings] = None, *, client=None, dashboard_runtime=None, sleep_fn=time.sleep, status_callback=None):
        self.settings = settings or get_settings()
        if not self.settings.BINANCE_API_KEY or not self.settings.BINANCE_API_SECRET:
            raise ExecutionError("ACCOUNT_UNAVAILABLE")
        self.client = client or BinanceFuturesExecutionClient(
            self.settings.BINANCE_API_KEY,
            self.settings.BINANCE_API_SECRET,
            testnet=self.settings.BINANCE_TESTNET,
            recv_window=self.settings.BINANCE_RECV_WINDOW,
        )
        self.journal = ExecutionJournal(self.settings.JOURNAL_DIR)
        telegram = TelegramClient(self.settings.TELEGRAM_BOT_TOKEN, self.settings.TELEGRAM_CHAT_ID, enabled=self.settings.TELEGRAM_ENABLED)
        self.notifier = TelegramEventNotifier(telegram, self.settings.TELEGRAM_DEDUPE_TTL_SECONDS)
        self.executor = SaferTestnetExecutor(self.client, settings=self.settings, execution_journal=self.journal, event_notifier=self.notifier)
        if dashboard_runtime is None:
            from dashboard_server import DashboardRuntime
            dashboard_runtime = DashboardRuntime(settings=self.settings)
        self.dashboard = dashboard_runtime
        self.state: BotState = self.dashboard.state
        self.sleep_fn = sleep_fn
        self.status_callback = status_callback
        self._smoke_attempted = False
        self._smoke_result: Optional[Dict[str, Any]] = None

    def _status(self, **changes: Any) -> None:
        self.executor._write_runtime_state(
            changes.get("smoke_test"),
            bot_status=changes.get("bot_status"),
            execution_thread=changes.get("execution_thread"),
            last_execution_result=changes.get("last_execution_result"),
            last_error=changes.get("last_error"),
        )
        if self.status_callback is not None:
            self.status_callback(**changes)

    @staticmethod
    def doctor(settings: Optional[BotSettings] = None) -> Dict[str, Any]:
        s = settings or get_settings()
        return {
            "binance_api_key_configured": bool(s.BINANCE_API_KEY),
            "binance_api_secret_configured": bool(s.BINANCE_API_SECRET),
            "binance_testnet": s.BINANCE_TESTNET,
            "env": "TESTNET" if s.ENV.strip().lower() == "testnet" else "OTHER",
            "order_submission_enabled": s.ORDER_SUBMISSION_ENABLED,
            "account_read_only": s.ACCOUNT_READ_ONLY,
            "shadow_mode": s.SHADOW_MODE,
            "telegram_enabled": s.TELEGRAM_ENABLED,
            "telegram_bot_token_configured": bool(s.TELEGRAM_BOT_TOKEN),
            "telegram_chat_id_configured": bool(s.TELEGRAM_CHAT_ID),
        }

    def authenticate(self) -> Dict[str, Any]:
        self.executor._assert_execution_boundary()
        server_time = self.client.get_server_time()
        account = self.client.get_account_summary()
        self.executor._notify("BINANCE_CONNECTED", {"message": "Signed Binance Futures TESTNET account connected"}, "BINANCE_CONNECTED")
        return {"server_time": server_time, "account": account}

    def run_smoke_test(self) -> Dict[str, Any]:
        if not self.settings.RUN_EXECUTION_SMOKE_TEST:
            return {"status": "NOT_RUN", "test_buy": "NOT_RUN", "test_close": "NOT_RUN", "final_position": "UNKNOWN"}
        if self._smoke_attempted:
            if self._smoke_result is not None:
                return dict(self._smoke_result)
            raise ExecutionError("SMOKE_TEST_ALREADY_FAILED")
        self._smoke_attempted = True
        self._status(smoke_test="RUNNING", bot_status="STARTING", execution_thread="STARTING", last_execution_result="SMOKE_TEST_RUNNING", last_error="")
        open_order: Optional[Dict[str, Any]] = None
        try:
            self.executor._assert_execution_boundary()
            self.authenticate()
            before = self.client.get_position("BTCUSDT")
            if float(before.get("position_amt") or 0) != 0:
                raise ExecutionError("POSITION_ALREADY_OPEN")
            cleanup = self.executor.cleanup_flat_reduce_only_orders()
            if cleanup["remaining"]:
                raise ExecutionError("UNEXPECTED_OPEN_ORDERS")
            mark_price = float(self.dashboard.binance.get_mark_price("BTCUSDT"))
            quantity = self.client.normalize_quantity("BTCUSDT", self.settings.TEST_ORDER_NOTIONAL_USDT / mark_price, market=True, price=mark_price)
            actual_notional = quantity * mark_price
            if actual_notional > self.settings.TEST_ORDER_MAX_NOTIONAL_USDT:
                raise ExecutionError("SMOKE_NOTIONAL_SAFETY_CAP")
            open_order = self.client.place_market_order("BTCUSDT", "BUY", quantity, reduce_only=False, client_order_id=f"btc-smoke-{int(time.time())}")
            position = self.client.get_position("BTCUSDT")
            opened = float(open_order.get("executed_quantity") or 0) > 0 and float(position.get("position_amt") or 0) > 0
            self.executor._known_position = position
            self.executor._record_order("SMOKE_TEST", "SMOKE_OPEN", open_order, before, position)
            if not opened:
                raise ExecutionError("TEST_POSITION_NOT_DETECTED")
            self.executor._notify("ORDER_OPENED", {"side": "LONG", "entry": open_order.get("average_fill_price"), "size": abs(float(position["position_amt"])), "stop": "SMOKE TEST", "tp1": "AUTO CLOSE", "tp2": "AUTO CLOSE"}, "ORDER_OPENED:SMOKE_TEST")
            self.sleep_fn(3)
            close_order = self.client.close_position_market("BTCUSDT")
            final_position = self.client.get_position("BTCUSDT")
            flat = float(final_position.get("position_amt") or 0) == 0
            self.executor._known_position = final_position
            if close_order:
                self.executor._record_order("SMOKE_TEST", "SMOKE_CLOSE", close_order, position, final_position)
            if not flat:
                raise ExecutionError("SMOKE_CLOSE_RECONCILIATION_FAILED")
            self.executor._notify("ORDER_CLOSED", {"message": "Controlled TESTNET smoke position closed; final position FLAT"}, "ORDER_CLOSED:SMOKE_TEST")
            result = {"status": "PASS", "test_buy": "PASS", "position_detected": True, "test_close": "PASS", "final_position": "FLAT", "open_order": open_order, "close_order": close_order}
            self._smoke_result = result
            self.executor._notify("SMOKE_TEST_PASS", {"message": "Controlled BUY, position verification and reduce-only close passed; final position FLAT"}, "SMOKE_TEST_PASS")
            self._status(smoke_test="PASS", bot_status="STARTING", execution_thread="STARTING", last_execution_result="SMOKE_TEST_PASS", last_error="")
            return dict(result)
        except Exception as exc:
            final_position: Dict[str, Any] = {"symbol": "BTCUSDT", "position_amt": None, "side": "UNKNOWN"}
            try:
                final_position = self.client.get_position("BTCUSDT")
                if float(final_position.get("position_amt") or 0) != 0:
                    self.client.close_position_market("BTCUSDT")
                    final_position = self.client.get_position("BTCUSDT")
            except Exception as cleanup_exc:
                self.executor._known_position = final_position
                self.executor.execution_journal.record(
                    decision_id="SMOKE_TEST",
                    action="SMOKE_FLATTEN_FAILURE",
                    status="KILL_SWITCH",
                    reason=type(cleanup_exc).__name__,
                    position_after=final_position,
                )
                self.state.activate_emergency_latch("SMOKE_FLATTEN_FAILURE")
            self.executor._known_position = final_position
            reason = getattr(exc, "category", type(exc).__name__)
            self.executor._notify("SMOKE_TEST_FAIL", {"message": f"Smoke test failed: {reason}"}, f"SMOKE_TEST_FAIL:{reason}")
            self._status(smoke_test="FAIL", bot_status="STOPPED", execution_thread="STOPPED", last_execution_result="SMOKE_TEST_FAIL", last_error=reason)
            raise ExecutionError(reason) from None

    def run_cycle(self) -> Dict[str, Any]:
        managed = self.executor.manage_existing_position(self.state)
        if managed["status"] != "FLAT":
            # Reconciliation and real-time exchange protection always run
            # first.  Thesis management is deliberately clocked by the most
            # recent CLOSED 5M candle and the executor deduplicates that
            # timestamp, so dashboard/poll cadence cannot repeat an action.
            if managed["status"] == "POSITION_MANAGEMENT":
                # Lightweight test/recovery dashboards without the strategy
                # pipeline can reconcile protection, but cannot safely form a
                # thesis-management decision.
                if not hasattr(self.dashboard, "pipeline"):
                    return managed
                snapshot = self.dashboard.snapshot(force=True)
                if not isinstance(snapshot, dict):
                    return managed
                return self.executor.manage_adaptive_position(
                    snapshot,
                    self.state,
                    managed["position"],
                )
            return managed
        snapshot = self.dashboard.snapshot(force=True)
        result = self.executor.process_snapshot(snapshot, self.state)
        return result or {"status": "NO_ACTION"}

    def run_loop(self, max_cycles: Optional[int] = None) -> None:
        self.executor._assert_execution_boundary()

        # Restart safety comes before a startup smoke. If Render restarts while
        # a real TESTNET position is already open, the bot must recover and
        # manage that position instead of treating it as a smoke-test failure
        # (which previously attempted to flatten it in the smoke exception
        # handler). A smoke is only meaningful on a verified-flat account.
        self.authenticate()
        recovered = self.executor.recover_from_exchange()
        recovered_position = recovered.get("position") or {}
        has_active_position = float(recovered_position.get("position_amt") or 0) != 0

        if self.settings.RUN_EXECUTION_SMOKE_TEST and has_active_position:
            self._status(
                smoke_test="SKIPPED_ACTIVE_POSITION",
                bot_status="STARTING",
                execution_thread="STARTING",
                last_execution_result="RECOVERED_ACTIVE_POSITION",
                last_error="",
            )
        elif self.settings.RUN_EXECUTION_SMOKE_TEST:
            smoke = self.run_smoke_test()
            if smoke.get("status") != "PASS" or smoke.get("final_position") != "FLAT":
                raise ExecutionError("SMOKE_TEST_FAILED")
            # Smoke ends FLAT. Reconcile once more so journal/exchange state is
            # aligned before normal strategy cycles begin.
            self.executor.recover_from_exchange()

        self.executor._notify("SYSTEM_STARTED", {"message": "Automatic TESTNET trading loop started"}, "SYSTEM_STARTED")
        self._status(bot_status="RUNNING", execution_thread="RUNNING", last_execution_result="LOOP_STARTED", last_error="")
        cycles = 0
        while max_cycles is None or cycles < max_cycles:
            try:
                result = self.run_cycle()
                self._status(bot_status="RUNNING", execution_thread="RUNNING", last_execution_result=str(result.get("status") or "NO_ACTION"), last_error="")
            except ExecutionError as exc:
                self.executor._notify("ORDER_REJECTED" if exc.category in {"ORDER_REJECTED", "PROTECTION_FAILURE"} else "ERROR", {"message": f"TESTNET execution cycle failed: {exc.category}"}, f"EXECUTION_ERROR:{exc.category}")
                self._status(bot_status="DEGRADED", execution_thread="RUNNING", last_execution_result="CYCLE_FAILED", last_error=exc.category)
                if exc.category in self.FATAL_EXECUTION_ERRORS or max_cycles is not None:
                    raise
            except Exception as exc:
                self.state.activate_emergency_latch("UNEXPECTED_EXECUTION_FAILURE")
                self.executor._notify("KILL_SWITCH", {"message": "Unexpected TESTNET execution failure; new entries blocked"}, "KILL_SWITCH:UNEXPECTED_EXECUTION_FAILURE")
                self._status(bot_status="DEGRADED", execution_thread="RUNNING", last_execution_result="CYCLE_FAILED", last_error=type(exc).__name__)
                if max_cycles is not None:
                    raise ExecutionError("UNEXPECTED_EXECUTION_FAILURE") from None
            cycles += 1
            if max_cycles is None or cycles < max_cycles:
                self.sleep_fn(self.settings.EXECUTION_POLL_SECONDS)
        if max_cycles is not None:
            self._status(bot_status="STOPPED", execution_thread="STOPPED", last_error="")
