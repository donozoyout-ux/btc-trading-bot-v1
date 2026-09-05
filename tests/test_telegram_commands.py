from types import SimpleNamespace

from notifications.telegram_commands import TelegramCommandService


class FakeTelegram:
    configured = True

    def __init__(self):
        self.messages = []
        self.posts = []

    def send_message(self, text):
        self.messages.append(text)
        return {"sent": True}

    def _post(self, method, payload=None):
        self.posts.append((method, payload or {}))
        if method == "getUpdates":
            return {"ok": True, "result": []}
        return {"ok": True, "result": True}


class FakeExecution:
    def get_account_summary(self):
        return {
            "wallet_balance": 4974.31,
            "available_balance": 4900.0,
            "margin_balance": 4980.0,
            "unrealized_pnl": 5.69,
            "positions": [{"symbol": "BTCUSDT"}],
            "open_orders": [{"symbol": "BTCUSDT"}],
        }

    def get_position(self, symbol="BTCUSDT"):
        return {
            "symbol": symbol,
            "position_amt": 0.002,
            "side": "LONG",
            "entry_price": 79000.0,
            "mark_price": 80000.0,
            "unrealized_pnl": 2.0,
            "leverage": 5,
        }

    def get_open_orders(self, symbol=None):
        return []

    def get_open_algo_orders(self, symbol=None):
        return [
            {"algoId": 1, "side": "SELL", "orderType": "STOP_MARKET", "triggerPrice": "78000", "algoStatus": "NEW"},
            {"algoId": 2, "side": "SELL", "orderType": "TAKE_PROFIT_MARKET", "triggerPrice": "81000", "algoStatus": "NEW"},
            {"algoId": 3, "side": "SELL", "orderType": "TAKE_PROFIT_MARKET", "triggerPrice": "82000", "algoStatus": "NEW"},
        ]


class FakeDashboard:
    def __init__(self):
        self.binance = SimpleNamespace(status=lambda: {
            "market_data_source": "TESTNET_PUBLIC_FALLBACK",
            "production_public_status": "HTTP_451_RESTRICTED",
            "derivatives_status": "DEGRADED",
        })

    def snapshot(self, force=False):
        return {
            "final_decision": "NO_TRADE",
            "decision": {
                "price": 80000.0,
                "regime": "BULL",
                "confidence": "MEDIUM",
                "risk_status": "WAIT",
                "risk_assessment": {"position_size_btc": 0.001},
            },
            "strategy": {
                "setup_type": "NONE",
                "direction": "NONE",
                "entry_trigger_state": "WAIT",
                "eligible": False,
                "blocking_reasons": ["NO_SETUP"],
                "trade_plan": {},
            },
            "system_state": {"kill_switch": False, "daily_loss_guard": "SAFE", "loss_streak_guard": "SAFE"},
            "news": {"status": "AVAILABLE"},
            "ai_analyst": {"status": "DISABLED"},
            "derivatives": {"status": "DEGRADED"},
        }


def settings():
    return SimpleNamespace(
        TELEGRAM_BOT_TOKEN="token",
        TELEGRAM_CHAT_ID="123",
        TELEGRAM_ENABLED=True,
        BINANCE_TESTNET=True,
        BINANCE_API_KEY="key",
        BINANCE_API_SECRET="secret",
        BINANCE_RECV_WINDOW=5000,
    )


def make_service():
    telegram = FakeTelegram()
    execution = FakeExecution()
    dashboard = FakeDashboard()
    service = TelegramCommandService(
        settings(),
        dashboard_provider=lambda: dashboard,
        execution_status_provider=lambda: {
            "bot_status": "RUNNING",
            "execution_thread": "RUNNING",
            "last_execution_result": "NO_ELIGIBLE_SIGNAL",
            "smoke_test": "NOT_RUN",
            "execution_error": None,
        },
        telegram_client=telegram,
        execution_client=execution,
        sleep_fn=lambda _: None,
    )
    return service, telegram


def test_unauthorized_chat_is_ignored():
    service, telegram = make_service()
    assert service.handle_message({"chat": {"id": 999}, "text": "/status"}) is False
    assert telegram.messages == []


def test_help_lists_read_only_commands_and_no_trade_actions():
    service, telegram = make_service()
    assert service.handle_message({"chat": {"id": 123}, "text": "/help"}) is True
    text = telegram.messages[-1]
    assert "/status" in text
    assert "/position" in text
    assert "/signal" in text
    assert "BUY/SELL/CLOSE" in text


def test_status_account_position_orders_signal_risk_sources():
    service, telegram = make_service()
    for command in ("status", "account", "position", "orders", "signal", "risk", "sources", "ping"):
        assert service.handle_message({"chat": {"id": 123}, "text": f"/{command}"}) is True
    combined = "\n".join(telegram.messages)
    assert "RUNNING" in combined
    assert "4,974.31" in combined
    assert "BTCUSDT LONG" in combined
    assert "Stop: 78,000.00" in combined
    assert "TP1: 81,000.00" in combined
    assert "NO_TRADE" in combined
    assert "Kill switch: SAFE" in combined
    assert "TESTNET_PUBLIC_FALLBACK" in combined
    assert "PONG" in combined


def test_mutating_commands_are_explicitly_blocked():
    service, telegram = make_service()
    assert service.handle_message({"chat": {"id": 123}, "text": "/close"}) is True
    assert "komut kapalı" in telegram.messages[-1].lower()


def test_registers_botfather_command_menu():
    service, telegram = make_service()
    service._register_commands()
    method, payload = telegram.posts[-1]
    assert method == "setMyCommands"
    commands = {row["command"] for row in payload["commands"]}
    assert {"help", "status", "account", "position", "orders", "signal", "risk", "sources", "ping"}.issubset(commands)
    assert "close" not in commands
