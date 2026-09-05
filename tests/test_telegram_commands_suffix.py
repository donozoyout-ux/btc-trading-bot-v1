from tests.test_telegram_commands import make_service


def test_botfather_command_suffix_is_accepted():
    service, telegram = make_service()
    assert service.handle_message({"chat": {"id": 123}, "text": "/status@btc_test_bot"}) is True
    assert "BTC BOT DURUMU" in telegram.messages[-1]
