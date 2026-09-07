from notifications.telegram_notifier import TelegramEventNotifier


class FakeTelegramClient:
    def __init__(self):
        self.messages = []

    def send_message(self, message):
        self.messages.append(message)
        return {"sent": True}


def snapshot(*, decision_id="d1", risk="HIGH", news_reason="Macro headline", setup="NONE"):
    news = {"news_risk": risk}
    if news_reason is not None:
        news["high_risk_reason"] = news_reason
    return {
        "decision_id": decision_id,
        "decision": {
            "timestamp": decision_id, "reason": "No active setup detected across current market state",
            "price": 60_000, "regime": "RANGE",
        },
        "strategy": {
            "setup_type": setup, "direction": "WAIT", "entry_trigger_state": "NO_SETUP",
            "trade_plan": {},
        },
        "news": news,
        "system_state": {"kill_switch": False},
        "sources": {"binance": {"status": "HEALTHY"}},
    }


def test_same_high_news_risk_twenty_cycles_sends_once_even_with_new_decision_ids():
    client = FakeTelegramClient()
    notifier = TelegramEventNotifier(client, dedupe_ttl_seconds=60)
    results = [notifier.notify_current_decision(snapshot(decision_id=f"cycle-{index}")) for index in range(20)]
    assert sum(result["sent"] for result in results) == 1
    assert sum(result["deduplicated"] for result in results) == 19
    assert len(client.messages) == 1


def test_high_to_extreme_sends_second_state_notification():
    client = FakeTelegramClient()
    notifier = TelegramEventNotifier(client)
    assert notifier.notify_current_decision(snapshot(risk="HIGH"))["sent"] is True
    assert notifier.notify_current_decision(snapshot(decision_id="d2", risk="EXTREME"))["sent"] is True
    assert len(client.messages) == 2


def test_extreme_to_high_same_reason_does_not_emit_downgrade_spam():
    client = FakeTelegramClient()
    notifier = TelegramEventNotifier(client)
    notifier.notify_current_decision(snapshot(risk="EXTREME"))
    result = notifier.notify_current_decision(snapshot(decision_id="d2", risk="HIGH"))
    assert result["deduplicated"] is True
    assert len(client.messages) == 1


def test_high_low_high_resets_and_notifies_again():
    client = FakeTelegramClient()
    notifier = TelegramEventNotifier(client)
    assert notifier.notify_current_decision(snapshot(risk="HIGH"))["sent"] is True
    low = notifier.notify_current_decision(snapshot(decision_id="low", risk="LOW"))
    assert low["sent"] is False
    assert low["reason"] == "NO_NOTIFY_EVENT"
    assert notifier.notify_current_decision(snapshot(decision_id="new-high", risk="HIGH"))["sent"] is True
    assert len(client.messages) == 2


def test_changed_news_reason_is_material_new_notification():
    client = FakeTelegramClient()
    notifier = TelegramEventNotifier(client)
    notifier.notify_current_decision(snapshot(news_reason="FOMC emergency decision"))
    result = notifier.notify_current_decision(snapshot(decision_id="d2", news_reason="Exchange security breach"))
    assert result["sent"] is True
    assert len(client.messages) == 2


def test_high_news_message_uses_news_not_setup_rejection_reason():
    client = FakeTelegramClient()
    notifier = TelegramEventNotifier(client)
    notifier.notify_current_decision(snapshot(news_reason="CPI volatility risk"))
    message = client.messages[-1]
    assert "CPI volatility risk" in message
    assert "No active setup detected" not in message


def test_high_news_without_specific_reason_uses_safe_fallback():
    client = FakeTelegramClient()
    notifier = TelegramEventNotifier(client)
    notifier.notify_current_decision(snapshot(news_reason=None))
    assert "Yeni girişler için haber riski yüksek." in client.messages[-1]


def test_no_setup_and_no_trade_do_not_notify():
    client = FakeTelegramClient()
    notifier = TelegramEventNotifier(client)
    item = snapshot(risk="LOW", setup="NONE")
    item["final_decision"] = "NO_TRADE"
    result = notifier.notify_current_decision(item)
    assert result == {"sent": False, "deduplicated": False, "event": None, "reason": "NO_NOTIFY_EVENT"}
    assert client.messages == []


def test_order_and_execution_events_remain_immediate_and_unique():
    client = FakeTelegramClient()
    notifier = TelegramEventNotifier(client)
    first = notifier.notify("ORDER_OPENED", {"side": "LONG", "entry": 60_000, "size": 0.01}, "order-1")
    second = notifier.notify("ORDER_OPENED", {"side": "LONG", "entry": 60_000, "size": 0.01}, "order-2")
    protection = notifier.notify("PROTECTION_FAILURE", {"reason": "STOP unavailable"}, "protection-1")
    assert first["sent"] and second["sent"] and protection["sent"]
    assert len(client.messages) == 3


def test_required_execution_event_family_still_sends_immediately():
    client = FakeTelegramClient()
    notifier = TelegramEventNotifier(client)
    events = (
        "ORDER_OPENED", "ORDER_CLOSED", "STOP_LOSS", "TAKE_PROFIT", "TP1", "TP2",
        "PROTECTION_FAILURE", "KILL_SWITCH", "DATA_SOURCE_ERROR",
    )
    for index, event in enumerate(events):
        result = notifier.notify(event, {"message": f"event-{index}", "side": "LONG"}, f"unique-{index}")
        assert result["sent"] is True
        assert result["deduplicated"] is False
    assert len(client.messages) == len(events)


def test_direct_high_news_notify_ignores_timestamp_style_explicit_key():
    client = FakeTelegramClient()
    notifier = TelegramEventNotifier(client)
    first = notifier.notify("HIGH_NEWS_RISK", {"news_risk": "HIGH", "news_reason": "Fed risk"}, "HIGH:d1:1")
    second = notifier.notify("HIGH_NEWS_RISK", {"news_risk": "HIGH", "news_reason": "Fed risk"}, "HIGH:d2:2")
    assert first["sent"] is True
    assert second["deduplicated"] is True
    assert len(client.messages) == 1
