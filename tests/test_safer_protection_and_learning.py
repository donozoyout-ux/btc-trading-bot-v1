import json
from types import SimpleNamespace

from engines.mistake_learning_engine import MistakeLearningEngine
from execution.safer_testnet_executor import SaferTestnetExecutor


class JournalStub:
    def __init__(self):
        self.rows = []

    def read_state(self):
        return {}

    def record(self, **kwargs):
        self.rows.append(kwargs)
        return kwargs

    def write_state(self, _state):
        return None


class ProtectionClient:
    testnet = True
    configured = True

    def __init__(self):
        self.orders = []
        self.next_id = 1

    def normalize_quantity(self, _symbol, quantity, *, market=True, price=None):
        # Simulate a 0.001 lot-size floor with minimum 0.001.
        value = int(float(quantity) * 1000) / 1000
        return max(0.001, value)

    def place_protective_order(self, symbol, side, order_type, quantity, stop_price):
        order = {
            "binance_order_id": self.next_id,
            "symbol": symbol,
            "side": side,
            "type": order_type,
            "requested_quantity": quantity,
            "executed_quantity": 0.0,
            "average_fill_price": 0.0,
            "status": "NEW",
            "trigger_price": stop_price,
        }
        self.next_id += 1
        self.orders.append(order)
        return dict(order)

    def get_open_algo_orders(self, _symbol):
        return [{"algoId": order["binance_order_id"]} for order in self.orders]


def _executor(client=None):
    settings = SimpleNamespace()
    return SaferTestnetExecutor(
        client or ProtectionClient(),
        settings=settings,
        execution_journal=JournalStub(),
    )


def test_split_targets_never_exceed_position_and_stop_covers_full_position():
    client = ProtectionClient()
    executor = _executor(client)
    plan = {"stop_loss": 79000.0, "tp1": 81000.0, "tp2": 82000.0}

    orders = executor._place_protection("decision-1", "BUY", 0.010, plan)

    by_role = {order["role"]: order for order in orders}
    assert by_role["STOP"]["requested_quantity"] == 0.010
    assert by_role["TP1"]["requested_quantity"] == 0.005
    assert by_role["TP2"]["requested_quantity"] == 0.005
    assert by_role["TP1"]["requested_quantity"] + by_role["TP2"]["requested_quantity"] <= 0.010


def test_tiny_position_uses_single_final_target_instead_of_oversizing_two_tps():
    client = ProtectionClient()
    executor = _executor(client)
    plan = {"stop_loss": 79000.0, "tp1": 81000.0, "tp2": 82000.0}

    orders = executor._place_protection("decision-2", "BUY", 0.001, plan)

    roles = [order["role"] for order in orders]
    assert roles == ["STOP", "TP_FINAL"]
    assert orders[1]["requested_quantity"] == 0.001


def _write_events(path, rows):
    path.mkdir(parents=True, exist_ok=True)
    with (path / "execution_events.jsonl").open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row) + "\n")


def test_learning_engine_correlates_entry_context_and_flags_repeated_loss_pattern(tmp_path):
    context = {
        "setup_type": "BREAKOUT_RETEST",
        "direction": "LONG",
        "regime": "BULL",
        "market_basis": "SPOT_PROXY",
    }
    rows = []
    timestamp = 1
    outcomes = ["STOP_LOSS", "STOP_LOSS", "STOP_LOSS", "STOP_LOSS", "TAKE_PROFIT"]
    for index, outcome in enumerate(outcomes):
        rows.append(
            {
                "timestamp": timestamp,
                "decision_id": f"d-{index}",
                "action": "ENTRY",
                "status": "FILLED",
                "context": context,
            }
        )
        timestamp += 1
        rows.append(
            {
                "timestamp": timestamp,
                "decision_id": None,
                "action": outcome,
                "status": "CONFIRMED",
                "context": {},
            }
        )
        timestamp += 1
    _write_events(tmp_path, rows)

    result = MistakeLearningEngine(tmp_path, min_samples=5).analyze()

    assert result["status"] == "READY"
    assert result["closed_trades"] == 5
    assert result["stop_losses"] == 4
    assert result["take_profits"] == 1
    assert result["auto_parameter_changes"] is False
    assert result["trade_patterns"][0]["loss_rate"] == 0.8
    assert result["review_candidates"]
    assert result["review_candidates"][0]["recommendation"] == "REVIEW_AND_BACKTEST"
    assert "BREAKOUT_RETEST" in result["review_candidates"][0]["context"]


def test_learning_engine_does_not_overreact_before_minimum_sample(tmp_path):
    context = {
        "setup_type": "TREND_PULLBACK",
        "direction": "SHORT",
        "regime": "BEAR",
        "market_basis": "FUTURES_NATIVE",
    }
    rows = [
        {"timestamp": 1, "decision_id": "a", "action": "ENTRY", "status": "FILLED", "context": context},
        {"timestamp": 2, "action": "STOP_LOSS", "status": "CONFIRMED", "context": {}},
        {"timestamp": 3, "decision_id": "b", "action": "ENTRY", "status": "FILLED", "context": context},
        {"timestamp": 4, "action": "STOP_LOSS", "status": "CONFIRMED", "context": {}},
    ]
    _write_events(tmp_path, rows)

    result = MistakeLearningEngine(tmp_path, min_samples=5).analyze()

    assert result["status"] == "WARMUP"
    assert result["closed_trades"] == 2
    assert result["review_candidates"] == []
    assert result["auto_parameter_changes"] is False
