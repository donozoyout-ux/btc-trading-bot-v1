from datetime import datetime, timezone

import pytest

from core.models import Candle
from engines.daily_trade_ledger import DailyTradeLedger
from engines.trade_state_engine import ActiveTradeStateEngine
from storage.state_repository import LocalStateRepository, create_state_repository


DAY = 1_800_000_000_000


def fill(at, side, qty, price=100, pnl=0):
    return {"symbol": "BTCUSDT", "time": at, "side": side, "qty": str(qty), "price": str(price), "realizedPnl": str(pnl)}


def test_daily_ledger_open_today_and_partial_targets_are_one_trade():
    rows = [fill(DAY + 1, "BUY", 1), fill(DAY + 2, "SELL", .4, 110, 4), fill(DAY + 3, "SELL", .6, 120, 12)]
    result = DailyTradeLedger().build(rows, day_start_ms=DAY, day_end_ms=DAY + 86_400_000)
    assert result["opened_trades_today"] == 1
    assert result["closed_trades_today"] == 1
    assert result["winning_trades_today"] == 1


def test_yesterday_open_position_is_not_opened_today():
    result = DailyTradeLedger().build([fill(DAY - 1, "SELL", 1)], day_start_ms=DAY, day_end_ms=DAY + 86_400_000)
    assert result["opened_trades_today"] == 0
    assert result["currently_opened_today"] == 0


def account(side="SHORT", liquidation=140, orders=None):
    amount = -1 if side == "SHORT" else 1
    return {"positions": [{"symbol": "BTCUSDT", "side": side, "position_amount": amount, "size": 1, "entry_price": 100, "mark_price": 95 if side == "SHORT" else 105, "liquidation_price": liquidation, "unrealized_pnl": 5}], "open_orders": orders or []}


def baseline(side="SHORT"):
    return {"entry_context": {"exchange_baseline_verified": True, "actual_entry_price": 100, "actual_initial_position_size": 1, "actual_initial_stop": 110 if side == "SHORT" else 90, "entry_opened_at": DAY}}


def test_field_merge_liquidation_and_exchange_protection_survive_empty_intelligence():
    orders = [
        {"symbol": "BTCUSDT", "type": "STOP_MARKET", "stop_price": 110, "quantity": 1},
        {"symbol": "BTCUSDT", "type": "TAKE_PROFIT_MARKET", "stop_price": 90, "quantity": .5},
        {"symbol": "BTCUSDT", "type": "TAKE_PROFIT_MARKET", "stop_price": 80, "quantity": .5},
    ]
    execution = baseline() | {"position": {"side": "SHORT", "position_amt": -1, "entry_price": 100}}
    state = ActiveTradeStateEngine().build(account=account(orders=orders), execution=execution, mark_price=95, closed_5m_candles=[], durable_state="LOCAL_EPHEMERAL")
    assert state["liquidation_price"] == 140
    assert state["stop_price"] == 110
    assert (state["tp1_price"], state["tp2_price"]) == (90, 80)
    assert state["protection_status"] == "HEALTHY"


def test_initial_stop_never_uses_tightened_exchange_stop():
    orders = [{"symbol": "BTCUSDT", "type": "STOP_MARKET", "stop_price": 101, "quantity": 1}]
    state = ActiveTradeStateEngine().build(account=account(orders=orders), execution={"position": {"side": "SHORT", "position_amt": -1}}, mark_price=95, closed_5m_candles=[], durable_state="LOCAL_EPHEMERAL")
    assert state["stop_price"] == 101
    assert state["initial_stop"] is None
    assert state["current_r"] is None
    assert state["context_status"] == "PARTIAL"


@pytest.mark.parametrize(("side", "high", "low", "expected"), [("LONG", 108, 99, .8), ("SHORT", 101, 92, .8)])
def test_closed_5m_mfe_rebuild_long_and_short(side, high, low, expected):
    acc = account(side=side, orders=[])
    mark = 105 if side == "LONG" else 95
    candles = [Candle(timestamp=DAY, open=100, high=high, low=low, close=mark, volume=1, is_closed=True)]
    state = ActiveTradeStateEngine().build(account=acc, execution=baseline(side), mark_price=mark, closed_5m_candles=candles, durable_state="LOCAL_EPHEMERAL")
    assert state["mfe_r"] == pytest.approx(expected)


def test_candle_before_entry_is_ignored():
    candles = [Candle(timestamp=DAY - 300_000, open=100, high=200, low=1, close=100, volume=1, is_closed=True)]
    state = ActiveTradeStateEngine().build(account=account(), execution=baseline(), mark_price=95, closed_5m_candles=candles, durable_state="LOCAL_EPHEMERAL")
    assert state["mfe_r"] == 0


def test_open_candle_is_ignored():
    candles = [Candle(timestamp=DAY, open=100, high=200, low=1, close=100, volume=1, is_closed=False)]
    state = ActiveTradeStateEngine().build(account=account(), execution=baseline(), mark_price=95, closed_5m_candles=candles, durable_state="LOCAL_EPHEMERAL")
    assert state["mfe_r"] == 0


@pytest.mark.parametrize(("orders", "expected"), [
    ([{"symbol": "BTCUSDT", "type": "STOP_MARKET", "stop_price": 110}], "STOP_ONLY"),
    ([{"symbol": "BTCUSDT", "type": "TAKE_PROFIT_MARKET", "stop_price": 90}], "TARGET_ONLY"),
    ([], "UNPROTECTED"),
    ([{"symbol": "BTCUSDT", "type": "STOP_MARKET", "stop_price": 110}, {"symbol": "BTCUSDT", "type": "STOP_MARKET", "stop_price": 111}], "AMBIGUOUS"),
])
def test_protection_health_classification(orders, expected):
    state = ActiveTradeStateEngine().build(account=account(orders=orders), execution=baseline(), mark_price=95, closed_5m_candles=[], durable_state="LOCAL_EPHEMERAL")
    assert state["protection_status"] == expected


def test_local_state_repository_filters_credentials_and_falls_back(tmp_path, monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    repo = create_state_repository(tmp_path / "state.json")
    assert isinstance(repo, LocalStateRepository)
    repo.save("execution_state", {"entry_context": {"actual_initial_stop": 90}, "api_secret": "never", "telegram_token": "never"})
    restored = repo.load("execution_state")
    assert restored["entry_context"]["actual_initial_stop"] == 90
    assert "api_secret" not in restored and "telegram_token" not in restored
