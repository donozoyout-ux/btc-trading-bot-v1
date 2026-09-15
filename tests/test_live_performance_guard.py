from __future__ import annotations

from config.settings import BotSettings
from execution.testnet_executor import TestnetExecutor


class FakeClient:
    def __init__(self, fills):
        self.fills = fills

    def get_user_trades(self, symbol="BTCUSDT", *, start_time=None, end_time=None, limit=1000):
        return list(self.fills)


class BrokenHistoryClient:
    def get_user_trades(self, *args, **kwargs):
        raise RuntimeError("history unavailable")


def _fill(ts, side, qty=0.01, realized=0.0, commission=0.1, trade_id=1):
    return {
        "symbol": "BTCUSDT",
        "time": ts,
        "id": trade_id,
        "side": side,
        "qty": str(qty),
        "price": "80000",
        "realizedPnl": str(realized),
        "commission": str(commission),
        "positionSide": "BOTH",
    }


def _loss_cycle(entry_ts, exit_ts, *, amount=-10.0, offset=0):
    return [
        _fill(entry_ts, "BUY", realized=0.0, trade_id=1 + offset),
        _fill(exit_ts, "SELL", realized=amount, trade_id=2 + offset),
    ]


def _win_cycle(entry_ts, exit_ts, *, amount=5.0, offset=0):
    return [
        _fill(entry_ts, "BUY", realized=0.0, trade_id=1 + offset),
        _fill(exit_ts, "SELL", realized=amount, trade_id=2 + offset),
    ]


def _executor(client, tmp_path, **overrides):
    settings = BotSettings(
        JOURNAL_DIR=str(tmp_path),
        PERFORMANCE_GUARD_ENABLED=True,
        PERFORMANCE_GUARD_CONSECUTIVE_LOSSES=2,
        PERFORMANCE_GUARD_COOLDOWN_HOURS=12,
        PERFORMANCE_GUARD_RISK_MULTIPLIER=0.5,
        PERFORMANCE_GUARD_ROLLING_WINDOW=10,
        PERFORMANCE_GUARD_MIN_PROFIT_FACTOR=1.0,
        **overrides,
    )
    return TestnetExecutor(client, settings=settings)


def test_two_consecutive_exchange_losses_start_cross_day_cooldown(tmp_path):
    now = 2_000_000_000_000
    fills = []
    fills += _loss_cycle(now - 14 * 3600_000, now - 13 * 3600_000, offset=0)
    fills += _loss_cycle(now - 2 * 3600_000, now - 1 * 3600_000, offset=10)

    guard = _executor(FakeClient(fills), tmp_path)._execution_performance_guard(
        4927.0,
        now_ms=now,
    )

    assert guard["allowed"] is False
    assert guard["reason"] == "CONSECUTIVE_LOSS_COOLDOWN"
    assert guard["consecutive_losses"] == 2
    assert guard["cooldown_until"] > now


def test_single_recent_loss_reduces_next_trade_risk(tmp_path):
    now = 2_000_000_000_000
    fills = _loss_cycle(now - 2 * 3600_000, now - 1 * 3600_000)

    guard = _executor(FakeClient(fills), tmp_path)._execution_performance_guard(
        4950.0,
        now_ms=now,
    )

    assert guard["allowed"] is True
    assert guard["risk_multiplier"] == 0.5
    assert guard["reason"] == "REDUCED_RISK_AFTER_LOSS"


def test_weak_rolling_profit_factor_reduces_risk_even_after_a_win(tmp_path):
    now = 2_000_000_000_000
    fills = []
    for idx in range(6):
        base = now - (20 - idx * 2) * 3600_000
        fills += _loss_cycle(base, base + 1800_000, amount=-10.0, offset=idx * 10)
    for idx in range(4):
        base = now - (8 - idx * 2) * 3600_000
        fills += _win_cycle(base, base + 1800_000, amount=5.0, offset=100 + idx * 10)

    guard = _executor(FakeClient(fills), tmp_path)._execution_performance_guard(
        4900.0,
        now_ms=now,
    )

    assert guard["allowed"] is True
    assert guard["consecutive_losses"] == 0
    assert guard["rolling_profit_factor"] < 1.0
    assert guard["risk_multiplier"] == 0.5
    assert guard["reason"] == "REDUCED_RISK_WEAK_ROLLING_PF"


def test_exchange_daily_realized_loss_limit_blocks_new_entries(tmp_path):
    now = 2_000_000_000_000
    fills = _loss_cycle(now - 2 * 3600_000, now - 1 * 3600_000, amount=-120.0)

    guard = _executor(FakeClient(fills), tmp_path)._execution_performance_guard(
        4880.0,
        now_ms=now,
    )

    assert guard["allowed"] is False
    assert guard["reason"] == "DAILY_REALIZED_LOSS_LIMIT"
    assert guard["daily_drawdown_pct"] >= 2.0


def test_trade_history_failure_fails_closed(tmp_path):
    now = 2_000_000_000_000

    guard = _executor(BrokenHistoryClient(), tmp_path)._execution_performance_guard(
        5000.0,
        now_ms=now,
    )

    assert guard["allowed"] is False
    assert guard["reason"] == "TRADE_HISTORY_UNAVAILABLE"
