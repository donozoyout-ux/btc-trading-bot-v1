from types import SimpleNamespace

import pytest

from config.settings import BotSettings
from core.state import BotState
from data.binance_execution_client import BinanceFuturesExecutionClient, ExecutionError
from execution.testnet_executor import TestnetExecutor
from execution.testnet_runtime import TestnetExecutionRuntime
from journal.execution_journal import ExecutionJournal
from notifications.telegram_notifier import TelegramEventNotifier


def enabled_settings(tmp_path, **overrides):
    values = dict(
        ENV="testnet",
        BINANCE_TESTNET=True,
        BINANCE_API_KEY="key",
        BINANCE_API_SECRET="secret",
        ACCOUNT_READ_ONLY=False,
        ORDER_SUBMISSION_ENABLED=True,
        SHADOW_MODE=False,
        JOURNAL_DIR=str(tmp_path),
        TELEGRAM_ENABLED=False,
    )
    values.update(overrides)
    return BotSettings(_env_file=None, **values)


class FakeExecutionClient:
    testnet = True
    configured = True

    def __init__(self, position_amt=0.0, protection_failure=False):
        self.position = {"symbol": "BTCUSDT", "position_amt": position_amt, "side": "LONG" if position_amt > 0 else "SHORT" if position_amt < 0 else "FLAT", "entry_price": 80000.0, "mark_price": 80000.0, "unrealized_pnl": 0.0, "leverage": 1}
        self.orders = []
        self.regular_orders = []
        self.market_calls = []
        self.close_calls = 0
        self.cancelled_algo_orders = []
        self.protection_failure = protection_failure

    def get_server_time(self): return 123456789
    def get_account_summary(self): return {"wallet_balance": 1000.0, "available_balance": 1000.0, "margin_balance": 1000.0, "unrealized_pnl": 0.0, "positions": [], "open_orders": []}
    def get_position(self, symbol="BTCUSDT"): return dict(self.position)
    def get_positions(self, symbol=None):
        if float(self.position.get("position_amt") or 0) == 0:
            return []
        return [{"symbol": "BTCUSDT", "positionAmt": self.position["position_amt"]}]
    def get_open_orders(self, symbol=None): return list(self.regular_orders)
    def get_open_algo_orders(self, symbol=None): return list(self.orders)
    def get_algo_order(self, *, algo_id=None, client_algo_id=None):
        return next((dict(order) for order in self.orders if order.get("algoId") == algo_id), {})
    def cancel_all_algo_open_orders(self, symbol):
        self.orders = []
        return {"code": 200}
    def cancel_algo_order(self, *, algo_id=None, client_algo_id=None):
        self.cancelled_algo_orders.append(algo_id)
        self.orders = [order for order in self.orders if order.get("algoId") != algo_id]
        return {"algoId": algo_id}
    def cancel_order(self, symbol, order_id):
        self.regular_orders = [order for order in self.regular_orders if order.get("orderId") != order_id]
        return {"orderId": order_id}
    def normalize_quantity(self, symbol, quantity, **kwargs): return quantity
    def place_market_order(self, symbol, side, quantity, *, reduce_only=False, client_order_id=None):
        self.market_calls.append({"side": side, "quantity": quantity, "reduce_only": reduce_only})
        if reduce_only:
            self.position.update(position_amt=0.0, side="FLAT")
        else:
            self.position.update(position_amt=quantity if side == "BUY" else -quantity, side="LONG" if side == "BUY" else "SHORT")
        return {"client_order_id": client_order_id, "binance_order_id": len(self.market_calls), "symbol": symbol, "side": side, "type": "MARKET", "requested_quantity": quantity, "executed_quantity": quantity, "average_fill_price": 80000.0, "status": "FILLED", "reduce_only": reduce_only, "timestamp": 1}
    def place_protective_order(self, symbol, side, order_type, quantity, stop_price):
        if self.protection_failure:
            raise ExecutionError("ORDER_REJECTED")
        order_id = 100 + len(self.orders)
        self.orders.append({"algoId": order_id, "clientAlgoId": f"p-{order_id}", "orderType": order_type, "algoStatus": "NEW", "reduceOnly": True})
        return {"client_order_id": f"p-{order_id}", "binance_order_id": order_id, "symbol": symbol, "side": side, "type": order_type, "requested_quantity": quantity, "executed_quantity": 0.0, "average_fill_price": 0.0, "status": "NEW", "reduce_only": True, "timestamp": 1}
    def close_position_market(self, symbol="BTCUSDT"):
        self.close_calls += 1
        amount = abs(float(self.position["position_amt"]))
        if amount == 0: return None
        side = "SELL" if self.position["position_amt"] > 0 else "BUY"
        return self.place_market_order(symbol, side, amount, reduce_only=True)


