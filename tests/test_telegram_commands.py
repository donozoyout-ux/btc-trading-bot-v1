from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

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
    testnet = True

    def __init__(self):
        self.position = {
            "symbol": "BTCUSDT", "position_amt": 0.002, "side": "LONG",
            "entry_price": 79000.0, "mark_price": 80000.0,
            "unrealized_pnl": 2.0, "leverage": 5,
        }
        self.closed = 0
        self.cancelled_algo = 0

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
        return dict(self.position, symbol=symbol)

    def close_position_market(self, symbol="BTCUSDT"):
        self.closed += 1
        qty = abs(float(self.position["position_amt"]))
        self.position["position_amt"] = 0.0
        self.position["side"] = "FLAT"
        return {"status": "FILLED", "executed_quantity": qty, "average_fill_price": 80010.0}

    def cancel_all_algo_open_orders(self, symbol):
        self.cancelled_algo += 1
        return {"status": "OK"}

    def cancel_order(self, symbol, order_id):
        return {"status": "CANCELED"}

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
        self.account_payload = {
            "connected": True,
            "wallet_balance_usdt": 4974.31,
            "daily_performance": {
                "status": "AVAILABLE", "net_pnl_usdt": 12.50,
                "realized_pnl_usdt": 14.0, "commission_usdt": -1.25, "funding_usdt": -0.25,
            },
            "daily_trade_ledger": {
                "status": "AVAILABLE", "opened_trades_today": 2,
                "closed_trades_today": 1, "winning_trades_today": 1, "losing_trades_today": 0,
            },
        }
        self.binance = SimpleNamespace(status=lambda: {
            "market_data_source": "TESTNET_PUBLIC_FALLBACK",
            "production_public_status": "HTTP_451_RESTRICTED",
            "derivatives_status": "DEGRADED",
        })

    def account(self, force=False):
        return self.account_payload

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
            "derivatives": {
                "status": "DEGRADED",
                "open_interest": {"value": 12345.0, "source": "BINANCE"},
                "funding_rate": {"value": 0.0001, "source": "BINANCE"},
                "long_short_ratio": {"value": 1.02, "source": "BINANCE"},
                "taker_buy_ratio": {"value": 1.1, "source": "BINANCE"},
                "liquidations_24h": {"value": None, "source": "UNAVAILABLE"},
            },
            "sources": {"coinglass": {"status": "AUTH_ERROR"}, "coinmarketcap": {"status": "HEALTHY"}},
            "macro_context": {"btc_dominance": 56.2, "total_market_cap_usd": 3000000000000, "total_volume_24h_usd": 90000000000},
            "active_trade": {
                "status": "ACTIVE", "side": "LONG", "entry_price": 79000.0,
                "mark_price": 80000.0, "unrealized_pnl": 2.0,
                "stop_price": 78000.0, "tp1_price": 81000.0, "current_r": 0.5,
            },
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
        JOURNAL_DIR="journal_logs",
        TELEGRAM_DAILY_REPORT_ENABLED=True,
        TELEGRAM_DAILY_REPORT_HOUR=23,
        TELEGRAM_DAILY_REPORT_MINUTE=55,
        TELEGRAM_MANUAL_TRADING_ENABLED=True,
        ENV="testnet",
        ORDER_SUBMISSION_ENABLED=True,
        ACCOUNT_READ_ONLY=False,
        SHADOW_MODE=False,
    )


class MemoryOperatorState:
    def __init__(self): self.state = {"manual_entry_lock": False}
    def read(self): return dict(self.state)
    def lock_entries(self, *, locked_by, reason):
        self.state = {"manual_entry_lock": True, "locked_by": locked_by, "reason": reason}
        return dict(self.state)
    def unlock_entries(self, *, unlocked_by):
        self.state = {"manual_entry_lock": False, "locked_by": unlocked_by, "reason": "OPERATOR_RESUMED_AUTO_ENTRIES"}
        return dict(self.state)


class MemoryReportState:
    def __init__(self): self.data = {}
    def load(self, key): return dict(self.data.get(key) or {})
    def save(self, key, value): self.data[key] = dict(value)


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
        daily_report_state=MemoryReportState(),
        operator_control_state=MemoryOperatorState(),
        sleep_fn=lambda _: None,
    )
    return service, telegram


def test_unauthorized_chat_is_ignored():
    service, telegram = make_service()
    assert service.handle_message({"chat": {"id": 999}, "text": "/status"}) is False
    assert telegram.messages == []


def test_help_lists_testnet_manual_close_and_no_manual_open():
    service, telegram = make_service()
    assert service.handle_message({"chat": {"id": 123}, "text": "/help"}) is True
    text = telegram.messages[-1]
    assert "/durum" in text
    assert "/pozisyon" in text
    assert "/sinyal" in text
    assert "/rapor" in text
    assert "/sat" in text
    assert "/kapat" in text
    assert "/manuel" in text
    assert "/devam" in text
    assert "pozisyon AÇMAZ" in text


