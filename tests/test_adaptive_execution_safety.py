from types import SimpleNamespace

import pytest

from core.state import BotState
from data.binance_execution_client import ExecutionError
from execution.safer_testnet_executor import SaferTestnetExecutor


class MemoryJournal:
    def __init__(self, state=None):
        self.state = dict(state or {})
        self.records = []

    def read_state(self):
        return dict(self.state)

    def write_state(self, state):
        self.state = dict(state)

    def record(self, **event):
        self.records.append(event)
        return event


def settings(*, enabled=False):
    return SimpleNamespace(
        testnet_execution_enabled=enabled,
        BINANCE_TESTNET=True,
        ENV="testnet",
        ORDER_SUBMISSION_ENABLED=enabled,
        ACCOUNT_READ_ONLY=not enabled,
        SHADOW_MODE=not enabled,
        TP_SPLIT_CONSERVATIVE=.70,
        TP_SPLIT_BALANCED=.50,
        TP_SPLIT_TREND_RUNNER=.35,
    )


def baseline(direction="LONG", entry=100.02, stop=90.0, size=.999):
    return {
        "exchange_baseline_verified": True,
        "actual_entry_price": entry,
        "actual_initial_position_size": size,
        "actual_initial_stop": stop,
        "entry_decision_id": "DEC-NORMALIZED",
        "entry_opened_at": 123456,
        "direction": direction,
        "management_profile": "BALANCED",
        "planned_entry": 100.0,
        "planned_size": 1.0,
        "planned_stop": 90.03 if direction == "LONG" else 109.97,
    }


class ExchangeFake:
    testnet = True
    configured = True

    def __init__(self, *, amount=.5, cancel_stop_fails=False, cancel_fail_ids=None):
        self.position = {"symbol": "BTCUSDT", "position_amt": amount, "side": "LONG" if amount > 0 else "SHORT", "entry_price": 100.02, "mark_price": 105, "unrealized_pnl": 2}
        close_side = "SELL" if amount > 0 else "BUY"
        self.orders = [
            {"algoId": 1, "orderType": "STOP_MARKET", "side": close_side, "triggerPrice": "90", "quantity": "1", "reduceOnly": True},
            {"algoId": 3, "orderType": "TAKE_PROFIT_MARKET", "side": close_side, "triggerPrice": "120", "quantity": ".5", "reduceOnly": True},
        ]
        self.regular = []
        self.calls = []
        self.next_id = 4
        self.cancel_stop_fails = cancel_stop_fails
        self.cancel_fail_ids = set(cancel_fail_ids or [])

    def get_position(self, _symbol="BTCUSDT"):
        self.calls.append("get_position")
        return dict(self.position)

    def get_open_orders(self, _symbol=None):
        return [dict(row) for row in self.regular]

    def get_open_algo_orders(self, _symbol=None):
        return [dict(row) for row in self.orders]

    def place_protective_order(self, _symbol, side, order_type, quantity, stop_price):
        self.calls.append(("place", order_type, quantity, stop_price))
        algo_id = self.next_id
        self.next_id += 1
        raw = {"algoId": algo_id, "orderType": order_type, "side": side, "triggerPrice": str(stop_price), "quantity": str(quantity), "reduceOnly": True}
        self.orders.append(raw)
        return {"binance_order_id": algo_id, "type": order_type, "side": side, "trigger_price": stop_price, "requested_quantity": quantity, "reduce_only": True}

    def cancel_algo_order(self, *, algo_id):
        self.calls.append(("cancel_algo", algo_id))
        if (self.cancel_stop_fails and algo_id == 1) or algo_id in self.cancel_fail_ids:
            raise RuntimeError("cancel unavailable")
        self.orders = [row for row in self.orders if row["algoId"] != algo_id]

    def cancel_order(self, _symbol, order_id):
        self.calls.append(("cancel_regular", order_id))
        self.regular = [row for row in self.regular if row["orderId"] != order_id]

    def cancel_all_algo_open_orders(self, _symbol):
        self.orders = []

    def close_position_market(self, _symbol="BTCUSDT"):
        self.calls.append("close_position_market")
        self.position = {"symbol": "BTCUSDT", "position_amt": 0.0, "side": "FLAT"}
        return {"status": "FILLED", "reduce_only": True}


