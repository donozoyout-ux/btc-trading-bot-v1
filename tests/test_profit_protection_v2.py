from types import SimpleNamespace

import pytest

from config.constants import MarketRegime, PositionManagementState, StructureType, TradeDirection
from core.state import BotState
from engines.position_manager import PositionManager
from execution.safer_testnet_executor import SaferTestnetExecutor


class MemoryJournal:
    def __init__(self, state=None):
        self.state = dict(state or {})
        self.records = []
    def read_state(self): return dict(self.state)
    def write_state(self, value): self.state = dict(value)
    def record(self, **value): self.records.append(value); return value


class Notifier:
    def __init__(self): self.events = []
    def notify(self, event, payload, dedupe_key=None):
        self.events.append((event, payload, dedupe_key))
        return {"sent": True, "event": event}


class ProtectionClient:
    testnet = True
    configured = True
    def __init__(self):
        self.position = {"symbol": "BTCUSDT", "position_amt": .5, "side": "LONG", "entry_price": 100, "mark_price": 105}
        self.orders = [
            {"algoId": 1, "orderType": "STOP_MARKET", "side": "SELL", "triggerPrice": "90", "quantity": ".5", "reduceOnly": True},
        ]
        self.next_id = 2
        self.calls = []
    def get_position(self, _symbol="BTCUSDT"): return dict(self.position)
    def get_positions(self): return [dict(self.position)]
    def get_open_orders(self, _symbol=None): return []
    def get_open_algo_orders(self, _symbol=None): return [dict(row) for row in self.orders]
    def normalize_quantity(self, _symbol, quantity, **_kwargs): return round(float(quantity), 3)
    def normalize_price(self, _symbol, price): return int(float(price) * 10) / 10
    def price_tick_size(self, _symbol): return .1
    def get_mark_price(self, _symbol="BTCUSDT"): return float(self.position["mark_price"])
    def place_protective_order(self, _symbol, side, order_type, quantity, stop_price):
        self.calls.append(("place", order_type, quantity, stop_price))
        self.next_id = max([row["algoId"] for row in self.orders] + [self.next_id - 1]) + 1
        row = {"algoId": self.next_id, "orderType": order_type, "side": side, "triggerPrice": str(stop_price), "quantity": str(quantity), "reduceOnly": True}
        self.next_id += 1
        self.orders.append(row)
        return {"binance_order_id": row["algoId"], "type": order_type, "side": side, "trigger_price": stop_price, "requested_quantity": quantity, "reduce_only": True}
    def cancel_algo_order(self, *, algo_id): self.orders = [row for row in self.orders if row["algoId"] != algo_id]
    def reduce_position_market(self, _symbol, quantity):
        self.position["position_amt"] = float(self.position["position_amt"]) - float(quantity)
        return {"status": "FILLED", "average_fill_price": self.position["mark_price"], "executed_quantity": quantity}


def settings():
    return SimpleNamespace(testnet_execution_enabled=False, BINANCE_TESTNET=True, ENV="testnet", ORDER_SUBMISSION_ENABLED=False,
                           ACCOUNT_READ_ONLY=True, SHADOW_MODE=True, PROFIT_PROTECTION_ENABLED=True)


def context():
    return {"exchange_baseline_verified": True, "actual_entry_price": 100, "actual_initial_position_size": .5,
            "actual_initial_stop": 90, "entry_decision_id": "D1", "entry_opened_at": 1, "tp2": 120,
            "management_profile": "BALANCED"}


def manager():
    return PositionManager(profit_protection_enabled=True, breakeven_trigger_r=.75,
                           profit_lock_1_trigger_r=1, profit_lock_1_r=.2,
                           profit_lock_2_trigger_r=1.5, profit_lock_2_r=.6,
                           profit_lock_3_trigger_r=2, profit_lock_3_r=1.1)


def decision(direction=TradeDirection.LONG, mark=105, **changes):
    payload = dict(direction=direction, entry=100, initial_stop=90 if direction == TradeDirection.LONG else 110,
                   current_stop=90 if direction == TradeDirection.LONG else 110, mark=mark,
                   initial_size=1, current_size=1, structure=StructureType.MIXED, last_bos=None, last_choch=None,
                   regime=MarketRegime.BULL if direction == TradeDirection.LONG else MarketRegime.BEAR,
                   momentum_support=True, momentum_opposing=False, momentum_available=True,
                   volume_support=True, volume_available=True, candle_closed=True, candle_timestamp=300_000,
                   mfe_r=.5, mae_r=0)
    payload.update(changes)
    return manager().evaluate(**payload)


