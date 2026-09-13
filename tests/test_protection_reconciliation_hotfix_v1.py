from copy import deepcopy
from types import SimpleNamespace

import pytest

from execution.safer_testnet_executor import SaferTestnetExecutor


class Journal:
    def __init__(self):
        self.records = []

    def record(self, **event):
        self.records.append(event)
        return event

    def read_state(self):
        return {}

    def write_state(self, value):
        pass


class Client:
    def __init__(self):
        self.next_id = 100
        self.position = {
            "symbol": "BTCUSDT",
            "position_amt": -0.0273,
            "side": "SHORT",
            "entry_price": 77141.4,
            "mark_price": 76750.0,
            "unrealized_pnl": 10.5,
        }
        self.orders = [
            {
                "algoId": 1,
                "orderType": "STOP_MARKET",
                "side": "BUY",
                "triggerPrice": "77030.9",
                "quantity": "0.0273",
                "reduceOnly": True,
            },
            {
                "algoId": 2,
                "orderType": "STOP_MARKET",
                "side": "BUY",
                "triggerPrice": "77030.9",
                "quantity": "0.0390",
                "reduceOnly": True,
            },
            {
                "algoId": 3,
                "orderType": "TAKE_PROFIT_MARKET",
                "side": "BUY",
                "triggerPrice": "76587.5",
                "quantity": "0.0390",
                "reduceOnly": True,
            },
        ]

    def get_open_algo_orders(self, symbol):
        return deepcopy(self.orders)

    def cancel_algo_order(self, *, algo_id):
        before = len(self.orders)
        self.orders = [row for row in self.orders if int(row.get("algoId")) != int(algo_id)]
        assert len(self.orders) == before - 1
        return {"status": "CANCELED"}

    def get_mark_price(self, symbol):
        return float(self.position["mark_price"])

    def price_tick_size(self, symbol):
        return 0.1

    def normalize_price(self, symbol, price):
        return round(float(price), 1)

    def place_protective_order(self, symbol, side, order_type, quantity, stop_price):
        row = {
            "algoId": self.next_id,
            "orderType": order_type,
            "side": side,
            "triggerPrice": str(stop_price),
            "quantity": str(quantity),
            "reduceOnly": True,
        }
        self.next_id += 1
        self.orders.append(row)
        return {
            "binance_order_id": row["algoId"],
            "type": order_type,
            "side": side,
            "trigger_price": stop_price,
            "requested_quantity": quantity,
            "reduce_only": True,
        }


def make_executor(client=None):
    executor = SaferTestnetExecutor.__new__(SaferTestnetExecutor)
    executor.client = client or Client()
    executor.execution_journal = Journal()
    executor._entry_context = {}
    executor._protective_orders = []
    executor.protection_reconciliation_required = True
    executor.protection_reconciliation_reason = "STOP_MISSING_OR_INVALID"
    executor.protection_reconciliation_expected_target_ids = []
    executor.active_alert_state = "UNPROTECTED"
    executor.last_alert_reason = "UNPROTECTED_TESTNET_POSITION"
    executor.last_alert_type = None
    executor.last_alert_sent_at = None
    executor.last_management_alert_key = None
    executor._write_runtime_state = lambda *args, **kwargs: None
    executor._transition_protection_alert = lambda state, reason: None
    executor._restore_protective_roles = lambda position, orders: None
    return executor


def test_live_like_duplicate_stop_keeps_exact_current_qty_stop_and_cancels_stale_one():
    executor = make_executor()
    position = deepcopy(executor.client.position)
    stops = [row for row in executor.client.get_open_algo_orders("BTCUSDT") if row["orderType"] == "STOP_MARKET"]

    assert executor._repair_duplicate_stops_safely(position, stops) is True

    remaining = [row for row in executor.client.orders if row["orderType"] == "STOP_MARKET"]
    assert len(remaining) == 1
    assert remaining[0]["algoId"] == 1
    assert float(remaining[0]["quantity"]) == pytest.approx(0.0273)
    assert executor.protection_reconciliation_required is False
    assert any(row["action"] == "DUPLICATE_STOP_REPAIRED" for row in executor.execution_journal.records)


def test_duplicate_stale_sized_stops_are_replaced_at_tightest_short_trigger():
    client = Client()
    client.orders = [
        {
            "algoId": 1, "orderType": "STOP_MARKET", "side": "BUY",
            "triggerPrice": "77030.9", "quantity": "0.039", "reduceOnly": True,
        },
        {
            "algoId": 2, "orderType": "STOP_MARKET", "side": "BUY",
            "triggerPrice": "77150.0", "quantity": "0.039", "reduceOnly": True,
        },
        {
            "algoId": 3, "orderType": "TAKE_PROFIT_MARKET", "side": "BUY",
            "triggerPrice": "76587.5", "quantity": "0.0273", "reduceOnly": True,
        },
    ]
    executor = make_executor(client)
    position = deepcopy(client.position)
    stops = [row for row in client.get_open_algo_orders("BTCUSDT") if row["orderType"] == "STOP_MARKET"]

    assert executor._repair_duplicate_stops_safely(position, stops) is True

    remaining = [row for row in client.orders if row["orderType"] == "STOP_MARKET"]
    assert len(remaining) == 1
    assert float(remaining[0]["triggerPrice"]) == pytest.approx(77030.9)
    assert float(remaining[0]["quantity"]) == pytest.approx(0.0273)


def test_wrong_side_duplicate_stop_set_is_never_auto_repaired():
    client = Client()
    client.orders[1]["side"] = "SELL"
    executor = make_executor(client)
    position = deepcopy(client.position)
    original = deepcopy(client.orders)
    stops = [row for row in client.get_open_algo_orders("BTCUSDT") if row["orderType"] == "STOP_MARKET"]

    assert executor._repair_duplicate_stops_safely(position, stops) is False
    assert client.orders == original


def test_single_oversized_target_is_resized_at_same_trigger():
    executor = make_executor()
    position = deepcopy(executor.client.position)

    # First consolidate the current live-like duplicate stop set.
    stops = [row for row in executor.client.get_open_algo_orders("BTCUSDT") if row["orderType"] == "STOP_MARKET"]
    assert executor._repair_duplicate_stops_safely(position, stops) is True

    target = next(row for row in executor.client.get_open_algo_orders("BTCUSDT") if row["orderType"] == "TAKE_PROFIT_MARKET")
    assert float(target["quantity"]) == pytest.approx(0.039)

    assert executor._resize_single_target_safely(position, target) is True

    targets = [row for row in executor.client.orders if row["orderType"] == "TAKE_PROFIT_MARKET"]
    assert len(targets) == 1
    assert float(targets[0]["triggerPrice"]) == pytest.approx(76587.5)
    assert float(targets[0]["quantity"]) == pytest.approx(0.0273)
    assert any(row["action"] == "OVERSIZED_TARGET_RESIZED" for row in executor.execution_journal.records)


def test_assess_and_repair_returns_position_management_for_live_stale_protection_shape():
    executor = make_executor()
    position = deepcopy(executor.client.position)

    result = executor._assess_and_repair_protection(position, SimpleNamespace())

    assert result["status"] == "POSITION_MANAGEMENT"
    stops = [row for row in executor.client.orders if row["orderType"] == "STOP_MARKET"]
    targets = [row for row in executor.client.orders if row["orderType"] == "TAKE_PROFIT_MARKET"]
    assert len(stops) == 1
    assert float(stops[0]["quantity"]) == pytest.approx(0.0273)
    assert len(targets) == 1
    assert float(targets[0]["quantity"]) == pytest.approx(0.0273)