@pytest.mark.parametrize(
    "amount,actual_stop,planned_stop",
    [(0.999, 90.0, 90.03), (-0.999, 109.9, 109.97)],
)
def test_verified_baseline_uses_exchange_normalized_values(amount, actual_stop, planned_stop):
    client = ExchangeFake(amount=amount)
    executor = SaferTestnetExecutor(client, settings=settings(), execution_journal=MemoryJournal())
    executor._entry_context = {"planned_entry": 100, "planned_size": 1, "planned_stop": planned_stop}
    side = "SELL" if amount > 0 else "BUY"
    executor._capture_verified_exchange_baseline(
        {"decision_id": "DEC-NORMALIZED"},
        {
            "entry": {"average_fill_price": 100.02, "transaction_time": 123456},
            "position": {"position_amt": amount, "entry_price": 100.02},
            "protective_orders": [{"role": "STOP", "side": side, "trigger_price": actual_stop, "requested_quantity": abs(amount)}],
        },
    )
    assert executor._entry_context["actual_initial_stop"] == actual_stop
    assert executor._entry_context["actual_initial_position_size"] == abs(amount)
    assert executor._entry_context["planned_stop"] == planned_stop
    assert executor._entry_context["planned_size"] == 1


@pytest.mark.parametrize(
    "amount,actual_stop,planned_stop,mark,regime,trend",
    [
        (.999, 90.0, 90.03, 97, "BULL", "UP"),
        (-.999, 109.9, 109.97, 103, "BEAR", "DOWN"),
    ],
)
def test_management_uses_normalized_baseline_without_false_widen_or_size_growth(amount, actual_stop, planned_stop, mark, regime, trend):
    client = ExchangeFake(amount=amount)
    close_side = "SELL" if amount > 0 else "BUY"
    client.orders = [
        {"algoId": 1, "orderType": "STOP_MARKET", "side": close_side, "triggerPrice": str(actual_stop), "quantity": str(abs(amount)), "reduceOnly": True},
        {"algoId": 3, "orderType": "TAKE_PROFIT_MARKET", "side": close_side, "triggerPrice": "120" if amount > 0 else "80", "quantity": str(abs(amount)), "reduceOnly": True},
    ]
    executor = SaferTestnetExecutor(client, settings=settings(), execution_journal=MemoryJournal())
    executor._entry_context = baseline(direction="LONG" if amount > 0 else "SHORT", stop=actual_stop, size=abs(amount))
    executor._entry_context["planned_stop"] = planned_stop
    snapshot = {
        "candles": {"5m": [{"time": 10}]},
        "market": {"mark_price": mark},
        "sources": {"binance": {"status": "HEALTHY", "market_data_trading_safe": True}},
        "decision": {"regime": regime, "volatility": "NORMAL"},
        "chart_intelligence": {"timeframes": {"5m": {"status": "AVAILABLE", "closed_candles": 30, "structure": "MIXED", "trend": trend, "volume_state": "NORMAL"}}},
        "zones": [],
    }
    position = dict(client.position, mark_price=mark)
    result = executor.manage_adaptive_position(snapshot, BotState(), position)
    reasons = result["position_intelligence"]["reason_codes"]
    assert "STOP_WIDENING_DETECTED" not in reasons
    assert "POSITION_SIZE_INCREASE_DETECTED" not in reasons


