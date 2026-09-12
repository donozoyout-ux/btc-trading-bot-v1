from copy import deepcopy
from types import SimpleNamespace

import pytest

from core.state import BotState
from engines.fast_profit_guard import FastProfitGuard
from execution.safer_testnet_executor import SaferTestnetExecutor


def guard():
    return FastProfitGuard()


@pytest.mark.parametrize(
    "direction,mark,expected_stop",
    [
        ("LONG", 106, 100),
        ("SHORT", 94, 100),
    ],
)
def test_fast_guard_moves_to_breakeven_at_point_six_r(direction, mark, expected_stop):
    initial_stop = 90 if direction == "LONG" else 110
    current_stop = initial_stop
    result = guard().evaluate(
        direction=direction, entry=100, initial_stop=initial_stop,
        current_stop=current_stop, mark=mark,
        initial_size=1, current_size=1, stop_min_gap=.1,
    )
    assert result.action == "TIGHTEN_STOP"
    assert result.new_stop == pytest.approx(expected_stop)
    assert result.current_r == pytest.approx(.6)


def test_fast_guard_arms_at_point_four_r_without_forcing_exit():
    result = guard().evaluate(
        direction="LONG", entry=100, initial_stop=90, current_stop=90,
        mark=104, initial_size=1, current_size=1, stop_min_gap=.1,
    )
    assert result.armed is True
    assert result.action == "NONE"
    assert result.state == "FAST_PROFIT_HOLD"


def test_fast_guard_takes_thirty_percent_at_one_r_and_locks_point_three_r():
    result = guard().evaluate(
        direction="LONG", entry=100, initial_stop=90, current_stop=100,
        mark=110, initial_size=1, current_size=1, stop_min_gap=.1,
    )
    assert result.action == "CLOSE_PARTIAL"
    assert result.partial_fraction == pytest.approx(.30)
    assert result.new_stop == pytest.approx(103)
    assert result.desired_lock_r == pytest.approx(.30)


def test_fast_guard_does_not_take_second_partial_after_any_position_reduction():
    result = guard().evaluate(
        direction="LONG", entry=100, initial_stop=90, current_stop=103,
        mark=111, initial_size=1, current_size=.7, stop_min_gap=.1,
    )
    assert result.action != "CLOSE_PARTIAL"


@pytest.mark.parametrize(
    "mark,previous_mfe,expected_lock",
    [(112.5, 0, .60), (115, 0, .90), (120, 0, 1.30)],
)
def test_fast_profit_ladder_locks_more_as_mfe_increases(mark, previous_mfe, expected_lock):
    result = guard().evaluate(
        direction="LONG", entry=100, initial_stop=90, current_stop=100,
        mark=mark, initial_size=1, current_size=.7,
        previous_mfe_r=previous_mfe, partial_taken=True, stop_min_gap=.1,
    )
    assert result.action == "TIGHTEN_STOP"
    assert result.desired_lock_r >= expected_lock - 1e-9


def test_fast_mfe_giveback_closes_remaining_profit_before_full_round_trip():
    result = guard().evaluate(
        direction="LONG", entry=100, initial_stop=90, current_stop=103,
        mark=108, initial_size=1, current_size=.7,
        previous_mfe_r=1.40, partial_taken=True, stop_min_gap=.1,
    )
    assert result.mfe_r == pytest.approx(1.40)
    assert result.current_r == pytest.approx(.80)
    assert result.giveback_r == pytest.approx(.60)
    assert result.action == "CLOSE_FULL"
    assert result.state == "FAST_PROFIT_EXIT"


def test_fast_guard_never_widens_an_already_better_stop():
    result = guard().evaluate(
        direction="LONG", entry=100, initial_stop=90, current_stop=107,
        mark=115, initial_size=1, current_size=.7,
        partial_taken=True, stop_min_gap=.1,
    )
    assert result.action == "NONE"
    assert result.new_stop is None
    assert result.protected_r == pytest.approx(.7)


class MemoryJournal:
    def __init__(self, state=None):
        self.state = dict(state or {})
        self.records = []

    def read_state(self):
        return deepcopy(self.state)

    def write_state(self, value):
        self.state = deepcopy(value)

    def record(self, **event):
        self.records.append(event)
        return event


class Notifier:
    def __init__(self):
        self.events = []

    def notify(self, event, payload, dedupe_key=None):
        self.events.append((event, deepcopy(payload), dedupe_key))
        return {"sent": True, "event": event}


