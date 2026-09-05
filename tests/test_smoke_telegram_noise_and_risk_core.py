from pathlib import Path
from types import SimpleNamespace

from config.settings import BotSettings
from core.state import BotState
from execution.testnet_runtime import TestnetExecutionRuntime


class FakeExecutionClient:
    testnet = True
    configured = True

    def __init__(self):
        self.position = {"symbol": "BTCUSDT", "position_amt": 0.0, "side": "FLAT", "entry_price": 80000.0, "mark_price": 80000.0, "unrealized_pnl": 0.0, "leverage": 1}
        self.market_calls = []

    def get_server_time(self):
        return 123456789

    def get_account_summary(self):
        return {"wallet_balance": 1000.0, "available_balance": 1000.0, "margin_balance": 1000.0, "unrealized_pnl": 0.0, "positions": [], "open_orders": []}

    def get_position(self, symbol="BTCUSDT"):
        return dict(self.position)

    def get_positions(self, symbol=None):
        return [] if float(self.position.get("position_amt") or 0) == 0 else [{"symbol": "BTCUSDT", "positionAmt": self.position["position_amt"]}]

    def get_open_orders(self, symbol=None):
        return []

    def get_open_algo_orders(self, symbol=None):
        return []

    def cancel_all_algo_open_orders(self, symbol):
        return {"code": 200}

    def normalize_quantity(self, symbol, quantity, **kwargs):
        return quantity

    def place_market_order(self, symbol, side, quantity, *, reduce_only=False, client_order_id=None):
        self.market_calls.append({"side": side, "quantity": quantity, "reduce_only": reduce_only})
        if reduce_only:
            self.position.update(position_amt=0.0, side="FLAT")
        else:
            self.position.update(position_amt=quantity, side="LONG")
        return {
            "client_order_id": client_order_id,
            "binance_order_id": len(self.market_calls),
            "symbol": symbol,
            "side": side,
            "type": "MARKET",
            "requested_quantity": quantity,
            "executed_quantity": quantity,
            "average_fill_price": 80000.0,
            "status": "FILLED",
            "reduce_only": reduce_only,
            "timestamp": 1,
        }

    def close_position_market(self, symbol="BTCUSDT"):
        amount = abs(float(self.position.get("position_amt") or 0))
        if not amount:
            return None
        return self.place_market_order(symbol, "SELL", amount, reduce_only=True)


def settings(tmp_path):
    return BotSettings(
        _env_file=None,
        ENV="testnet",
        BINANCE_TESTNET=True,
        BINANCE_API_KEY="key",
        BINANCE_API_SECRET="secret",
        ACCOUNT_READ_ONLY=False,
        ORDER_SUBMISSION_ENABLED=True,
        SHADOW_MODE=False,
        RUN_EXECUTION_SMOKE_TEST=True,
        JOURNAL_DIR=str(tmp_path),
        TELEGRAM_ENABLED=False,
    )


def test_smoke_emits_only_summary_not_fake_trade_open_close(tmp_path):
    dashboard = SimpleNamespace(
        binance=SimpleNamespace(get_mark_price=lambda symbol: 80000.0),
        state=BotState(),
    )
    runtime = TestnetExecutionRuntime(settings(tmp_path), client=FakeExecutionClient(), dashboard_runtime=dashboard, sleep_fn=lambda _: None)
    events = []
    runtime.executor._notify = lambda event, payload, dedupe_key=None: events.append(event) or {"sent": True}

    result = runtime.run_smoke_test()

    assert result["status"] == "PASS"
    assert "ORDER_OPENED" not in events
    assert "ORDER_CLOSED" not in events
    assert events.count("SMOKE_TEST_PASS") == 1


def test_dashboard_risk_core_explains_no_setup_state():
    source = Path("dashboard/dashboard-tabs.js").read_text(encoding="utf-8")
    assert "syncRiskCore" in source
    assert "NO SETUP" in source