def test_partial_tp_reconciliation_resizes_stop_and_preserves_initial_baseline():
    journal = MemoryJournal()
    client = ExchangeFake(amount=.5)
    executor = SaferTestnetExecutor(client, settings=settings(), execution_journal=journal)
    executor._entry_context = baseline(size=1)
    executor._known_position = {"position_amt": 1, "side": "LONG", "entry_price": 100.02}
    executor._protective_orders = [
        {"binance_order_id": 1, "role": "STOP"},
        {"binance_order_id": 2, "role": "TP1"},
        {"binance_order_id": 3, "role": "TP2"},
    ]

    executor.reconcile_position()

    stops = [row for row in client.orders if row["orderType"] == "STOP_MARKET"]
    assert len(stops) == 1 and float(stops[0]["quantity"]) == .5
    assert any(row["orderType"] == "TAKE_PROFIT_MARKET" and float(row["quantity"]) == .5 for row in client.orders)
    assert executor._entry_context["actual_initial_position_size"] == 1
    assert executor.target_replan_count == 0
    assert [row["action"] for row in journal.records].count("TP1_FILLED") == 1
    assert [row["action"] for row in journal.records].count("PARTIAL_POSITION_RECONCILED") == 1

    executor.reconcile_position()
    assert [row["action"] for row in journal.records].count("PARTIAL_POSITION_RECONCILED") == 1


def test_stop_cancellation_failure_latches_adaptive_changes_with_redundant_stops():
    journal = MemoryJournal()
    client = ExchangeFake(amount=.5, cancel_stop_fails=True)
    executor = SaferTestnetExecutor(client, settings=settings(), execution_journal=journal)
    executor._entry_context = baseline(size=.5)
    with pytest.raises(ExecutionError, match="PROTECTION_RECONCILIATION_REQUIRED"):
        executor._replace_stop_safely(client.position, 95)
    assert len([row for row in client.orders if row["orderType"] == "STOP_MARKET"]) == 2
    assert executor.protection_reconciliation_required is True
    assert any(row["action"] == "PROTECTION_RECONCILIATION_REQUIRED" for row in journal.records)


@pytest.mark.parametrize("amount,expected_side,new_stop", [(.5, "SELL", 95), (-.5, "BUY", 105)])
def test_stop_replacement_final_set_has_close_side_reduce_only_and_exact_quantity(amount, expected_side, new_stop):
    client = ExchangeFake(amount=amount)
    client.orders[0].update({"side": expected_side, "quantity": str(abs(amount))})
    executor = SaferTestnetExecutor(client, settings=settings(), execution_journal=MemoryJournal())
    executor._entry_context = baseline(direction="LONG" if amount > 0 else "SHORT", size=abs(amount))
    executor._replace_stop_safely(client.position, new_stop)
    stops = [row for row in client.orders if row["orderType"] == "STOP_MARKET"]
    assert len(stops) == 1
    assert stops[0]["side"] == expected_side
    assert stops[0]["reduceOnly"] is True
    assert float(stops[0]["quantity"]) == abs(amount)


def test_early_exit_confirms_flat_and_cleans_all_stale_protection_same_cycle():
    journal = MemoryJournal()
    client = ExchangeFake(amount=.5)
    client.regular = [{"orderId": 7, "reduceOnly": True}]
    executor = SaferTestnetExecutor(client, settings=settings(), execution_journal=journal)
    executor._entry_context = baseline(size=.5)
    result = executor._early_exit_and_reconcile(dict(client.position), BotState(), {"reason": "TEST"})
    assert result["position"]["side"] == "FLAT"
    assert client.orders == [] and client.regular == []
    assert journal.records[-1]["action"] == "EARLY_EXIT"
    assert journal.records[-1]["status"] == "CONFIRMED"


def test_early_exit_does_not_confirm_when_position_remains_open_but_stop_survives():
    journal = MemoryJournal()
    client = ExchangeFake(amount=.5)
    client.close_position_market = lambda _symbol="BTCUSDT": {"status": "UNKNOWN"}
    executor = SaferTestnetExecutor(client, settings=settings(), execution_journal=journal)
    executor._entry_context = baseline(size=.5)
    with pytest.raises(ExecutionError, match="EARLY_EXIT_RECONCILIATION_FAILED"):
        executor._early_exit_and_reconcile(dict(client.position), BotState(), {"reason": "TEST"})
    assert any(row["action"] == "EARLY_EXIT_RECONCILIATION_FAILURE" for row in journal.records)
    assert not any(row["action"] == "EARLY_EXIT" for row in journal.records)
    assert any(row["orderType"] == "STOP_MARKET" for row in client.orders)