def test_status_account_position_orders_signal_risk_sources():
    service, telegram = make_service()
    for command in ("durum", "hesap", "pozisyon", "emirler", "sinyal", "risk", "kaynaklar", "piyasa", "ping"):
        assert service.handle_message({"chat": {"id": 123}, "text": f"/{command}"}) is True
    combined = "\n".join(telegram.messages)
    assert "RUNNING" in combined
    assert "4,974.31" in combined
    assert "BTCUSDT LONG" in combined
    assert "Stop: 78,000.00" in combined
    assert "TP1: 81,000.00" in combined
    assert "NO_TRADE" in combined
    assert "Acil durdurma: GÜVENLİ" in combined
    assert "Binance: FALLBACK" in combined
    assert "CoinGlass: AUTH_ERROR" in combined
    assert "CoinMarketCap: CONNECTED" in combined
    assert "Türev verileri: DEGRADED" in combined
    assert "BTC dominansı: 56.20%" in combined
    assert "CoinGlass likidasyon: $VERİ YOK" in combined
    assert "Salt okunur" in combined
    assert "PONG" in combined


def test_opening_mutating_commands_remain_blocked():
    service, telegram = make_service()
    assert service.handle_message({"chat": {"id": 123}, "text": "/buy"}) is True
    assert "komut kapalı" in telegram.messages[-1].lower()


def test_manual_sat_closes_testnet_position_and_locks_auto_entries():
    service, telegram = make_service()
    assert service.handle_message({"chat": {"id": 123}, "text": "/sat"}) is True
    assert "POZİSYON KAPATILDI" in telegram.messages[-1]
    assert service.execution.closed == 1
    assert service.execution.get_position()["position_amt"] == 0
    assert service.operator_control.read()["manual_entry_lock"] is True


def test_plain_sat_is_an_exact_authorized_operator_command():
    service, telegram = make_service()
    assert service.handle_message({"chat": {"id": 123}, "text": "sat"}) is True
    assert service.execution.closed == 1
    assert "POZİSYON KAPATILDI" in telegram.messages[-1]


def test_manuel_and_devam_toggle_auto_entry_lock_without_orders():
    service, telegram = make_service()
    assert service.handle_message({"chat": {"id": 123}, "text": "/manuel"}) is True
    assert service.operator_control.read()["manual_entry_lock"] is True
    assert service.execution.closed == 0
    assert service.handle_message({"chat": {"id": 123}, "text": "/devam"}) is True
    assert service.operator_control.read()["manual_entry_lock"] is False
    assert service.execution.closed == 0


def test_registers_botfather_command_menu():
    service, telegram = make_service()
    service._register_commands()
    method, payload = telegram.posts[-1]
    assert method == "setMyCommands"
    commands = {row["command"] for row in payload["commands"]}
    assert {"yardim", "durum", "hesap", "pozisyon", "emirler", "sinyal", "risk", "kaynaklar", "piyasa", "rapor", "manuel", "devam", "sat", "kapat", "ping"}.issubset(commands)
    assert "buy" not in commands


def test_manual_daily_report_is_turkish_and_uses_authoritative_daily_fields():
    service, telegram = make_service()
    assert service.handle_message({"chat": {"id": 123}, "text": "/rapor"}) is True
    message = telegram.messages[-1]
    assert "GÜNLÜK BTC RAPORU" in message
    assert "Bugünkü net: +12.50 USDT" in message
    assert "Gerçekleşen K/Z: +14.00 USDT" in message
    assert "Komisyon: -1.25 USDT" in message
    assert "Bugün açılan işlem: 2" in message
    assert "Kazanan: 1" in message
    assert "AÇIK POZİSYON" in message
    assert "Mevcut R: +0.50R" in message


def test_scheduled_daily_report_sends_once_per_istanbul_day():
    service, telegram = make_service()
    tz = ZoneInfo("Europe/Istanbul")
    before = datetime(2026, 9, 11, 23, 54, tzinfo=tz)
    due = datetime(2026, 9, 11, 23, 55, tzinfo=tz)
    assert service.maybe_send_daily_report(before) is False
    assert service.maybe_send_daily_report(due) is True
    assert service.maybe_send_daily_report(datetime(2026, 9, 11, 23, 59, tzinfo=tz)) is False
    assert len(telegram.messages) == 1
    assert service.maybe_send_daily_report(datetime(2026, 9, 12, 23, 55, tzinfo=tz)) is True
    assert len(telegram.messages) == 2


def test_english_command_aliases_remain_compatible():
    service, telegram = make_service()
    assert service.handle_message({"chat": {"id": 123}, "text": "/status"}) is True
    assert "BTC BOT DURUMU" in telegram.messages[-1]
    assert service.handle_message({"chat": {"id": 123}, "text": "/daily"}) is True
    assert "GÜNLÜK BTC RAPORU" in telegram.messages[-1]
