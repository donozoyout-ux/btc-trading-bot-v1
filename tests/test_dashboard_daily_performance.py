"""Daily TESTNET performance is authoritative, read-only and Istanbul-based."""

from datetime import datetime, timezone

import pytest

from data.binance_client import BinanceFuturesAccountClient


def _client():
    return BinanceFuturesAccountClient("test-key", "test-secret", testnet=True)


@pytest.mark.parametrize(
    ("realized", "commission", "funding", "expected"),
    [
        ("25.50", "-0.75", "-0.25", 24.50),
        ("-12.00", "-0.40", "0.10", -12.30),
    ],
)
def test_daily_net_pnl_includes_realized_commission_and_funding(
    monkeypatch, realized, commission, funding, expected
):
    client = _client()
    now = datetime(2026, 9, 9, 9, 0, tzinfo=timezone.utc)
    start, _ = client._istanbul_day_bounds_ms(now)
    rows = [
        {"symbol": "BTCUSDT", "incomeType": "REALIZED_PNL", "income": realized, "time": start + 1_000},
        {"symbol": "BTCUSDT", "incomeType": "COMMISSION", "income": commission, "time": start + 2_000},
        {"symbol": "BTCUSDT", "incomeType": "FUNDING_FEE", "income": funding, "time": start + 3_000},
    ]
    monkeypatch.setattr(client, "_signed_get", lambda path, params=None: rows)

    result = client.get_daily_performance(now=now)

    assert result["status"] == "AVAILABLE"
    assert result["source"] == "BINANCE_TESTNET_INCOME_HISTORY"
    assert result["realized_pnl_usdt"] == float(realized)
    assert result["commission_usdt"] == float(commission)
    assert result["funding_usdt"] == float(funding)
    assert result["net_pnl_usdt"] == pytest.approx(expected)


def test_daily_performance_uses_istanbul_day_and_excludes_yesterday(monkeypatch):
    client = _client()
    now = datetime(2026, 9, 8, 22, 30, tzinfo=timezone.utc)  # 01:30 TSİ, Sep 9
    start, end = client._istanbul_day_bounds_ms(now)
    captured = {}
    rows = [
        {"symbol": "BTCUSDT", "incomeType": "REALIZED_PNL", "income": "99", "time": start - 1},
        {"symbol": "BTCUSDT", "incomeType": "REALIZED_PNL", "income": "8", "time": start},
        {"symbol": "BTCUSDT", "incomeType": "COMMISSION", "income": "-1", "time": end - 1},
        {"symbol": "BTCUSDT", "incomeType": "REALIZED_PNL", "income": "77", "time": end},
    ]

    def fake_get(path, params=None):
        captured.update({"path": path, "params": params})
        return rows

    monkeypatch.setattr(client, "_signed_get", fake_get)
    result = client.get_daily_performance(now=now)

    assert datetime.fromtimestamp(start / 1000, timezone.utc).hour == 21
    assert captured["path"] == "/fapi/v1/income"
    assert captured["params"]["startTime"] == start
    assert captured["params"]["endTime"] == end - 1
    assert result["net_pnl_usdt"] == 7.0
    assert result["closed_trades"] == 1
    assert result["winning_trades"] == 1
    assert result["losing_trades"] == 0


def test_empty_daily_history_is_unavailable_not_fake_zero(monkeypatch):
    client = _client()
    monkeypatch.setattr(client, "_signed_get", lambda path, params=None: [])

    result = client.get_daily_performance(
        now=datetime(2026, 9, 9, 12, 0, tzinfo=timezone.utc)
    )

    assert result["status"] == "UNAVAILABLE"
    for key in (
        "realized_pnl_usdt",
        "commission_usdt",
        "funding_usdt",
        "net_pnl_usdt",
        "closed_trades",
        "winning_trades",
        "losing_trades",
    ):
        assert result[key] is None


def test_daily_performance_client_is_testnet_only_and_cannot_place_orders():
    client = _client()
    assert client.testnet is True
    assert client.read_only is True
    assert not hasattr(client, "place_order")