def test_restart_with_active_position_and_empty_context_is_fail_closed():
    journal = MemoryJournal()
    client = ExchangeFake(amount=.5)
    executor = SaferTestnetExecutor(client, settings=settings(enabled=True), execution_journal=journal)
    recovered = executor.recover_from_exchange()
    assert recovered["status"] == "RECOVERED_POSITION_CONTEXT_UNAVAILABLE"
    stop = next(row for row in client.orders if row["orderType"] == "STOP_MARKET")
    assert float(stop["quantity"]) == .5
    assert float(stop["triggerPrice"]) == 90
    calls_after_recovery = list(client.calls)
    result = executor.manage_adaptive_position({"candles": {"5m": [{"time": 1}]}}, BotState(), client.position)
    assert result["status"] == "RECOVERED_POSITION_CONTEXT_UNAVAILABLE"
    assert client.calls == calls_after_recovery
    assert not any(call == "close_position_market" for call in client.calls)
    assert [row["action"] for row in journal.records].count("RECOVERED_POSITION_CONTEXT_UNAVAILABLE") == 1


def test_restart_repairs_offline_partial_fill_stop_with_surviving_context_and_is_idempotent():
    context = baseline(size=1)
    journal = MemoryJournal({"entry_context": context})
    client = ExchangeFake(amount=.5)
    executor = SaferTestnetExecutor(client, settings=settings(enabled=True), execution_journal=journal)

    first = executor.recover_from_exchange()
    stop = next(row for row in client.orders if row["orderType"] == "STOP_MARKET")
    assert first["protection_reconciliation"]["stop_resized"] is True
    assert float(stop["quantity"]) == .5
    assert float(stop["triggerPrice"]) == 90
    assert executor._entry_context["actual_initial_position_size"] == 1
    place_count = len([call for call in client.calls if isinstance(call, tuple) and call[0] == "place"])

    second = executor.recover_from_exchange()
    assert second["protection_reconciliation"]["stop_resized"] is False
    assert len([call for call in client.calls if isinstance(call, tuple) and call[0] == "place"]) == place_count
    assert [row["action"] for row in journal.records].count("RESTART_PROTECTION_QUANTITY_RECONCILED") == 1


def test_restart_correct_stop_quantity_is_untouched():
    journal = MemoryJournal({"entry_context": baseline(size=.5)})
    client = ExchangeFake(amount=.5)
    client.orders[0]["quantity"] = ".5"
    executor = SaferTestnetExecutor(client, settings=settings(enabled=True), execution_journal=journal)
    result = executor.recover_from_exchange()
    assert result["protection_reconciliation"]["stop_resized"] is False
    assert not any(isinstance(call, tuple) and call[0] == "place" for call in client.calls)
    assert next(row for row in executor._protective_orders if row["type"] == "TAKE_PROFIT_MARKET")["role"] == "UNKNOWN_TARGET"


def test_target_cancel_failure_rolls_back_new_target_without_quantity_accumulation():
    journal = MemoryJournal()
    client = ExchangeFake(amount=.5, cancel_fail_ids={3})
    client.orders[0]["quantity"] = ".5"
    executor = SaferTestnetExecutor(client, settings=settings(), execution_journal=journal)
    executor._entry_context = baseline(size=.5)
    with pytest.raises(ExecutionError, match="PROTECTION_REPLACEMENT_FAILED"):
        executor._replace_tp2_safely(client.position, 125)
    targets = [row for row in client.orders if row["orderType"] == "TAKE_PROFIT_MARKET"]
    assert [row["algoId"] for row in targets] == [3]
    assert sum(float(row["quantity"]) for row in targets) == .5
    assert executor.protection_reconciliation_required is False
    assert any(row["action"] == "TARGET_REPLACEMENT_ROLLED_BACK" for row in journal.records)