def test_target_only_missing_is_degraded_then_self_heals_without_kill_switch():
    client, journal, notifier, state = ProtectionClient(), MemoryJournal(), Notifier(), BotState()
    executor = SaferTestnetExecutor(client, settings=settings(), execution_journal=journal, event_notifier=notifier)
    executor._entry_context = context()
    result = executor.manage_existing_position(state)
    assert result["status"] == "POSITION_MANAGEMENT"
    assert any(row["action"] == "TARGET_PROTECTION_MISSING" for row in journal.records)
    assert any(row["action"] == "TARGET_PROTECTION_REPAIRED" for row in journal.records)
    assert not state.emergency_latch_active
    assert not [event for event, _, _ in notifier.events if event == "KILL_SWITCH"]


def test_failed_target_repair_keeps_stop_and_blocks_entries():
    client, journal = ProtectionClient(), MemoryJournal()
    executor = SaferTestnetExecutor(client, settings=settings(), execution_journal=journal)
    executor._entry_context = {**context(), "tp2": None}
    result = executor.manage_existing_position(BotState())
    assert result["status"] == "PROTECTION_DEGRADED_TARGET_MISSING"
    assert executor.protection_reconciliation_required
    assert any(row["orderType"] == "STOP_MARKET" for row in client.orders)
    assert executor.process_snapshot({}, BotState())["status"] == "PROTECTION_RECONCILIATION_REQUIRED"


def test_missing_stop_alert_is_persisted_deduped_and_rearms_after_recovery():
    client, journal, notifier, state = ProtectionClient(), MemoryJournal(), Notifier(), BotState()
    client.orders = []
    executor = SaferTestnetExecutor(client, settings=settings(), execution_journal=journal, event_notifier=notifier)
    executor._entry_context = context()
    for _ in range(20): executor.manage_existing_position(state)
    assert len([event for event, _, _ in notifier.events if event == "KILL_SWITCH"]) == 1
    restarted = SaferTestnetExecutor(client, settings=settings(), execution_journal=journal, event_notifier=notifier)
    restarted._entry_context = context()
    restarted.manage_existing_position(state)
    assert len([event for event, _, _ in notifier.events if event == "KILL_SWITCH"]) == 1
    client.orders = [
        {"algoId": 10, "orderType": "STOP_MARKET", "side": "SELL", "triggerPrice": "90", "quantity": ".5", "reduceOnly": True},
        {"algoId": 11, "orderType": "TAKE_PROFIT_MARKET", "side": "SELL", "triggerPrice": "120", "quantity": ".5", "reduceOnly": True},
    ]
    restarted.manage_existing_position(state)
    assert len([event for event, _, _ in notifier.events if event == "PROTECTION_RECOVERED"]) == 1
    client.orders = [row for row in client.orders if row["orderType"] != "STOP_MARKET"]
    restarted.manage_existing_position(state)
    assert len([event for event, _, _ in notifier.events if event == "KILL_SWITCH"]) == 2


def test_profit_v2_does_not_act_before_arm_and_calculates_long_short_r():
    early = decision(mark=105, mfe_r=.5)
    long = decision(mark=108, mfe_r=1.2, momentum_support=False, momentum_opposing=True)
    short = decision(TradeDirection.SHORT, 92, mfe_r=1.2, momentum_support=False, momentum_opposing=True)
    assert early.state not in {PositionManagementState.PROFIT_PROTECT, PositionManagementState.PROFIT_TRAIL}
    assert long.current_r == short.current_r == .8
    assert long.profit_giveback_r == short.profit_giveback_r == .4


@pytest.mark.parametrize(
    "direction,mark,current_stop",
    [(TradeDirection.LONG, 104, 90), (TradeDirection.SHORT, 96, 110)],
)
def test_historical_1_5r_lock_crossed_cannot_create_impossible_stop(direction, mark, current_stop):
    result = decision(direction, mark, mfe_r=1.5, current_stop=current_stop)
    assert result.stop_action == {}
    assert "HISTORICAL_PROFIT_LOCK_ALREADY_CROSSED" in result.reason_codes
    assert result.protected_r == -1.0


@pytest.mark.parametrize("amount,mark,candidate", [(0.5, 104, 104), (-0.5, 96, 96)])
def test_executor_rejects_cross_mark_stop_before_any_order_and_preserves_old(amount, mark, candidate):
    client = ProtectionClient()
    client.position.update({"position_amt": amount, "side": "LONG" if amount > 0 else "SHORT", "mark_price": mark})
    close_side = "SELL" if amount > 0 else "BUY"
    old_stop = 90 if amount > 0 else 110
    client.orders = [
        {"algoId": 1, "orderType": "STOP_MARKET", "side": close_side, "triggerPrice": str(old_stop), "quantity": ".5", "reduceOnly": True},
        {"algoId": 2, "orderType": "TAKE_PROFIT_MARKET", "side": close_side, "triggerPrice": "120" if amount > 0 else "80", "quantity": ".5", "reduceOnly": True},
    ]
    executor = SaferTestnetExecutor(client, settings=settings(), execution_journal=MemoryJournal())
    before = list(client.orders)
    with pytest.raises(Exception, match="STOP_TRIGGER_WOULD_IMMEDIATELY_FIRE"):
        executor._replace_stop_safely(client.position, candidate)
    assert client.calls == []
    assert client.orders == before


