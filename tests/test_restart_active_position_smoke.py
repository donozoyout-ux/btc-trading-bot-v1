from types import SimpleNamespace

from config.settings import BotSettings
from core.state import BotState
from execution.testnet_runtime import TestnetExecutionRuntime


class ActivePositionClient:
    testnet = True
    configured = True

    def __init__(self):
        self.position = {
            "symbol": "BTCUSDT",
            "position_amt": 0.001,
            "side": "LONG",
            "entry_price": 80000.0,
            "mark_price": 80100.0,
            "unrealized_pnl": 0.1,
            "leverage": 1,
        }
        self.market_calls = 0
        self.close_calls = 0
        self.algo_orders = [
            {
                "algoId": 101,
                "clientAlgoId": "sl-101",
                "orderType": "STOP_MARKET",
                "algoStatus": "NEW",
                "triggerPrice": "79000",
                "side": "SELL",
                "quantity": "0.001",
                "reduceOnly": True,
            },
            {
                "algoId": 102,
                "clientAlgoId": "tp-102",
                "orderType": "TAKE_PROFIT_MARKET",
                "algoStatus": "NEW",
                "triggerPrice": "81000",
                "side": "SELL",
                "quantity": "0.001",
                "reduceOnly": True,
            },
        ]

    def get_server_time(self):
        return 123456789

    def get_account_summary(self):
        return {"wallet_balance": 1000.0, "positions": [dict(self.position)]}

    def get_position(self, symbol="BTCUSDT"):
        return dict(self.position)

    def get_open_orders(self, symbol=None):
        return []

    def get_open_algo_orders(self, symbol=None):
        return list(self.algo_orders)

    def place_market_order(self, *args, **kwargs):
        self.market_calls += 1
        raise AssertionError("startup smoke must not place a market order while a position is active")

    def close_position_market(self, *args, **kwargs):
        self.close_calls += 1
        raise AssertionError("restart recovery must not flatten an active TESTNET position")


def test_restart_with_active_position_skips_smoke_and_enters_management(tmp_path):
    settings = BotSettings(
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
    snapshot_calls = []
    dashboard = SimpleNamespace(
        state=BotState(),
        binance=SimpleNamespace(get_mark_price=lambda symbol: 80000.0),
        snapshot=lambda force=False: snapshot_calls.append(force),
    )
    client = ActivePositionClient()
    runtime = TestnetExecutionRuntime(
        settings,
        client=client,
        dashboard_runtime=dashboard,
        sleep_fn=lambda _: None,
    )

    runtime.run_loop(max_cycles=1)

    assert client.market_calls == 0
    assert client.close_calls == 0
    assert snapshot_calls == []
    state = runtime.executor.execution_journal.read_state()
    assert state["smoke_test"] == "SKIPPED_ACTIVE_POSITION"
    assert state["last_execution_result"] == "RECOVERED_POSITION_CONTEXT_UNAVAILABLE"
    assert state["position_intelligence"]["reason_codes"] == ["RECOVERED_POSITION_CONTEXT_UNAVAILABLE"]