def test_target_cancel_and_rollback_failure_requires_reconciliation_lock():
    journal = MemoryJournal()
    client = ExchangeFake(amount=.5, cancel_fail_ids={3, 4})
    client.orders[0]["quantity"] = ".5"
    executor = SaferTestnetExecutor(client, settings=settings(), execution_journal=journal)
    executor._entry_context = baseline(size=.5)
    with pytest.raises(ExecutionError, match="PROTECTION_RECONCILIATION_REQUIRED"):
        executor._replace_tp2_safely(client.position, 125)
    assert executor.protection_reconciliation_required is True
    assert any(row["action"] == "PROTECTION_RECONCILIATION_REQUIRED" for row in journal.records)
    assert any(row["orderType"] == "STOP_MARKET" for row in client.orders)
    calls = list(client.calls)
    blocked = executor.manage_adaptive_position({"candles": {"5m": [{"time": 5}]}}, BotState(), client.position)
    assert blocked["status"] == "PROTECTION_RECONCILIATION_REQUIRED"
    assert client.calls == calls
    client.cancel_fail_ids.clear()
    reconciled = executor._reconcile_active_protection(client.position)
    assert reconciled["status"] == "PROTECTION_RECONCILED"
    assert executor.protection_reconciliation_required is False
    assert [row["algoId"] for row in client.orders if row["orderType"] == "TAKE_PROFIT_MARKET"] == [3]


def test_restart_excess_target_quantity_fails_closed_and_keeps_stop():
    journal = MemoryJournal()
    client = ExchangeFake(amount=.5)
    client.orders[0]["quantity"] = ".5"
    client.orders[1]["quantity"] = "1"
    executor = SaferTestnetExecutor(client, settings=settings(enabled=True), execution_journal=journal)
    result = executor.recover_from_exchange()
    assert result["status"] == "RECOVERED_POSITION_CONTEXT_UNAVAILABLE"
    assert result["protection_reconciliation"]["status"] == "PROTECTION_RECONCILIATION_REQUIRED"
    assert executor.protection_reconciliation_required is True
    assert any(row["orderType"] == "STOP_MARKET" for row in client.orders)


def test_restart_restores_unambiguous_target_roles():
    client = ExchangeFake(amount=1)
    client.orders[0]["quantity"] = "1"
    client.orders[1]["quantity"] = ".5"
    client.orders.append({"algoId": 5, "orderType": "TAKE_PROFIT_MARKET", "side": "SELL", "triggerPrice": "110", "quantity": ".5", "reduceOnly": True})
    executor = SaferTestnetExecutor(client, settings=settings(enabled=True), execution_journal=MemoryJournal({"entry_context": baseline(size=1)}))
    executor.recover_from_exchange()
    roles = {row["binance_order_id"]: row["role"] for row in executor._protective_orders}
    assert roles[1] == "STOP"
    assert roles[5] == "TP1"
    assert roles[3] == "TP2"


def test_adaptive_backtest_disables_legacy_auto_be_but_static_baseline_keeps_it():
    from backtest.simulator import BacktestSimulator
    from config.settings import BotSettings

    configured = BotSettings(_env_file=None, EXIT_POLICY_AUTO_BREAKEVEN=True)
    static = BacktestSimulator(configured, management_mode=BacktestSimulator.STATIC_EXIT_BASELINE)
    adaptive = BacktestSimulator(configured, management_mode=BacktestSimulator.ADAPTIVE_MANAGEMENT_V1)
    assert static.pipeline.exit_engine.auto_breakeven is True
    assert adaptive.pipeline.exit_engine.auto_breakeven is False
    assert static.settings.EXIT_POLICY_AUTO_BREAKEVEN is True
    assert adaptive.settings.EXIT_POLICY_AUTO_BREAKEVEN is False
