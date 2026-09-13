from copy import deepcopy
from types import SimpleNamespace

import pytest

from engines.restart_profit_fallback import RestartProfitFallback
from execution.restart_profit_fallback_manager import RestartProfitFallbackManager
from execution.safer_testnet_executor import SaferTestnetExecutor


class MemoryRepo:
    durability = "PERSISTENT"

    def __init__(self):
        self.data = {}

    def load(self, key):
        return dict(self.data.get(key) or {})

    def save(self, key, value):
        self.data[key] = dict(value)


class Journal:
    def __init__(self):
        self.records = []

    def record(self, **event):
        self.records.append(event)
        return event


class Client:
    def __init__(self, *, mark=76824.0, pnl=13.17):
        self.position = {
            "symbol": "BTCUSDT",
            "position_amt": -0.0398,
            "side": "SHORT",
            "entry_price": 77141.4,
            "mark_price": mark,
            "unrealized_pnl": pnl,
        }
        self.orders = [
            {
                "algoId": 1,
                "orderType": "STOP_MARKET",
                "side": "BUY",
                "triggerPrice": "77030.9",
                "quantity": "0.0398",
                "reduceOnly": True,
            },
            {
                "algoId": 2,
                "orderType": "TAKE_PROFIT_MARKET",
                "side": "BUY",
                "triggerPrice": "76587.5",
                "quantity": "0.0398",
                "reduceOnly": True,
            },
        ]

    def get_open_algo_orders(self, symbol):
        return deepcopy(self.orders)

    def get_mark_price(self, symbol):
        return float(self.position["mark_price"])

    def normalize_quantity(self, symbol, quantity, **kwargs):
        return round(float(quantity), 4)


class Executor:
    def __init__(self, client):
        self.client = client
        self.protection_reconciliation_required = False
        self.execution_journal = Journal()
        self._known_position = deepcopy(client.position)
        self.notifications = []
        self.stop_replacements = []
        self.partial_calls = []
        self.closed = 0

    def _has_verified_exchange_baseline(self):
        return False

    @staticmethod
    def _order_type(order):
        return str(order.get("orderType") or order.get("type") or "").upper()

    @staticmethod
    def _trigger_price(order):
        return float(order.get("triggerPrice") or 0)

    @staticmethod
    def _safe_float(value):
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    def _validate_stop(self, order, position, expected_quantity):
        return (
            self._order_type(order) == "STOP_MARKET"
            and order.get("side") == "BUY"
            and bool(order.get("reduceOnly"))
            and abs(float(order.get("quantity")) - expected_quantity) < 1e-9
            and float(order.get("triggerPrice")) > float(position.get("mark_price"))
        )

    def _replace_stop_safely(self, position, new_stop):
        old = next(row for row in self.client.orders if row["orderType"] == "STOP_MARKET")
        assert float(new_stop) <= float(old["triggerPrice"])
        assert float(new_stop) > float(position["mark_price"])
        old["triggerPrice"] = str(new_stop)
        old["quantity"] = str(abs(float(position["position_amt"])))
        self.stop_replacements.append(float(new_stop))

    def _take_partial_safely(self, position, quantity):
        self.partial_calls.append(quantity)
        updated = deepcopy(position)
        updated["position_amt"] = float(position["position_amt"]) + quantity
        self.client.position = deepcopy(updated)
        return {"position": updated, "order": {"status": "FILLED"}}

    def _reconcile_profit_partial_protection(self, position):
        remaining = abs(float(position["position_amt"]))
        for row in self.client.orders:
            row["quantity"] = str(remaining)

    def _fast_profit_exit_and_reconcile(self, position, state, payload):
        self.closed += 1
        flat = deepcopy(position)
        flat["position_amt"] = 0.0
        self.client.position = deepcopy(flat)
        self.client.orders = []
        return {"position": flat, "order": {"status": "FILLED"}, "stale_orders_cancelled": 2}

    def _notify(self, event, payload, dedupe_key):
        self.notifications.append((event, payload, dedupe_key))
        return {"sent": True}


def settings():
    return SimpleNamespace()


def test_engine_partial_at_ten_usdt_and_lock_four():
    decision = RestartProfitFallback().evaluate(current_pnl_usdt=13.17)
    assert decision.action == "CLOSE_PARTIAL"
    assert decision.partial_fraction == pytest.approx(0.30)
    assert decision.desired_lock_usdt == pytest.approx(4.0)
    assert decision.armed is True


def test_engine_giveback_exit_after_40_percent_peak_retrace():
    decision = RestartProfitFallback().evaluate(
        current_pnl_usdt=9.0,
        previous_peak_pnl_usdt=15.0,
        partial_taken=True,
    )
    assert decision.giveback_usdt == pytest.approx(6.0)
    assert decision.giveback_pct == pytest.approx(0.40)
    assert decision.action == "CLOSE_FULL"