class FastClient:
    testnet = True
    configured = True

    def __init__(self, *, mark=106):
        self.position = {
            "symbol": "BTCUSDT", "position_amt": .5, "side": "LONG",
            "entry_price": 100.0, "mark_price": float(mark), "unrealized_pnl": 3.0,
        }
        self.orders = [
            {"algoId": 1, "orderType": "STOP_MARKET", "side": "SELL", "triggerPrice": "90",
             "quantity": ".5", "reduceOnly": True},
            {"algoId": 2, "orderType": "TAKE_PROFIT_MARKET", "side": "SELL", "triggerPrice": "120",
             "quantity": ".5", "reduceOnly": True},
        ]
        self.next_id = 10
        self.reductions = []
        self.full_closes = 0

    def get_position(self, symbol="BTCUSDT"):
        return deepcopy(self.position)

    def get_positions(self):
        return [deepcopy(self.position)] if self.position["position_amt"] else []

    def get_open_orders(self, symbol=None):
        return []

    def get_open_algo_orders(self, symbol=None):
        return deepcopy(self.orders)

    def get_mark_price(self, symbol="BTCUSDT"):
        return float(self.position["mark_price"])

    def price_tick_size(self, symbol):
        return .1

    def normalize_price(self, symbol, price):
        return round(float(price), 1)

    def normalize_quantity(self, symbol, quantity, **kwargs):
        return round(float(quantity), 3)

    def place_protective_order(self, symbol, side, order_type, quantity, stop_price):
        row = {
            "algoId": self.next_id, "orderType": order_type, "side": side,
            "triggerPrice": str(stop_price), "quantity": str(quantity), "reduceOnly": True,
        }
        self.next_id += 1
        self.orders.append(row)
        return {
            "binance_order_id": row["algoId"], "type": order_type, "side": side,
            "trigger_price": stop_price, "requested_quantity": quantity, "reduce_only": True,
        }

    def cancel_algo_order(self, *, algo_id):
        self.orders = [row for row in self.orders if int(row["algoId"]) != int(algo_id)]
        return {"status": "CANCELED"}

    def reduce_position_market(self, symbol, quantity):
        quantity = float(quantity)
        self.reductions.append(quantity)
        self.position["position_amt"] = round(float(self.position["position_amt"]) - quantity, 6)
        return {
            "status": "FILLED", "average_fill_price": self.position["mark_price"],
            "executed_quantity": quantity,
        }

    def close_position_market(self, symbol):
        self.full_closes += 1
        quantity = abs(float(self.position["position_amt"]))
        self.position["position_amt"] = 0.0
        self.position["side"] = "FLAT"
        return {
            "status": "FILLED", "average_fill_price": self.position["mark_price"],
            "executed_quantity": quantity,
        }


def settings():
    return SimpleNamespace(
        testnet_execution_enabled=False,
        BINANCE_TESTNET=True,
        ENV="testnet",
        ORDER_SUBMISSION_ENABLED=False,
        ACCOUNT_READ_ONLY=True,
        SHADOW_MODE=True,
        PROFIT_PROTECTION_ENABLED=True,
        FAST_PROFIT_GUARD_ENABLED=True,
    )


def context():
    return {
        "exchange_baseline_verified": True,
        "actual_entry_price": 100.0,
        "actual_initial_position_size": .5,
        "actual_initial_stop": 90.0,
        "entry_decision_id": "D1",
        "entry_opened_at": 1,
        "tp2": 120.0,
        "management_profile": "BALANCED",
    }


def make_executor(mark):
    client = FastClient(mark=mark)
    journal = MemoryJournal()
    notifier = Notifier()
    executor = SaferTestnetExecutor(
        client, settings=settings(), execution_journal=journal, event_notifier=notifier
    )
    executor._entry_context = context()
    return executor, client, journal, notifier


def test_executor_fast_guard_tightens_stop_without_waiting_for_closed_5m():
    executor, client, journal, notifier = make_executor(106)
    result = executor.manage_fast_profit_guard(client.position, BotState())
    assert result["status"] == "FAST_PROFIT_STOP_TIGHTENED"
    stop = next(row for row in client.orders if row["orderType"] == "STOP_MARKET")
    assert float(stop["triggerPrice"]) == pytest.approx(100)
    assert client.reductions == []
    assert any(row["action"] == "FAST_PROFIT_STOP_TIGHTENED" for row in journal.records)
    assert [event for event, _, _ in notifier.events] == ["PROFIT_PROTECTION"]


def test_executor_one_r_partial_realizes_once_and_resizes_protection():
    executor, client, journal, notifier = make_executor(110)
    result = executor.manage_fast_profit_guard(client.position, BotState())
    assert result["status"] == "FAST_PROFIT_PARTIAL"
    assert client.reductions == [pytest.approx(.15)]
    assert float(client.position["position_amt"]) == pytest.approx(.35)
    stop = next(row for row in client.orders if row["orderType"] == "STOP_MARKET")
    targets = [row for row in client.orders if row["orderType"] == "TAKE_PROFIT_MARKET"]
    assert float(stop["quantity"]) == pytest.approx(.35)
    assert float(stop["triggerPrice"]) == pytest.approx(103)
    assert sum(float(row["quantity"]) for row in targets) <= .35 + 1e-9
    assert executor.fast_profit_partial_taken is True
    assert executor.profit_fade_partial_taken is True
    assert [event for event, _, _ in notifier.events] == ["PROFIT_PARTIAL_TAKEN"]

    client.position["mark_price"] = 111
    second = executor.manage_fast_profit_guard(client.position, BotState())
    assert second is None
    assert len(client.reductions) == 1


def test_executor_fast_giveback_closes_remaining_position_and_cleans_protection():
    executor, client, journal, notifier = make_executor(108)
    executor.fast_profit_mfe_r = 1.40
    executor.fast_profit_partial_taken = True
    result = executor.manage_fast_profit_guard(client.position, BotState())
    assert result["status"] == "FAST_PROFIT_EXIT"
    assert client.full_closes == 1
    assert client.position["position_amt"] == 0
    assert client.orders == []
    assert any(row["action"] == "FAST_PROFIT_EXIT" for row in journal.records)
    assert [event for event, _, _ in notifier.events] == ["PROFIT_FADE"]


def test_fast_profit_state_survives_restart():
    executor, client, journal, _ = make_executor(110)
    executor.fast_profit_mfe_r = 1.25
    executor.fast_profit_partial_taken = True
    executor.fast_profit_armed = True
    executor.fast_profit_last_action = "CLOSE_PARTIAL"
    executor._write_runtime_state()

    restarted = SaferTestnetExecutor(
        client, settings=settings(), execution_journal=journal, event_notifier=Notifier()
    )
    assert restarted.fast_profit_mfe_r == pytest.approx(1.25)
    assert restarted.fast_profit_partial_taken is True
    assert restarted.fast_profit_armed is True
    assert restarted.fast_profit_last_action == "CLOSE_PARTIAL"