def test_profit_fade_protects_and_blocks_target_extension():
    faded = decision(mark=108, mfe_r=1.2, momentum_support=False, momentum_opposing=True,
                     current_tp2=120, candidate_tp2=130)
    assert faded.state == PositionManagementState.PROFIT_PROTECT
    assert faded.target_action["action"] == "CLOSE_PARTIAL"
    assert "TARGET_EXTENSION_BLOCKED_PROFIT_FADE" in faded.reason_codes
    assert faded.stop_action["new_stop"] >= 100


def test_strong_continuation_keeps_runner_and_stop_never_widens():
    runner = decision(mark=115, mfe_r=1.5, regime=MarketRegime.STRONG_BULL,
                      current_stop=105, current_tp2=120, candidate_tp2=130)
    assert runner.state == PositionManagementState.PROFIT_TRAIL
    assert runner.stop_action["new_stop"] >= 105
    assert runner.target_action["new_tp2"] == 130


def test_deterioration_after_1_5r_tightens_and_confirmed_second_fade_exits():
    protect = decision(mark=110, mfe_r=1.5, momentum_support=False, momentum_opposing=True,
                       volume_support=False, profit_fade_partial_taken=False)
    exit_fade = decision(mark=109, mfe_r=1.5, momentum_support=False, momentum_opposing=True,
                         volume_support=False, profit_fade_partial_taken=False)
    assert protect.stop_action["new_stop"] >= 106
    assert exit_fade.state == PositionManagementState.EXIT_PROFIT_FADE


def test_profit_partial_reconciles_exact_stop_and_target_quantities():
    client, journal = ProtectionClient(), MemoryJournal()
    client.orders.append({"algoId": 2, "orderType": "TAKE_PROFIT_MARKET", "side": "SELL", "triggerPrice": "120", "quantity": ".5", "reduceOnly": True})
    executor = SaferTestnetExecutor(client, settings=settings(), execution_journal=journal)
    executor._entry_context = context()
    partial = executor._take_partial_safely(client.position, .1)
    executor._reconcile_profit_partial_protection(partial["position"])
    orders = client.get_open_algo_orders()
    stop = next(row for row in orders if row["orderType"] == "STOP_MARKET")
    targets = [row for row in orders if row["orderType"] == "TAKE_PROFIT_MARKET"]
    assert float(stop["quantity"]) == .4
    assert sum(float(row["quantity"]) for row in targets) <= .4


def test_restart_preserves_mfe_partial_and_alert_state():
    journal = MemoryJournal()
    first = SaferTestnetExecutor(ProtectionClient(), settings=settings(), execution_journal=journal)
    first.management_mfe_r = 1.35
    first.profit_fade_partial_taken = True
    first.active_alert_state = "UNPROTECTED"
    first.last_alert_reason = "UNPROTECTED_TESTNET_POSITION"
    first._write_runtime_state()
    restarted = SaferTestnetExecutor(ProtectionClient(), settings=settings(), execution_journal=journal)
    assert restarted.management_mfe_r == 1.35
    assert restarted.profit_fade_partial_taken is True
    assert restarted.active_alert_state == "UNPROTECTED"


def test_management_telegram_is_state_based_not_candle_based():
    notifier = Notifier()
    executor = SaferTestnetExecutor(ProtectionClient(), settings=settings(), execution_journal=MemoryJournal(), event_notifier=notifier)
    payload = {"state": "PROFIT_TRAIL", "reason_codes": ["PROFIT_RUNNER"], "timestamp": 1, "target_action": {}, "stop_action": {}}
    executor._notify_management_transition("PROFIT_RUNNER", payload)
    executor._notify_management_transition("PROFIT_RUNNER", {**payload, "timestamp": 2})
    assert [event for event, _, _ in notifier.events] == ["PROFIT_RUNNER"]


def test_open_5m_candle_cannot_change_mfe_or_management_timestamp():
    client = ProtectionClient()
    client.orders.append({"algoId": 2, "orderType": "TAKE_PROFIT_MARKET", "side": "SELL", "triggerPrice": "120", "quantity": ".5", "reduceOnly": True})
    executor = SaferTestnetExecutor(client, settings=settings(), execution_journal=MemoryJournal())
    executor._entry_context = context()
    result = executor.manage_adaptive_position({"candles": {"5m": [{"time": 1, "high": 130, "low": 70, "close": 115, "is_closed": False}]}}, BotState(), client.position)
    assert result["status"] == "MANAGEMENT_NO_CHANGE"
    assert executor.management_mfe_r == 0
    assert executor.management_mae_r == 0
    assert executor.last_management_closed_5m_timestamp is None