def snapshot(ts=1, decision_id="D1", eligible=True, kill=False):
    return {
        "decision_id": decision_id,
        "final_decision": "LONG_ENTRY" if eligible else "NO_TRADE",
        "candles": {"5m": [{"time": ts}]},
        "decision": {"price": 80000.0, "risk_status": "ACCEPT_TRADE" if eligible else "REJECT_TRADE", "risk_assessment": {"position_size_btc": 0.001}, "trade_plan": {"stop_loss": 79000.0, "tp1": 81000.0, "tp2": 82000.0}},
        "strategy": {"eligible": eligible, "entry_trigger_state": "ENTRY_READY" if eligible else "NO_SETUP", "trade_plan": {"stop_loss": 79000.0, "tp1": 81000.0, "tp2": 82000.0}},
        "system_state": {"kill_switch": kill},
        "sources": {"binance": {"status": "HEALTHY"}},
        "ai_analyst": {"execution_authority": True},
    }


def make_executor(tmp_path, client=None, settings=None, notifier=None):
    settings = settings or enabled_settings(tmp_path)
    return TestnetExecutor(client or FakeExecutionClient(), settings=settings, execution_journal=ExecutionJournal(str(tmp_path)), event_notifier=notifier)


def test_mainnet_and_incomplete_flags_fail_closed(tmp_path):
    settings = enabled_settings(tmp_path, BINANCE_TESTNET=False)
    assert settings.ORDER_SUBMISSION_ENABLED is False
    assert settings.ACCOUNT_READ_ONLY is True
    assert settings.SHADOW_MODE is True
    with pytest.raises(ExecutionError, match="MAINNET_EXECUTION_BLOCKED"):
        BinanceFuturesExecutionClient("key", "secret", testnet=False)


def test_explicit_testnet_flags_enable_execution(tmp_path):
    settings = enabled_settings(tmp_path)
    assert settings.testnet_execution_enabled is True
    assert make_executor(tmp_path, settings=settings).orders_enabled is True


@pytest.mark.parametrize("overrides", [
    {"ACCOUNT_READ_ONLY": True},
    {"SHADOW_MODE": True},
    {"ORDER_SUBMISSION_ENABLED": False},
    {"ENV": "production"},
])
def test_incomplete_execution_flags_fail_closed(tmp_path, overrides):
    settings = enabled_settings(tmp_path, **overrides)
    assert settings.testnet_execution_enabled is False
    assert settings.ORDER_SUBMISSION_ENABLED is False


def test_missing_keys_block_execution(tmp_path):
    settings = enabled_settings(tmp_path, BINANCE_API_KEY=None, BINANCE_API_SECRET=None)
    client = FakeExecutionClient(); client.configured = False
    with pytest.raises(ExecutionError, match="ACCOUNT_UNAVAILABLE"):
        make_executor(tmp_path, client, settings)._assert_execution_boundary()


def test_exchange_filter_normalization():
    client = BinanceFuturesExecutionClient("key", "secret", testnet=True)
    client._exchange_info = {"symbols": [{"symbol": "BTCUSDT", "filters": [{"filterType": "MARKET_LOT_SIZE", "minQty": "0.001", "maxQty": "100", "stepSize": "0.001"}, {"filterType": "PRICE_FILTER", "tickSize": "0.10"}, {"filterType": "MIN_NOTIONAL", "notional": "5"}]}]}
    assert client.normalize_quantity("BTCUSDT", 0.00199, price=80000) == 0.001
    assert client.normalize_price("BTCUSDT", 80000.19) == 80000.1


