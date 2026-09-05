from pathlib import Path

from notifications.telegram_notifier import TelegramEventNotifier


class FakeTelegramClient:
    def __init__(self):
        self.messages = []

    def send_message(self, text):
        self.messages.append(text)
        return {"sent": True}


def test_dashboard_uses_single_console_without_hidden_tab_navigation():
    source = Path("dashboard/dashboard-tabs.js").read_text(encoding="utf-8")
    assert "dashboard-board" in source
    assert "grid-template-columns:repeat(12" in source
    assert "section.hidden = false" in source
    assert "dashboard-tabs').forEach(el => el.remove()" in source
    assert "sessionStorage.setItem('btc-dashboard-tab'" not in source


def test_order_opened_telegram_is_compact_turkish_testnet_message():
    client = FakeTelegramClient()
    notifier = TelegramEventNotifier(client)
    result = notifier.notify(
        "ORDER_OPENED",
        {
            "side": "LONG",
            "entry": 61234.5,
            "size": 0.00125,
            "stop": 60700,
            "tp1": 62000,
            "tp2": 62800,
        },
        dedupe_key="order-1",
    )
    assert result["sent"] is True
    message = client.messages[-1]
    assert "BTCUSDT LONG AÇILDI" in message
    assert "Giriş:" in message
    assert "Stop:" in message
    assert "TP1:" in message and "TP2:" in message
    assert "MODE: BINANCE FUTURES TESTNET" in message
    assert "REAL MONEY: NO" in message


def test_smoke_pass_message_has_only_actionable_status():
    client = FakeTelegramClient()
    notifier = TelegramEventNotifier(client)
    notifier.notify("SMOKE_TEST_PASS", {"message": "ignored verbose detail"}, dedupe_key="smoke")
    message = client.messages[-1]
    assert "SMOKE TEST BAŞARILI" in message
    assert "Test BUY: PASS" in message
    assert "Test CLOSE: PASS" in message
    assert "Final pozisyon: FLAT" in message


def test_degraded_market_data_does_not_spam_data_source_error():
    client = FakeTelegramClient()
    notifier = TelegramEventNotifier(client)
    snapshot = {
        "decision": {"price": 60000},
        "strategy": {"setup_type": "NONE", "entry_trigger_state": "WAIT"},
        "news": {"news_risk": "LOW"},
        "system_state": {"kill_switch": False},
        "sources": {"binance": {"status": "DEGRADED"}},
    }
    result = notifier.notify_current_decision(snapshot)
    assert result["sent"] is False
    assert result["reason"] == "NO_NOTIFY_EVENT"
    assert client.messages == []


def test_setup_notification_waits_for_entry_ready():
    client = FakeTelegramClient()
    notifier = TelegramEventNotifier(client)
    snapshot = {
        "decision_id": "d1",
        "decision": {"price": 60000, "regime": "BULL"},
        "strategy": {
            "setup_type": "TREND_PULLBACK",
            "direction": "LONG",
            "entry_trigger_state": "WAIT_TRIGGER",
            "trade_plan": {},
        },
        "news": {"news_risk": "LOW"},
        "system_state": {"kill_switch": False},
        "sources": {"binance": {"status": "HEALTHY"}},
    }
    result = notifier.notify_current_decision(snapshot)
    assert result["sent"] is False
    assert client.messages == []
