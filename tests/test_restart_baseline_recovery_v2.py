from copy import deepcopy
from types import SimpleNamespace

import pytest

from engines.restart_baseline_recovery import RestartBaselineRecovery
from engines.trade_state_engine import ActiveTradeStateEngine
from execution.safer_testnet_executor import SaferTestnetExecutor


ENTRY_TIME = 1_800_000_000_000


def position(side="LONG", quantity=1):
    signed = quantity if side == "LONG" else -quantity
    return {"symbol": "BTCUSDT", "position_amt": signed, "side": side, "entry_price": 100, "mark_price": 105 if side == "LONG" else 95, "unrealized_pnl": 5}


def trades(side="LONG", quantity=1):
    return [{"symbol": "BTCUSDT", "orderId": 10, "id": 1, "side": "BUY" if side == "LONG" else "SELL", "qty": str(quantity), "price": "100", "time": ENTRY_TIME}]


def stop(trigger=90, *, side="SELL", quantity=1, created=ENTRY_TIME + 1_000, symbol="BTCUSDT", order_id=20):
    return {"symbol": symbol, "algoId": order_id, "orderType": "STOP_MARKET", "side": side, "triggerPrice": str(trigger), "quantity": str(quantity), "reduceOnly": True, "createTime": created, "algoStatus": "CANCELED"}


def recover(*, pos=None, fill_rows=None, orders=None):
    return RestartBaselineRecovery().recover(pos or position(), fill_rows or trades(), [], [stop()] if orders is None else orders)


def test_restart_historical_entry_and_original_stop_is_verified():
    result = recover()
    assert result["exchange_baseline_verified"] is True
    assert result["context_status"] == "RECONSTRUCTED_VERIFIED"
    assert result["actual_initial_stop"] == 90
    assert result["initial_stop_source"] == "BINANCE_HISTORICAL_ORDER"


def test_later_tightened_stop_never_replaces_earliest_initial_stop():
    result = recover(orders=[stop(95, created=ENTRY_TIME + 20_000, order_id=22), stop(90, created=ENTRY_TIME + 1_000, order_id=20)])
    assert result["actual_initial_stop"] == 90
    assert result["historical_stop_order_id"] == 20


def test_multiple_stops_select_earliest_valid_lifecycle_stop():
    result = recover(orders=[stop(88, created=ENTRY_TIME + 2_000, order_id=21), stop(90, created=ENTRY_TIME + 1_000, order_id=20)])
    assert result["actual_initial_stop"] == 90


@pytest.mark.parametrize("bad_order", [
    stop(80, created=ENTRY_TIME - 1, order_id=1),
    stop(90, side="BUY"),
    stop(90, quantity=.5),
    stop(110),
    stop(90, symbol="ETHUSDT"),
])
def test_unrelated_wrong_side_quantity_direction_or_symbol_is_ignored(bad_order):
    result = recover(orders=[bad_order])
    assert result["verified"] is False
    assert result["reason"] == "ORIGINAL_INITIAL_STOP_UNVERIFIED"


def test_no_provable_stop_remains_partial():
    assert recover(orders=[])["verified"] is False


class MemoryJournal:
    def __init__(self, state=None): self.state, self.records = dict(state or {}), []
    def read_state(self): return deepcopy(self.state)
    def write_state(self, state): self.state = deepcopy(state)
    def record(self, **event): self.records.append(event); return event


class HistoryClient:
    testnet = True
    configured = True

    def __init__(self):
        self.position = position()
        self.open_algo = [stop(95, created=ENTRY_TIME + 60_000, order_id=99), {"symbol": "BTCUSDT", "algoId": 100, "orderType": "TAKE_PROFIT_MARKET", "side": "SELL", "triggerPrice": "120", "quantity": "1", "reduceOnly": True}]
        self.history = [stop(90), stop(95, created=ENTRY_TIME + 60_000, order_id=99)]
        self.mutations = []

    def get_position(self, symbol="BTCUSDT"): return deepcopy(self.position)
    def get_open_orders(self, symbol=None): return []
    def get_open_algo_orders(self, symbol=None): return deepcopy(self.open_algo)
    def get_user_trades(self, symbol="BTCUSDT"): return trades()
    def get_order_history(self, symbol="BTCUSDT", start_time=None): return []
    def get_algo_order_history(self, symbol="BTCUSDT", start_time=None): return deepcopy(self.history)
    def get_closed_klines_since(self, symbol, interval, start_time):
        return [{"timestamp": ENTRY_TIME, "high": 108, "low": 98, "is_closed": True}, {"timestamp": ENTRY_TIME + 300_000, "high": 999, "low": 1, "is_closed": False}]
    def place_protective_order(self, *args, **kwargs): self.mutations.append("place"); raise AssertionError("no order mutation")
    def cancel_algo_order(self, *args, **kwargs): self.mutations.append("cancel"); raise AssertionError("no order mutation")