def test_engine_does_not_act_below_five_usdt():
    decision = RestartProfitFallback().evaluate(current_pnl_usdt=4.99)
    assert decision.action == "NONE"
    assert decision.armed is False


def test_manager_current_screenshot_like_profit_takes_partial_and_tightens_short_stop():
    client = Client(mark=76824.0, pnl=13.17)
    executor = Executor(client)
    repo = MemoryRepo()
    manager = RestartProfitFallbackManager(executor, settings(), state_repository=repo)

    result = manager.manage(client.position, SimpleNamespace())

    assert result["status"] == "RESTART_PROFIT_FALLBACK_PARTIAL"
    assert len(executor.partial_calls) == 1
    assert executor.partial_calls[0] == pytest.approx(0.0119)
    assert manager.partial_taken is True
    assert executor.stop_replacements
    new_stop = executor.stop_replacements[-1]
    assert new_stop < 77141.4
    assert new_stop > 76824.0
    # Peak is rebased to the remaining runner after the intentional 30% partial.
    assert repo.load(manager.KEY)["peak_pnl_usdt"] == pytest.approx((77141.4 - 76824.0) * 0.0279)


def test_manager_never_runs_without_valid_exchange_stop():
    client = Client(mark=76824.0, pnl=13.17)
    client.orders = [row for row in client.orders if row["orderType"] != "STOP_MARKET"]
    executor = Executor(client)
    manager = RestartProfitFallbackManager(executor, settings(), state_repository=MemoryRepo())

    assert manager.manage(client.position, SimpleNamespace()) is None
    assert executor.partial_calls == []
    assert executor.stop_replacements == []


def test_manager_skips_when_normal_verified_r_manager_is_available():
    client = Client(mark=76824.0, pnl=13.17)
    executor = Executor(client)
    executor._has_verified_exchange_baseline = lambda: True
    manager = RestartProfitFallbackManager(executor, settings(), state_repository=MemoryRepo())

    assert manager.manage(client.position, SimpleNamespace()) is None
    assert executor.partial_calls == []


def test_manager_peak_state_survives_restart_and_can_close_on_giveback():
    client = Client(mark=76750.0, pnl=16.0)
    executor = Executor(client)
    repo = MemoryRepo()
    first = RestartProfitFallbackManager(executor, settings(), state_repository=repo)
    first.partial_taken = True
    first.peak_pnl_usdt = 16.0
    first._save()

    client.position["mark_price"] = 76900.0
    client.position["unrealized_pnl"] = 9.0
    restarted = RestartProfitFallbackManager(executor, settings(), state_repository=repo)
    result = restarted.manage(client.position, SimpleNamespace())

    assert result["status"] == "RESTART_PROFIT_FALLBACK_EXIT"
    assert executor.closed == 1
    assert client.position["position_amt"] == 0.0



def test_close_position_stop_without_quantity_is_valid_full_position_protection():
    executor = SaferTestnetExecutor.__new__(SaferTestnetExecutor)
    position = {
        "symbol": "BTCUSDT",
        "position_amt": -0.0398,
        "mark_price": 76824.0,
    }
    order = {
        "algoId": 99,
        "orderType": "STOP_MARKET",
        "side": "BUY",
        "triggerPrice": "77030.9",
        "closePosition": "true",
    }
    assert executor._validate_stop(order, position, 0.0398) is True


def test_missing_quantity_stop_without_close_position_semantics_is_invalid():
    executor = SaferTestnetExecutor.__new__(SaferTestnetExecutor)
    position = {
        "symbol": "BTCUSDT",
        "position_amt": -0.0398,
        "mark_price": 76824.0,
    }
    order = {
        "algoId": 100,
        "orderType": "STOP_MARKET",
        "side": "BUY",
        "triggerPrice": "77030.9",
        "reduceOnly": True,
    }
    assert executor._validate_stop(order, position, 0.0398) is False


def test_partial_peak_rebase_prevents_false_giveback_exit_at_unchanged_price():
    client = Client(mark=76824.0, pnl=13.17)
    executor = Executor(client)
    repo = MemoryRepo()
    manager = RestartProfitFallbackManager(executor, settings(), state_repository=repo)

    first = manager.manage(client.position, SimpleNamespace())
    assert first["status"] == "RESTART_PROFIT_FALLBACK_PARTIAL"
    assert manager.partial_taken is True

    # Simulate the exchange's remaining-position unrealized PnL at the same mark.
    remaining = abs(float(client.position["position_amt"]))
    client.position["unrealized_pnl"] = (77141.4 - 76824.0) * remaining
    second = manager.manage(client.position, SimpleNamespace())

    assert second is None
    assert executor.closed == 0
    assert len(executor.partial_calls) == 1
