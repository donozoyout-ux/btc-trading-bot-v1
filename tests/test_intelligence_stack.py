"""Regression tests for the demo intelligence stack."""

from pathlib import Path

import pytest

from data.binance_client import BinanceFuturesClient
from integrations.ai_analyst import AIAnalyst
from integrations.news_engine import NewsEngine
from integrations.telegram_notifier import TelegramNotifier


def test_read_only_binance_client_blocks_orders():
    client = BinanceFuturesClient(api_key="x", api_secret="y", testnet=True, read_only=True)
    with pytest.raises(PermissionError):
        client.place_order()


def test_missing_account_credentials_never_return_fake_balance():
    client = BinanceFuturesClient(testnet=True, read_only=True)
    with pytest.raises(RuntimeError):
        client.get_account_balance()


def test_account_summary_filters_zero_positions(monkeypatch):
    client = BinanceFuturesClient(api_key="x", api_secret="y", testnet=True, read_only=True)

    account = {
        "assets": [
            {
                "asset": "USDT",
                "walletBalance": "1234.50",
                "availableBalance": "1100.25",
                "unrealizedProfit": "4.75",
                "marginBalance": "1239.25",
                "crossWalletBalance": "1234.50",
                "crossUnPnl": "4.75",
            }
        ],
        "totalInitialMargin": "100.00",
        "totalMaintMargin": "10.00",
        "totalPositionInitialMargin": "80.00",
        "totalOpenOrderInitialMargin": "20.00",
    }
    positions = [
        {"symbol": "BTCUSDT", "positionAmt": "0", "positionSide": "BOTH"},
        {
            "symbol": "BTCUSDT",
            "positionAmt": "0.010",
            "positionSide": "BOTH",
            "entryPrice": "50000",
            "markPrice": "50500",
            "notional": "505",
            "leverage": "5",
            "marginType": "cross",
            "unRealizedProfit": "5",
            "liquidationPrice": "40000",
            "isolatedWallet": "0",
        },
    ]
    orders = []

    monkeypatch.setattr(client, "get_account_information", lambda: account)
    monkeypatch.setattr(client, "get_position_risk", lambda symbol=None: positions)
    monkeypatch.setattr(client, "get_open_orders", lambda symbol=None: orders)

    result = client.get_account_summary()
    assert result["environment"] == "TESTNET"
    assert result["read_only"] is True
    assert result["orders_enabled"] is False
    assert result["wallet_balance_usdt"] == 1234.5
    assert result["available_balance_usdt"] == 1100.25
    assert result["open_position_count"] == 1
    assert result["positions"][0]["side"] == "LONG"


def test_telegram_status_never_exposes_credentials():
    tg = TelegramNotifier("secret-token", "12345", enabled=True)
    status = tg.status()
    dumped = str(status)
    assert "secret-token" not in dumped
    assert "12345" not in dumped
    assert status["configured"] is True


def test_ai_is_advisory_only():
    ai = AIAnalyst("secret", "model", enabled=True)
    status = ai.status()
    assert status["execution_authority"] is False
    assert "secret" not in str(status)


def test_news_keyword_scoring_is_transparent():
    row = NewsEngine._score_item({"title": "Bitcoin exchange hack triggers liquidation wave"})
    assert row["risk_score"] > 0
    assert row["sentiment"] == "BEARISH"
    assert "hack" in row["risk_terms"]


def test_dashboard_has_no_order_api_route():
    text = Path("dashboard_server.py").read_text(encoding="utf-8")
    assert '"/api/order"' not in text
    assert "ORDER SUBMISSION: DISABLED IN DASHBOARD" in text


def test_dashboard_account_client_is_read_only_by_construction():
    text = Path("dashboard_server.py").read_text(encoding="utf-8")
    assert "testnet=True" in text
    assert "read_only=True" in text