def _extrema_snapshot(candle, *, mark, direction):
    return {
        "candles": {"5m": [candle]},
        "market": {"mark_price": mark},
        "sources": {"binance": {"status": "OFFLINE", "market_data_trading_safe": False}},
        "decision": {"regime": "BULL" if direction == "LONG" else "BEAR", "volatility": "NORMAL"},
        "chart_intelligence": {"timeframes": {"5m": {"status": "UNAVAILABLE", "closed_candles": 1, "structure": "MIXED"}}},
        "zones": [],
    }


@pytest.mark.parametrize(
    "amount,candle,expected_mfe,expected_mae",
    [
        (.5, {"time": 2, "open": 100, "high": 113, "low": 95, "close": 107, "is_closed": True}, 1.3, -.5),
        (-.5, {"time": 2, "open": 100, "high": 105, "low": 87, "close": 93, "is_closed": True}, 1.3, -.5),
    ],
)
def test_closed_candle_extrema_update_mfe_and_mae_for_both_directions(amount, candle, expected_mfe, expected_mae):
    client = ProtectionClient()
    direction = "LONG" if amount > 0 else "SHORT"
    client.position.update({"position_amt": amount, "side": direction, "mark_price": candle["close"]})
    close_side = "SELL" if amount > 0 else "BUY"
    client.orders = [
        {"algoId": 1, "orderType": "STOP_MARKET", "side": close_side, "triggerPrice": "90" if amount > 0 else "110", "quantity": ".5", "reduceOnly": True},
        {"algoId": 2, "orderType": "TAKE_PROFIT_MARKET", "side": close_side, "triggerPrice": "120" if amount > 0 else "80", "quantity": ".5", "reduceOnly": True},
    ]
    executor = SaferTestnetExecutor(client, settings=settings(), execution_journal=MemoryJournal())
    executor._entry_context = context() | {"direction": direction, "actual_initial_stop": 90 if amount > 0 else 110}
    executor.manage_adaptive_position(_extrema_snapshot(candle, mark=candle["close"], direction=direction), BotState(), client.position)
    assert executor.management_mfe_r == pytest.approx(expected_mfe)
    assert executor.management_mae_r == pytest.approx(expected_mae)


def test_closed_high_then_lower_current_mark_produces_giveback_and_valid_protected_r():
    client = ProtectionClient()
    client.position["mark_price"] = 107
    client.orders.append({"algoId": 2, "orderType": "TAKE_PROFIT_MARKET", "side": "SELL", "triggerPrice": "120", "quantity": ".5", "reduceOnly": True})
    executor = SaferTestnetExecutor(client, settings=settings(), execution_journal=MemoryJournal())
    executor._entry_context = context()
    candle = {"time": 3, "open": 100, "high": 113, "low": 99, "close": 107, "is_closed": True}
    snapshot = _extrema_snapshot(candle, mark=107, direction="LONG")
    snapshot["sources"]["binance"] = {"status": "HEALTHY", "market_data_trading_safe": True}
    snapshot["chart_intelligence"]["timeframes"]["5m"].update({"status": "AVAILABLE", "closed_candles": 30, "trend": "UP", "volume_state": "NORMAL"})
    result = executor.manage_adaptive_position(snapshot, BotState(), client.position)
    intel = result["position_intelligence"]
    assert intel["mfe_r"] == pytest.approx(1.3)
    assert intel["current_r"] == pytest.approx(.7)
    assert intel["profit_giveback_r"] == pytest.approx(.6)
    assert intel["protected_r"] == pytest.approx(.2)
    assert intel["new_stop"] == pytest.approx(102)
    assert intel["new_stop"] < 107


def test_partial_quantity_cannot_equal_or_exceed_remaining():
    executor = SaferTestnetExecutor(ProtectionClient(), settings=settings(), execution_journal=MemoryJournal())
    with pytest.raises(Exception, match="INVALID_REDUCE_ONLY_QUANTITY"):
        executor._take_partial_safely(executor.client.position, .5)


def test_v2_settings_are_initial_hypotheses_and_backtest_mode_isolated():
    from backtest.simulator import BacktestSimulator
    from config.settings import BotSettings
    configured = BotSettings(_env_file=None)
    v1 = BacktestSimulator(configured, management_mode=BacktestSimulator.ADAPTIVE_MANAGEMENT_V1)
    v2 = BacktestSimulator(configured, management_mode=BacktestSimulator.PROFIT_PROTECTION_V2)
    assert v1.position_manager.profit_protection_enabled is False
    assert v2.position_manager.profit_protection_enabled is True
    assert configured.PROFIT_ARM_R == .60