def settings():
    return SimpleNamespace(testnet_execution_enabled=True, BINANCE_TESTNET=True, ENV="testnet", ORDER_SUBMISSION_ENABLED=True, ACCOUNT_READ_ONLY=False, SHADOW_MODE=False, TP_SPLIT_CONSERVATIVE=.7, TP_SPLIT_BALANCED=.5, TP_SPLIT_TREND_RUNNER=.35)


def test_reconstructed_baseline_persists_rebuilds_excursions_and_leaves_open_orders_untouched():
    client, journal = HistoryClient(), MemoryJournal()
    before = deepcopy(client.open_algo)
    executor = SaferTestnetExecutor(client, settings=settings(), execution_journal=journal)
    result = executor.recover_from_exchange()
    assert result["status"] == "RECONSTRUCTED_VERIFIED"
    assert journal.state["entry_context"]["actual_initial_stop"] == 90
    assert journal.state["entry_context"]["context_status"] == "RECONSTRUCTED_VERIFIED"
    assert executor.management_mfe_r == pytest.approx(.8)
    assert executor.management_mae_r == pytest.approx(-.2)
    assert client.open_algo == before
    assert client.mutations == []

    restarted = SaferTestnetExecutor(client, settings=settings(), execution_journal=journal)
    assert restarted._has_verified_exchange_baseline() is True
    assert restarted._entry_context["initial_stop_source"] == "PERSISTED_ENTRY_CONTEXT"


@pytest.mark.parametrize(("direction", "trigger", "high", "low", "mfe", "mae"), [
    ("LONG", 90, 108, 97, .8, -.3),
    ("SHORT", 110, 103, 92, .8, -.3),
])
def test_long_short_r_excursions_use_closed_candles_only(direction, trigger, high, low, mfe, mae):
    candles = [
        {"timestamp": ENTRY_TIME, "high": high, "low": low, "is_closed": True},
        {"timestamp": ENTRY_TIME + 300_000, "high": 999, "low": 1, "is_closed": False},
    ]
    result = RestartBaselineRecovery.rebuild_excursions(direction, 100, trigger, ENTRY_TIME, candles)
    assert result["mfe_r"] == pytest.approx(mfe)
    assert result["mae_r"] == pytest.approx(mae)


@pytest.mark.parametrize(("direction", "amount", "mark", "initial_stop", "current_stop", "high", "low"), [
    ("LONG", 1, 105, 90, 101, 108, 97),
    ("SHORT", -1, 95, 110, 99, 103, 92),
])
def test_reconstructed_context_restores_all_canonical_r_metrics(direction, amount, mark, initial_stop, current_stop, high, low):
    account = {
        "positions": [{"symbol": "BTCUSDT", "position_amount": amount, "entry_price": 100, "mark_price": mark, "unrealized_pnl": 5}],
        "open_orders": [{"symbol": "BTCUSDT", "type": "STOP_MARKET", "stop_price": current_stop, "quantity": 1}],
    }
    execution = {"entry_context": {"exchange_baseline_verified": True, "actual_entry_price": 100, "actual_initial_position_size": 1, "actual_initial_stop": initial_stop, "entry_opened_at": ENTRY_TIME, "context_status": "RECONSTRUCTED_VERIFIED", "initial_stop_source": "BINANCE_HISTORICAL_ORDER"}}
    state = ActiveTradeStateEngine().build(account=account, execution=execution, mark_price=mark, closed_5m_candles=[{"timestamp": ENTRY_TIME, "high": high, "low": low, "is_closed": True}], durable_state="PERSISTENT")
    assert state["context_status"] == "RECONSTRUCTED_VERIFIED"
    assert state["initial_stop_source"] == "BINANCE_HISTORICAL_ORDER"
    assert state["current_r"] == pytest.approx(.5)
    assert state["mfe_r"] == pytest.approx(.8)
    assert state["mae_r"] == pytest.approx(-.3)
    assert state["giveback_r"] == pytest.approx(.3)
    assert state["protected_r"] == pytest.approx(.1)


def test_read_only_account_history_surface_has_no_order_submission():
    from data.binance_client import BinanceFuturesAccountClient
    assert hasattr(BinanceFuturesAccountClient, "get_order_history")
    assert hasattr(BinanceFuturesAccountClient, "get_algo_order_history")
    assert hasattr(BinanceFuturesAccountClient, "get_user_trades")
    assert not hasattr(BinanceFuturesAccountClient, "place_order")


def test_dashboard_explains_reconstructed_and_unavailable_context():
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    html = (root / "dashboard" / "index.html").read_text(encoding="utf-8")
    app = (root / "dashboard" / "app.js").read_text(encoding="utf-8")
    engine = (root / "engines" / "trade_state_engine.py").read_text(encoding="utf-8")
    assert "positionContextStatus" in html
    assert "YENİDEN DOĞRULANDI" in app
    assert "Orijinal başlangıç stopu Binance geçmişinden doğrulanamadı." in engine