def test_protective_orders_use_current_algo_api():
    client = BinanceFuturesExecutionClient("key", "secret", testnet=True)
    client._exchange_info = {"symbols": [{"symbol": "BTCUSDT", "filters": [{"filterType": "PRICE_FILTER", "tickSize": "0.10"}]}]}
    calls = []

    def fake_request(method, path, params=None):
        calls.append((method, path, dict(params or {})))
        if method == "POST":
            return {"algoId": 77}
        return {"algoId": 77, "clientAlgoId": "p-77", "symbol": "BTCUSDT", "side": "SELL", "orderType": "STOP_MARKET", "quantity": "0.001", "algoStatus": "NEW", "triggerPrice": "79000.0", "reduceOnly": True}

    client._request = fake_request
    result = client.place_protective_order("BTCUSDT", "SELL", "STOP_MARKET", 0.001, 79000.09)
    assert calls[0][0:2] == ("POST", "/fapi/v1/algoOrder")
    assert calls[0][2]["algoType"] == "CONDITIONAL"
    assert calls[0][2]["triggerPrice"] == "79000.0"
    assert "stopPrice" not in calls[0][2]
    assert calls[1][0:2] == ("GET", "/fapi/v1/algoOrder")
    assert result["binance_order_id"] == 77


def test_one_position_rule(tmp_path):
    client = FakeExecutionClient(position_amt=0.001)
    result = make_executor(tmp_path, client).process_snapshot(snapshot(), BotState())
    assert result["status"] == "POSITION_ALREADY_OPEN"
    assert client.market_calls == []


def test_duplicate_candle_and_signal_do_not_trade(tmp_path):
    executor = make_executor(tmp_path)
    assert executor.process_snapshot(snapshot(1, "A", eligible=False), BotState())["status"] == "NO_ELIGIBLE_SIGNAL"
    assert executor.process_snapshot(snapshot(1, "B"), BotState())["status"] == "DUPLICATE_CANDLE"
    assert executor.process_snapshot(snapshot(2, "A"), BotState())["status"] == "DUPLICATE_SIGNAL"
    assert executor.client.market_calls == []


def test_disabled_experimental_setup_cannot_reach_testnet_order(tmp_path):
    executor = make_executor(tmp_path)
    blocked = snapshot(3, "EXPERIMENTAL-B-SHORT", eligible=False)
    blocked["strategy"]["blocking_reasons"] = ["EXPERIMENTAL_SETUP_DISABLED"]
    result = executor.process_snapshot(blocked, BotState())
    assert result["status"] == "NO_ELIGIBLE_SIGNAL"
    assert executor.client.market_calls == []


def test_entry_reconciliation_and_reduce_only_protection(tmp_path):
    client = FakeExecutionClient()
    result = make_executor(tmp_path, client).process_snapshot(snapshot(), BotState())
    assert result["status"] == "OPENED"
    assert result["position"]["position_amt"] == 0.001
    assert len(result["protective_orders"]) == 3
    assert all(order["reduce_only"] for order in result["protective_orders"])


def test_protection_failure_flattens_position(tmp_path):
    client = FakeExecutionClient(protection_failure=True)
    with pytest.raises(ExecutionError, match="PROTECTION_FAILURE"):
        make_executor(tmp_path, client).process_snapshot(snapshot(), BotState())
    assert client.position["position_amt"] == 0
    assert client.close_calls == 1


def test_restart_recovery_uses_exchange(tmp_path):
    client = FakeExecutionClient(position_amt=-0.002)
    result = make_executor(tmp_path, client).recover_from_exchange()
    assert result["recovered"] is True
    assert result["position"]["side"] == "SHORT"


def test_position_manager_latches_kill_switch_when_unprotected(tmp_path):
    client = FakeExecutionClient(position_amt=0.001)
    state = BotState()
    result = make_executor(tmp_path, client).manage_existing_position(state)
    assert result["status"] == "UNPROTECTED_POSITION"
    assert state.kill_switch_activated is True
    assert client.market_calls == []


