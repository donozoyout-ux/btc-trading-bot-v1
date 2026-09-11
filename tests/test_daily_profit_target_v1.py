from datetime import datetime, timezone
from pathlib import Path

import pytest

from config.settings import BotSettings
from core.state import BotState
from engines.daily_profit_target import DailyProfitTargetEngine
from execution.testnet_executor import TestnetExecutor
from execution.testnet_runtime import TestnetExecutionRuntime


class MemoryRepository:
    durability = "PERSISTENT"

    def __init__(self, state=None):
        self.data = dict(state or {})

    def load(self, key):
        return dict(self.data.get(key) or {})

    def save(self, key, value):
        self.data[key] = dict(value)


NOW = datetime(2026, 9, 11, 9, 0, tzinfo=timezone.utc)  # 12:00 Istanbul


def row(amount, kind="REALIZED_PNL", minute=1):
    start = int(datetime(2026, 9, 10, 21, 0, tzinfo=timezone.utc).timestamp() * 1000)
    return {"time": start + minute * 60_000, "asset": "USDT", "incomeType": kind, "income": str(amount)}


def build(rows, wallet, repo=None, now=NOW, **kwargs):
    engine = DailyProfitTargetEngine(repo or MemoryRepository(), **kwargs)
    return engine, engine.build(current_wallet_usdt=wallet, income_rows=rows, income_history_available=True, now=now)


@pytest.mark.parametrize(("net", "reached"), [(49.99, False), (50.0, True), (55.0, True)])
def test_one_percent_target_boundaries(net, reached):
    _, target = build([row(net)], 5000 + net)
    assert target["opening_balance_usdt"] == pytest.approx(5000)
    assert target["target_profit_usdt"] == pytest.approx(50)
    assert target["target_reached_once"] is reached
    assert target["new_entries_allowed"] is (not reached)


def test_commission_and_funding_are_net_but_transfer_and_unrealized_are_not():
    rows = [row(55), row(-2, "COMMISSION", 2), row(1, "FUNDING_FEE", 3), row(100, "TRANSFER", 4)]
    _, target = build(rows, 5154)  # opening 5000 after every wallet-affecting row
    assert target["opening_balance_usdt"] == pytest.approx(5000)
    assert target["net_realized_pnl_usdt"] == pytest.approx(54)
    assert target["target_reached_once"] is True
    assert "unrealized" not in target


def test_unrealized_profit_cannot_reach_target():
    _, target = build([row(10)], 5010)
    assert target["net_realized_pnl_usdt"] == 10
    assert target["target_reached_once"] is False


def test_latch_survives_fall_below_target_and_restart():
    repo = MemoryRepository()
    engine, reached = build([row(50)], 5050, repo=repo)
    assert reached["status"] == "TARGET_REACHED"
    later = engine.build(current_wallet_usdt=5040, income_rows=[row(50), row(-10, "FUNDING_FEE", 2)], income_history_available=True, now=NOW)
    assert later["status"] == "LOCKED_FOR_DAY"
    restarted = DailyProfitTargetEngine(repo).build(current_wallet_usdt=5040, income_rows=[row(40)], income_history_available=True, now=NOW)
    assert restarted["target_reached_once"] is True
    assert restarted["new_entries_allowed"] is False


def test_lost_local_state_recovers_earlier_crossing_chronologically():
    rows = [row(51), row(-5, "FUNDING_FEE", 2)]
    _, target = build(rows, 5046, repo=MemoryRepository())
    assert target["net_realized_pnl_usdt"] == 46
    assert target["target_reached_now"] is False
    assert target["target_reached_once"] is True
    assert target["status"] == "LOCKED_FOR_DAY"


def test_istanbul_new_day_resets_latch_and_reconstructs_fresh_opening():
    repo = MemoryRepository({"daily_profit_target": {"date_istanbul": "2026-09-10", "opening_balance_usdt": 5000, "target_profit_usdt": 50, "target_reached_once": True}})
    next_day = datetime(2026, 9, 11, 21, 1, tzinfo=timezone.utc)  # Sep 12 Istanbul
    engine = DailyProfitTargetEngine(repo)
    target = engine.build(current_wallet_usdt=5050, income_rows=[], income_history_available=True, now=next_day)
    assert target["date_istanbul"] == "2026-09-12"
    assert target["opening_balance_source"] == "BINANCE_RECONSTRUCTED"
    assert target["target_reached_once"] is False


