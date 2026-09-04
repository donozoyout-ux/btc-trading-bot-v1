"""Safety tests for testnet account and Telegram dashboard integrations."""

import json
from pathlib import Path

import pytest

import data.binance_client as binance_module
from config.settings import BotSettings
from dashboard_server import DashboardRuntime
from data.binance_client import (
    AccountConnectionBlocked,
    BinanceAccountError,
    BinanceFuturesAccountClient,
    BinanceFuturesClient,
)
from notifications.telegram_client import TelegramClient, TelegramError


ROOT = Path(__file__).resolve().parents[1]


def settings(**overrides):
    values = {
        "BINANCE_TESTNET": True,
        "BINANCE_API_KEY": None,
        "BINANCE_API_SECRET": None,
        "COINGLASS_API_KEY": None,
        "COINMARKETCAP_API_KEY": None,
        "TELEGRAM_ENABLED": False,
        "TELEGRAM_BOT_TOKEN": None,
        "TELEGRAM_CHAT_ID": None,
    }
    values.update(overrides)
    return BotSettings(_env_file=None, **values)


class FailingAccountClient:
    configured = True
    testnet = True

    def get_account_summary(self):
        raise BinanceAccountError("INVALID_API_KEY")


def test_mainnet_configuration_blocks_account_connectivity():
    runtime = DashboardRuntime(
        settings=settings(
            BINANCE_TESTNET=False,
            BINANCE_API_KEY="testnet-key",
            BINANCE_API_SECRET="testnet-secret",
        )
    )
    result = runtime.account()
    assert result["connected"] is False
    assert result["status"] == "BLOCKED"
    assert result["error_category"] == "ACCOUNT_CONNECTION_BLOCKED"
    assert result["wallet_balance_usdt"] is None


def test_account_client_refuses_non_testnet_mode():
    client = BinanceFuturesAccountClient("key", "secret", testnet=False)
    with pytest.raises(AccountConnectionBlocked):
        client.get_account_summary()


def test_missing_credentials_returns_unavailable_not_fake_balance():
    result = DashboardRuntime(settings=settings()).account()
    assert result["connected"] is False
    assert result["error_category"] == "ACCOUNT_UNAVAILABLE"
    assert result["wallet_balance_usdt"] is None
    assert BinanceFuturesClient().get_account_balance() is None


def test_account_payload_never_contains_credentials_or_signature():
    config = settings(
        BINANCE_API_KEY="api-key-value",
        BINANCE_API_SECRET="api-secret-value",
    )
    result = DashboardRuntime(
        settings=config, account_client=FailingAccountClient()
    ).account()
    serialized = json.dumps(result)
    assert "api-key-value" not in serialized
    assert "api-secret-value" not in serialized
    forbidden = {"api_key", "api_secret", "signature", "authorization", "x-mbx-apikey"}
    assert not forbidden & result.keys()


def test_dashboard_exposes_no_order_endpoint():
    source = (ROOT / "dashboard_server.py").read_text(encoding="utf-8")
    assert "place_order(" not in source
    assert 'path == "/api/account"' in source
    assert 'path == "/api/telegram"' in source
    assert '"/api/telegram/test"' in source
    assert '"/api/ai/analyze"' in source
    assert '"/api/order"' not in source
    assert '"/api/buy"' not in source
    assert '"/api/sell"' not in source
    assert "def do_DELETE" not in source


def test_testnet_account_summary_maps_usdt_balance(monkeypatch):
    client = BinanceFuturesAccountClient("key", "secret", testnet=True)

    def fake_get(path, params=None):
        if path.endswith("account"):
            return {
                "assets": [{
                    "asset": "USDT",
                    "walletBalance": "10245.32",
                    "availableBalance": "9884.17",
                    "crossWalletBalance": "10240.00",
                    "crossUnPnl": "42.61",
                    "marginBalance": "10287.93",
                }],
                "totalUnrealizedProfit": "42.61",
                "totalInitialMargin": "361.15",
                "totalMaintMargin": "12.00",
                "totalPositionInitialMargin": "300.00",
                "totalOpenOrderInitialMargin": "61.15",
                "updateTime": 123456,
            }
        return []

    monkeypatch.setattr(client, "_signed_get", fake_get)
    result = client.get_account_summary()
    assert result["wallet_balance"] == 10245.32
    assert result["available_balance"] == 9884.17
    assert result["margin_balance"] == 10287.93
    assert result["unrealized_pnl"] == 42.61
    assert result["balances"][0]["cross_wallet_balance"] == 10240.0