def test_position_manager_accepts_exchange_side_algo_protection(tmp_path):
    client = FakeExecutionClient(position_amt=0.001)
    client.orders = [
        {"algoId": 100, "orderType": "STOP_MARKET", "algoStatus": "NEW", "reduceOnly": True},
        {"algoId": 101, "orderType": "TAKE_PROFIT_MARKET", "algoStatus": "NEW", "reduceOnly": True},
    ]
    state = BotState()
    result = make_executor(tmp_path, client).manage_existing_position(state)
    assert result["status"] == "POSITION_MANAGEMENT"
    assert state.kill_switch_activated is False


def test_triggered_stop_is_detected_and_stale_algo_orders_cancelled(tmp_path):
    client = FakeExecutionClient(position_amt=0.0)
    client.orders = [
        {"algoId": 100, "orderType": "STOP_MARKET", "algoStatus": "TRIGGERED", "actualOrderId": 501, "actualPrice": "79000", "reduceOnly": True},
        {"algoId": 101, "orderType": "TAKE_PROFIT_MARKET", "algoStatus": "NEW", "reduceOnly": True},
    ]
    executor = make_executor(tmp_path, client)
    executor._known_position = {"symbol": "BTCUSDT", "position_amt": 0.001, "side": "LONG"}
    executor._protective_orders = [{"binance_order_id": 100, "type": "STOP_MARKET"}]
    result = executor.reconcile_position()
    assert result["side"] == "FLAT"
    assert client.orders == []
    events = (tmp_path / "execution_events.jsonl").read_text(encoding="utf-8")
    assert '"action":"STOP_LOSS"' in events


def test_reconciliation_failure_activates_kill_switch(tmp_path):
    client = FakeExecutionClient()
    client.get_position = lambda symbol="BTCUSDT": (_ for _ in ()).throw(ExecutionError("NETWORK_ERROR"))
    state = BotState()
    with pytest.raises(ExecutionError, match="NETWORK_ERROR"):
        make_executor(tmp_path, client).manage_existing_position(state)
    assert state.kill_switch_activated is True


def test_flat_account_cleans_orphan_reduce_only_orders(tmp_path):
    client = FakeExecutionClient(position_amt=0.0)
    client.orders = [
        {"algoId": 999, "orderType": "STOP_MARKET", "algoStatus": "NEW", "reduceOnly": True}
    ]
    state = BotState()
    result = make_executor(tmp_path, client).manage_existing_position(state)
    assert result["status"] == "FLAT"
    assert result["stale_orders_cancelled"] == 1
    assert state.kill_switch_activated is False
    assert client.cancelled_algo_orders == [999]
    assert client.market_calls == []


def test_flat_account_with_non_reduce_only_order_blocks_new_entries(tmp_path):
    client = FakeExecutionClient(position_amt=0.0)
    client.orders = [
        {"algoId": 999, "orderType": "STOP_MARKET", "algoStatus": "NEW", "reduceOnly": False}
    ]
    state = BotState()
    result = make_executor(tmp_path, client).manage_existing_position(state)
    assert result["status"] == "OPEN_ORDERS_PRESENT"
    assert state.kill_switch_activated is True
    assert client.market_calls == []


def test_kill_switch_and_ai_cannot_trigger_entry(tmp_path):
    executor = make_executor(tmp_path)
    assert executor.process_snapshot(snapshot(kill=True), BotState())["status"] == "NO_ELIGIBLE_SIGNAL"
    assert executor.client.market_calls == []
    assert make_executor(tmp_path).process_snapshot(snapshot(2, eligible=False), BotState())["status"] == "NO_ELIGIBLE_SIGNAL"


def test_telegram_execution_event_dedupes():
    class Client:
        configured = True
        def __init__(self): self.calls = 0
        def send_message(self, text): self.calls += 1; return {"sent": True}
    client = Client(); notifier = TelegramEventNotifier(client)
    payload = {"message": "connected"}
    assert notifier.notify("BINANCE_CONNECTED", payload, "same")["sent"] is True
    assert notifier.notify("BINANCE_CONNECTED", payload, "same")["deduplicated"] is True
    assert client.calls == 1


