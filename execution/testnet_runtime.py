"""End-to-end TESTNET smoke test and closed-5m execution loop."""

from __future__ import annotations

import time
from typing import Any, Dict, Optional

from config.settings import BotSettings, get_settings
from core.state import BotState
from data.binance_execution_client import BinanceFuturesExecutionClient, ExecutionError
from execution.testnet_executor import TestnetExecutor
from journal.execution_journal import ExecutionJournal
from notifications.telegram_client import TelegramClient
from notifications.telegram_notifier import TelegramEventNotifier


class TestnetExecutionRuntime:
    __test__ = False

    def __init__(self, settings: Optional[BotSettings] = None, *, client=None, dashboard_runtime=None, sleep_fn=time.sleep):
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
        self.executor = TestnetExecutor(self.client, settings=self.settings, execution_journal=self.journal, event_notifier=self.notifier)
        if dashboard_runtime is None:
            from dashboard_server import DashboardRuntime
            dashboard_runtime = DashboardRuntime(settings=self.settings)
        self.dashboard = dashboard_runtime
        self.state: BotState = self.dashboard.state
        self.sleep_fn = sleep_fn

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
        self.executor._assert_execution_boundary()
        self.authenticate()
        before = self.client.get_position("BTCUSDT")
        if float(before.get("position_amt") or 0) != 0:
            raise ExecutionError("POSITION_ALREADY_OPEN")
        mark_price = float(self.dashboard.binance.get_mark_price("BTCUSDT"))
        quantity = self.client.normalize_quantity("BTCUSDT", self.settings.TEST_ORDER_NOTIONAL_USDT / mark_price, market=True, price=mark_price)
        actual_notional = quantity * mark_price
        if actual_notional > self.settings.TEST_ORDER_MAX_NOTIONAL_USDT:
            raise ExecutionError("SMOKE_NOTIONAL_SAFETY_CAP")
        opened = False
        open_order: Optional[Dict[str, Any]] = None
        try:
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
            self.executor._write_runtime_state("PASS")
            return {"status": "PASS", "test_buy": "PASS", "position_detected": True, "test_close": "PASS", "final_position": "FLAT", "open_order": open_order, "close_order": close_order}
        except Exception as exc:
            final_position = self.client.get_position("BTCUSDT")
            if opened and float(final_position.get("position_amt") or 0) != 0:
                try:
                    self.client.close_position_market("BTCUSDT")
                    final_position = self.client.get_position("BTCUSDT")
                except Exception:
                    pass
            self.executor._known_position = final_position
            self.executor._write_runtime_state("FAIL")
            reason = getattr(exc, "category", type(exc).__name__)
            self.executor._notify("ERROR", {"message": f"Smoke test failed: {reason}"}, f"ERROR:SMOKE_TEST:{reason}")
            raise ExecutionError(reason) from None

    def run_cycle(self) -> Dict[str, Any]:
        managed = self.executor.manage_existing_position(self.state)
        if managed["status"] != "FLAT":
            return managed
        snapshot = self.dashboard.snapshot(force=True)
        result = self.executor.process_snapshot(snapshot, self.state)
        return result or {"status": "NO_ACTION"}

    def run_loop(self, max_cycles: Optional[int] = None) -> None:
        self.executor._assert_execution_boundary()
        self.authenticate()
        self.executor.recover_from_exchange()
        self.executor._notify("SYSTEM_STARTED", {"message": "Automatic TESTNET trading loop started"}, "SYSTEM_STARTED")
        cycles = 0
        while max_cycles is None or cycles < max_cycles:
            self.run_cycle()
            cycles += 1
            if max_cycles is None or cycles < max_cycles:
                self.sleep_fn(self.settings.EXECUTION_POLL_SECONDS)