def test_zero_size_positions_are_filtered(monkeypatch):
    client = BinanceFuturesAccountClient("key", "secret", testnet=True)
    monkeypatch.setattr(
        client,
        "_signed_get",
        lambda path, params=None: [
            {"symbol": "ETHUSDT", "positionAmt": "0"},
            {"symbol": "BTCUSDT", "positionAmt": "-0.125", "entryPrice": "60000"},
        ],
    )
    positions = client.get_open_positions()
    assert len(positions) == 1
    assert positions[0]["side"] == "SHORT"
    assert positions[0]["size"] == 0.125


def test_signed_timestamp_uses_server_offset(monkeypatch):
    client = BinanceFuturesAccountClient("key", "secret", recv_window=7000)
    monkeypatch.setattr(client, "sync_server_time", lambda force=False: 250)
    monkeypatch.setattr(binance_module.time, "time", lambda: 1000.0)
    signed = client._signed_params({"symbol": "BTCUSDT"})
    assert signed["timestamp"] == 1_000_250
    assert signed["recvWindow"] == 7000
    assert len(signed["signature"]) == 64


def test_account_failure_does_not_mutate_public_market_client():
    config = settings(BINANCE_API_KEY="key", BINANCE_API_SECRET="secret")
    runtime = DashboardRuntime(settings=config, account_client=FailingAccountClient())
    assert runtime.account()["error_category"] == "INVALID_API_KEY"
    assert runtime.binance.api_key is None
    assert runtime.binance.api_secret is None
    assert runtime.binance.testnet is False


def test_dashboard_account_client_is_always_testnet_and_read_only():
    runtime = DashboardRuntime(
        settings=settings(BINANCE_API_KEY="key", BINANCE_API_SECRET="secret")
    )
    assert runtime.account_client.testnet is True
    assert not hasattr(runtime.account_client, "place_order")


def test_telegram_missing_configuration_is_explicitly_unavailable():
    client = TelegramClient(None, None, enabled=False)
    assert client.safe_status() == {
        "enabled": False,
        "configured": False,
        "token_configured": False,
        "mode": "NOTIFICATIONS_ONLY",
        "commands_enabled": False,
        "trading_actions_enabled": False,
    }
    with pytest.raises(TelegramError, match="TELEGRAM_UNAVAILABLE"):
        client.send_message("test")


def test_telegram_calls_are_backend_only_and_return_safe_metadata(monkeypatch):
    client = TelegramClient("123:secret-token", "987654", enabled=True)
    calls = []

    class Response:
        status_code = 200

        def json(self):
            return {"ok": True, "result": {"message_id": 42, "date": 123}}

    def fake_post(url, json, timeout):
        calls.append((url, json, timeout))
        return Response()

    monkeypatch.setattr(client.session, "post", fake_post)
    result = client.send_message("Read-only connection ready")
    assert result == {"sent": True, "message_id": 42, "date": 123}
    assert calls[0][1]["chat_id"] == "987654"
    assert "123:secret-token" not in json.dumps(result)
    assert "987654" not in json.dumps(result)


def test_health_payload_contains_only_safe_integration_state():
    config = settings(
        BINANCE_API_KEY="key-value",
        BINANCE_API_SECRET="secret-value",
        TELEGRAM_ENABLED=True,
        TELEGRAM_BOT_TOKEN="123:telegram-secret",
        TELEGRAM_CHAT_ID="987654",
    )
    payload = DashboardRuntime(
        settings=config, account_client=FailingAccountClient()
    ).health()
    serialized = json.dumps(payload)
    assert payload["account_read_only"] is True
    assert payload["orders_enabled"] is False
    assert payload["telegram"]["commands_enabled"] is False
    for secret in ("key-value", "secret-value", "123:telegram-secret", "987654"):
        assert secret not in serialized