def test_smoke_test_opens_and_reduce_only_closes(tmp_path):
    settings = enabled_settings(tmp_path, RUN_EXECUTION_SMOKE_TEST=True)
    client = FakeExecutionClient()
    dashboard = SimpleNamespace(binance=SimpleNamespace(get_mark_price=lambda symbol: 80000.0), state=BotState())
    runtime = TestnetExecutionRuntime(settings, client=client, dashboard_runtime=dashboard, sleep_fn=lambda _: None)
    result = runtime.run_smoke_test()
    assert result["status"] == "PASS"
    assert result["final_position"] == "FLAT"
    assert client.market_calls[0]["reduce_only"] is False
    assert client.market_calls[-1]["reduce_only"] is True


def test_smoke_runs_before_auto_loop_and_pass_status_is_preserved(tmp_path):
    settings = enabled_settings(tmp_path, RUN_EXECUTION_SMOKE_TEST=True)
    client = FakeExecutionClient()
    dashboard = SimpleNamespace(
        binance=SimpleNamespace(get_mark_price=lambda symbol: 80000.0),
        state=BotState(),
        snapshot=lambda force=False: snapshot(3, "NO-ENTRY", eligible=False),
    )
    runtime = TestnetExecutionRuntime(settings, client=client, dashboard_runtime=dashboard, sleep_fn=lambda _: None)

    runtime.run_loop(max_cycles=1)

    assert len(client.market_calls) == 2
    assert runtime.executor.execution_journal.read_state()["smoke_test"] == "PASS"
    assert runtime.executor.execution_journal.read_state()["last_execution_result"] == "NO_ELIGIBLE_SIGNAL"
    assert runtime.executor.execution_journal.read_state()["execution_thread"] == "STOPPED"


def test_smoke_failure_prevents_auto_loop(tmp_path):
    class FailingSmokeClient(FakeExecutionClient):
        def place_market_order(self, symbol, side, quantity, *, reduce_only=False, client_order_id=None):
            raise ExecutionError("ORDER_REJECTED")

    settings = enabled_settings(tmp_path, RUN_EXECUTION_SMOKE_TEST=True)
    snapshot_calls = []
    dashboard = SimpleNamespace(
        binance=SimpleNamespace(get_mark_price=lambda symbol: 80000.0),
        state=BotState(),
        snapshot=lambda force=False: snapshot_calls.append(force),
    )
    runtime = TestnetExecutionRuntime(settings, client=FailingSmokeClient(), dashboard_runtime=dashboard, sleep_fn=lambda _: None)

    with pytest.raises(ExecutionError, match="ORDER_REJECTED"):
        runtime.run_loop(max_cycles=1)

    assert snapshot_calls == []
    state = runtime.executor.execution_journal.read_state()
    assert state["smoke_test"] == "FAIL"
    assert state["execution_thread"] == "STOPPED"


def test_smoke_blocks_when_a_non_reduce_only_order_exists(tmp_path):
    settings = enabled_settings(tmp_path, RUN_EXECUTION_SMOKE_TEST=True)
    client = FakeExecutionClient()
    client.orders = [{"algoId": 55, "orderType": "STOP_MARKET", "reduceOnly": False}]
    dashboard = SimpleNamespace(
        binance=SimpleNamespace(get_mark_price=lambda symbol: 80000.0),
        state=BotState(),
    )
    runtime = TestnetExecutionRuntime(settings, client=client, dashboard_runtime=dashboard, sleep_fn=lambda _: None)

    with pytest.raises(ExecutionError, match="UNEXPECTED_OPEN_ORDERS"):
        runtime.run_smoke_test()

    assert client.market_calls == []


def test_testnet_trade_telegram_has_explicit_real_money_boundary():
    class Client:
        configured = True
        def __init__(self): self.messages = []
        def send_message(self, text): self.messages.append(text); return {"sent": True}

    client = Client()
    TelegramEventNotifier(client).notify("SMOKE_TEST_PASS", {"message": "ok"}, "smoke")
    assert "MODE: BINANCE FUTURES TESTNET" in client.messages[0]
    assert "REAL MONEY: NO" in client.messages[0]