def test_unavailable_accounting_never_uses_initial_capital_and_fails_closed():
    target = DailyProfitTargetEngine(MemoryRepository()).build(current_wallet_usdt=None, income_rows=[], income_history_available=False, now=NOW)
    assert target["status"] == "DATA_UNAVAILABLE"
    assert target["opening_balance_usdt"] is None
    assert target["new_entries_allowed"] is False
    assert target["entry_block_reason"] == "DAILY_PROFIT_TARGET_DATA_UNAVAILABLE"


class FlatClient:
    testnet = True
    configured = True

    def __init__(self):
        self.market_orders = 0

    def get_positions(self): return []
    def get_position(self, symbol): return {"symbol": symbol, "position_amt": 0, "side": "FLAT"}
    def place_market_order(self, *args, **kwargs):
        self.market_orders += 1
        raise AssertionError("entry submission must remain unreachable")


class Journal:
    durable_state = "LOCAL_EPHEMERAL"
    state_repository = MemoryRepository()
    def read_state(self): return {}
    def write_state(self, state): self.state = state
    def record(self, **kwargs): return kwargs


def settings():
    return BotSettings(_env_file=None, ENV="testnet", BINANCE_TESTNET=True, BINANCE_API_KEY="key", BINANCE_API_SECRET="secret", ACCOUNT_READ_ONLY=False, ORDER_SUBMISSION_ENABLED=True, SHADOW_MODE=False)


def test_executor_defense_in_depth_blocks_direct_entry_call():
    client = FlatClient()
    executor = TestnetExecutor(client, settings=settings(), execution_journal=Journal())
    result = executor.process_snapshot({"candles": {"5m": [{"time": 1}]}, "daily_profit_target": {"new_entries_allowed": False, "entry_block_reason": "DAILY_PROFIT_TARGET_REACHED"}}, BotState())
    assert result["status"] == "DAILY_PROFIT_TARGET_REACHED"
    assert client.market_orders == 0


class RuntimeExecutor:
    def __init__(self, managed): self.managed, self.process_calls = managed, 0
    def manage_existing_position(self, state): return dict(self.managed)
    def process_snapshot(self, snapshot, state): self.process_calls += 1; raise AssertionError("new entry path called")


def runtime_with(executor, target):
    runtime = object.__new__(TestnetExecutionRuntime)
    runtime.executor = executor
    runtime.state = BotState()
    runtime.dashboard = type("Dashboard", (), {"snapshot": lambda self, force=False: {"daily_profit_target": target}})()
    return runtime


@pytest.mark.parametrize("reason", ["DAILY_PROFIT_TARGET_REACHED", "DAILY_PROFIT_TARGET_DATA_UNAVAILABLE"])
def test_runtime_flat_guard_blocks_before_entry(reason):
    executor = RuntimeExecutor({"status": "FLAT"})
    result = runtime_with(executor, {"new_entries_allowed": False, "entry_block_reason": reason}).run_cycle()
    assert result["status"] == reason
    assert executor.process_calls == 0


def test_active_position_management_runs_before_target_guard():
    executor = RuntimeExecutor({"status": "POSITION_MANAGEMENT", "position": {"position_amt": 1}})
    runtime = runtime_with(executor, {"new_entries_allowed": False})
    result = runtime.run_cycle()
    assert result["status"] == "POSITION_MANAGEMENT"
    assert executor.process_calls == 0


def test_notification_dedupe_state_is_persisted():
    repo = MemoryRepository()
    engine, target = build([row(50)], 5050, repo=repo)
    assert target["notification_pending"] is True
    engine.mark_notification_sent()
    target = engine.build(current_wallet_usdt=5050, income_rows=[row(50)], income_history_available=True, now=NOW)
    assert target["notification_pending"] is False


def test_compact_turkish_dashboard_target_ui_keeps_trade_state_fields():
    root = Path(__file__).resolve().parents[1]
    html = (root / "dashboard" / "index.html").read_text(encoding="utf-8")
    app = (root / "dashboard" / "app.js").read_text(encoding="utf-8")
    assert html.index('class="summary-grid"') < html.index('id="dailyTargetSection"') < html.index('id="activeTradePanel"')
    for label in ("GÜNLÜK HEDEF", "+%1,00", "BUGÜNKÜ NET", "HEDEF", "KALAN"):
        assert label in html
    for label in ("HEDEF DEVAM EDİYOR", "HEDEFE YAKLAŞIYOR", "HEDEF TAMAMLANDI", "BUGÜN YENİ İŞLEM YOK", "HEDEF VERİSİ ALINAMIYOR"):
        assert label in app
    for existing in ("positionCurrentStop", "positionIntelTp1", "positionMfeR", "positionCurrentR", "summaryTodayTrades"):
        assert existing in html
