from pathlib import Path
from types import SimpleNamespace

import pytest

from config.constants import (
    ManagementProfile,
    MarketRegime,
    PositionManagementState,
    SetupType,
    StructureType,
    TradeDirection,
    TriggerState,
    VolatilityLevel,
)
from core.models import Candle, PositionManagementDecision, SetupSignal, TargetContext, TradeRecord, TriggerResult
from core.state import BotState
from config.settings import BotSettings
from engines.position_manager import PositionManager
from engines.trade_plan_engine import TradePlanEngine
from execution.safer_testnet_executor import SaferTestnetExecutor


def _setup(direction=TradeDirection.LONG, setup_type=SetupType.BREAKOUT_RETEST, target_level=None):
    return SetupSignal(
        setup_type=setup_type,
        direction=direction,
        detected=True,
        invalidation_level=99 if direction == TradeDirection.LONG else 101,
        target_level=(103 if direction == TradeDirection.LONG else 97) if target_level is None else target_level,
    )


def _trigger(direction=TradeDirection.LONG):
    return TriggerResult(
        state=TriggerState.ENTRY_READY,
        is_triggered=True,
        direction=direction,
        trigger_price=100,
    )


@pytest.mark.parametrize(
    "direction,regime,levels,expected",
    [
        (TradeDirection.LONG, MarketRegime.STRONG_BULL, [102, 105], [102, 103]),
        (TradeDirection.SHORT, MarketRegime.STRONG_BEAR, [98, 95], [98, 97]),
    ],
)
def test_adaptive_trend_plan_uses_ordered_structure(direction, regime, levels, expected):
    kwargs = {"resistance_levels": levels} if direction == TradeDirection.LONG else {"support_levels": levels}
    plan = TradePlanEngine().generate_plan(
        _setup(direction), _trigger(direction), 1,
        TargetContext(regime=regime, regime_confidence="HIGH", volatility=VolatilityLevel.NORMAL, **kwargs),
    )
    assert plan.is_valid
    assert [plan.tp1, plan.tp2] == expected
    assert plan.target_mode == "TREND_EXPANSION"
    assert plan.management_profile == ManagementProfile.TREND_RUNNER
    assert plan.risk_reward_tp2 >= plan.risk_reward_tp1 > 0


@pytest.mark.parametrize(
    "regime,volatility,setup_type,mode",
    [
        (MarketRegime.RANGE, VolatilityLevel.NORMAL, SetupType.BREAKOUT_RETEST, "RANGE_TARGETS"),
        (MarketRegime.BULL, VolatilityLevel.NORMAL, SetupType.COUNTER_TREND_REACTION, "COUNTER_TREND_CONSERVATIVE"),
        (MarketRegime.BULL, VolatilityLevel.LOW, SetupType.BREAKOUT_RETEST, "VOLATILITY_COMPRESSED"),
        (MarketRegime.BULL, VolatilityLevel.EXTREME, SetupType.BREAKOUT_RETEST, "VOLATILITY_EXPANDED"),
    ],
)
def test_adaptive_modes_are_deterministic_and_valid(regime, volatility, setup_type, mode):
    engine = TradePlanEngine()
    context = TargetContext(regime=regime, volatility=volatility)
    one = engine.generate_plan(_setup(setup_type=setup_type, target_level=0), _trigger(), 1, context)
    two = engine.generate_plan(_setup(setup_type=setup_type, target_level=0), _trigger(), 1, context)
    assert one == two
    assert one.target_mode == mode
    assert one.stop_loss < one.entry_price < one.tp1 <= one.tp2
    assert one.tp1_source == "ATR_R_FALLBACK"


def _manage(direction, mark, **changes):
    args = dict(
        direction=direction,
        entry=100,
        initial_stop=90 if direction == TradeDirection.LONG else 110,
        current_stop=90 if direction == TradeDirection.LONG else 110,
        mark=mark,
        initial_size=1,
        current_size=1,
        structure=StructureType.MIXED,
        last_bos=None,
        last_choch=None,
        regime=MarketRegime.BULL if direction == TradeDirection.LONG else MarketRegime.BEAR,
        candle_timestamp=1_800_000,
    )
    args.update(changes)
    return PositionManager().evaluate(**args)


@pytest.mark.parametrize("direction,mark", [(TradeDirection.LONG, 97), (TradeDirection.SHORT, 103)])
def test_negative_position_with_intact_thesis_waits_for_recovery(direction, mark):
    decision = _manage(direction, mark)
    assert decision.state == PositionManagementState.RECOVERY_WAIT
    assert decision.thesis_valid
    assert decision.target_action == {}
    assert decision.stop_action == {}


@pytest.mark.parametrize(
    "direction,mark,structure,bos",
    [
        (TradeDirection.LONG, 95, StructureType.BEARISH, "BEARISH_BOS"),
        (TradeDirection.SHORT, 105, StructureType.BULLISH, "BULLISH_BOS"),
    ],
)
def test_confirmed_opposite_structure_exits_early(direction, mark, structure, bos):
    decision = _manage(direction, mark, structure=structure, last_bos=bos)
    assert decision.state == PositionManagementState.EXIT_EARLY
    assert "CONFIRMED_OPPOSITE_STRUCTURE_BREAK" in decision.reason_codes


def test_stop_only_tightens_and_size_increase_is_blocked():
    long = _manage(TradeDirection.LONG, 116, current_stop=95)
    short = _manage(TradeDirection.SHORT, 84, current_stop=105)
    restore_long = _manage(TradeDirection.LONG, 97, current_stop=85)
    restore_short = _manage(TradeDirection.SHORT, 103, current_stop=115)
    increased = _manage(TradeDirection.LONG, 97, current_size=1.1)
    assert long.stop_action["new_stop"] >= 95
    assert short.stop_action["new_stop"] <= 105
    assert restore_long.stop_action["new_stop"] == 90
    assert restore_short.stop_action["new_stop"] == 110
    assert increased.state == PositionManagementState.NO_CHANGE
    assert "AVERAGING_DOWN_BLOCKED" in increased.reason_codes
    assert increased.target_action == {}


def test_tp2_replan_requires_strength_and_obeys_cooldown():
    strong = _manage(
        TradeDirection.LONG, 116, regime=MarketRegime.STRONG_BULL,
        current_tp2=120, candidate_tp2=125, momentum_support=True, volume_support=True,
    )
    weak = _manage(
        TradeDirection.LONG, 116, regime=MarketRegime.BULL,
        current_tp2=120, candidate_tp2=125,
    )
    cooldown = _manage(
        TradeDirection.LONG, 116, regime=MarketRegime.STRONG_BULL,
        current_tp2=120, candidate_tp2=125, last_target_replan_at=1_500_001,
    )
    assert strong.state == PositionManagementState.TARGET_REPLAN
    assert strong.target_action["new_tp2"] == 125
    assert weak.state != PositionManagementState.TARGET_REPLAN
    assert cooldown.state != PositionManagementState.TARGET_REPLAN


def test_dashboard_timezone_is_iana_based_and_raw_epoch_is_not_mutated():
    timezone = Path("dashboard/timezone.js").read_text(encoding="utf-8")
    sources = "".join(Path(name).read_text(encoding="utf-8") for name in [
        "dashboard/app.js", "dashboard/chart-intelligence.js", "dashboard/trade-tracker.js", "dashboard/index.html",
    ])
    assert "Europe/Istanbul" in timezone
    assert "Intl.DateTimeFormat" in timezone
    assert "TSİ · Europe/Istanbul" in sources
    assert "timeZone: ISTANBUL_TIMEZONE" in timezone
    assert "+ 3" not in timezone and "+3" not in timezone
    assert "c.time*1000" in sources


def test_backtest_exposes_static_and_adaptive_comparison_modes():
    from backtest.simulator import BacktestSimulator

    assert BacktestSimulator.STATIC_EXIT_BASELINE == "STATIC_EXIT_BASELINE"
    assert BacktestSimulator.ADAPTIVE_MANAGEMENT_V1 == "ADAPTIVE_MANAGEMENT_V1"
    settings = BotSettings(_env_file=None)
    static = BacktestSimulator(settings, management_mode=BacktestSimulator.STATIC_EXIT_BASELINE)
    adaptive = BacktestSimulator(settings, management_mode=BacktestSimulator.ADAPTIVE_MANAGEMENT_V1)
    assert static.pipeline.trade_plan_engine.dynamic_targets_enabled is False
    assert adaptive.pipeline.trade_plan_engine.dynamic_targets_enabled is True
    context = TargetContext(regime=MarketRegime.RANGE, resistance_levels=[102, 104])
    static_plan = static.pipeline.trade_plan_engine.generate_plan(_setup(), _trigger(), 1, context)
    adaptive_plan = adaptive.pipeline.trade_plan_engine.generate_plan(_setup(), _trigger(), 1, context)
    assert static_plan.target_mode == "STATIC_EXIT_BASELINE"
    assert adaptive_plan.target_mode == "RANGE_TARGETS"
    with pytest.raises(ValueError, match="incompatible"):
        BacktestSimulator(settings, pipeline=adaptive.pipeline, management_mode=BacktestSimulator.STATIC_EXIT_BASELINE)


class _Journal:
    def read_state(self): return {}
    def write_state(self, _state): return None
    def record(self, **row): return row


class _ProtectionClient:
    testnet = True
    configured = True

    def __init__(self, fail_new=False):
        self.fail_new = fail_new
        self.calls = []
        self.orders = [
            {"algoId": 1, "orderType": "STOP_MARKET", "triggerPrice": "90", "quantity": "1", "reduceOnly": True},
            {"algoId": 2, "orderType": "TAKE_PROFIT_MARKET", "triggerPrice": "120", "quantity": "1", "reduceOnly": True},
        ]

    def normalize_quantity(self, _symbol, quantity, **_kwargs):
        return round(float(quantity), 3)

    def get_open_algo_orders(self, _symbol):
        return [dict(row) for row in self.orders]

    def place_protective_order(self, _symbol, _side, order_type, quantity, stop_price):
        self.calls.append(("place", order_type, stop_price))
        order = {"algoId": 3, "orderType": order_type, "triggerPrice": str(stop_price), "quantity": str(quantity), "reduceOnly": True}
        if not self.fail_new:
            self.orders.append(order)
        return {"binance_order_id": 3, "type": order_type, "trigger_price": stop_price, "requested_quantity": quantity}

    def cancel_algo_order(self, *, algo_id):
        self.calls.append(("cancel", algo_id))
        self.orders = [row for row in self.orders if row["algoId"] != algo_id]


def _safer(client):
    settings = SimpleNamespace(
        TP_SPLIT_CONSERVATIVE=.70, TP_SPLIT_BALANCED=.50, TP_SPLIT_TREND_RUNNER=.35,
        testnet_execution_enabled=False,
    )
    return SaferTestnetExecutor(client, settings=settings, execution_journal=_Journal())


def test_dynamic_split_profiles_and_small_position_fallback():
    executor = _safer(_ProtectionClient())
    conservative = executor._split_target_quantities(1, {"management_profile": "CONSERVATIVE", "tp1": 110, "tp2": 120})
    balanced = executor._split_target_quantities(1, {"management_profile": "BALANCED", "tp1": 110, "tp2": 120})
    runner = executor._split_target_quantities(1, {"management_profile": "TREND_RUNNER", "tp1": 110, "tp2": 120})
    assert conservative == (.7, .3)
    assert balanced == (.5, .5)
    assert runner == (.35, .65)


def test_stop_replacement_places_and_verifies_before_cancel():
    client = _ProtectionClient()
    executor = _safer(client)
    executor._replace_stop_safely({"position_amt": 1}, 95)
    assert client.calls[:2] == [("place", "STOP_MARKET", 95), ("cancel", 1)]
    assert any(row["orderType"] == "STOP_MARKET" and row["algoId"] == 3 for row in client.orders)


def test_failed_stop_replacement_preserves_existing_stop():
    client = _ProtectionClient(fail_new=True)
    executor = _safer(client)
    with pytest.raises(Exception):
        executor._replace_stop_safely({"position_amt": 1}, 95)
    assert client.calls == [("place", "STOP_MARKET", 95)]
    assert any(row["orderType"] == "STOP_MARKET" and row["algoId"] == 1 for row in client.orders)


def test_management_actions_are_deduplicated_by_closed_5m_timestamp():
    client = _ProtectionClient()
    executor = _safer(client)
    executor._entry_context = {"initial_stop": 90, "initial_size": 1, "management_profile": "BALANCED"}
    snapshot = {
        "candles": {"5m": [{"time": 1_000}]},
        "market": {"mark_price": 97},
        "sources": {"binance": {"status": "HEALTHY", "market_data_trading_safe": True}},
        "decision": {"regime": "BULL", "volatility": "NORMAL"},
        "chart_intelligence": {"timeframes": {"5m": {"status": "AVAILABLE", "closed_candles": 30, "structure": "MIXED", "trend": "UP", "volume_state": "NORMAL"}}},
        "zones": [],
    }
    position = {"position_amt": 1, "entry_price": 100, "mark_price": 97, "unrealized_pnl": -3}
    first = executor.manage_adaptive_position(snapshot, BotState(), position)
    second = executor.manage_adaptive_position(snapshot, BotState(), position)
    assert first["status"] == "RECOVERY_WAIT"
    assert second["status"] == "DUPLICATE_MANAGEMENT_CANDLE"


@pytest.mark.parametrize(
    "direction,trend,supportive,opposing,available",
    [
        (TradeDirection.LONG, "UP", True, False, True),
        (TradeDirection.LONG, "DOWN", False, True, True),
        (TradeDirection.SHORT, "DOWN", True, False, True),
        (TradeDirection.SHORT, "UP", False, True, True),
        (TradeDirection.LONG, "RANGE", False, False, True),
        (TradeDirection.SHORT, "UNAVAILABLE", False, False, False),
    ],
)
def test_chart_trend_vocabulary_is_normalized(direction, trend, supportive, opposing, available):
    assert PositionManager.normalize_momentum(direction, trend) == (supportive, opposing, available)


@pytest.mark.parametrize(
    "raw,supportive,available",
    [("EXPANSION", True, True), ("NORMAL", True, True), ("CONTRACTION", False, True), ("UNAVAILABLE", False, False)],
)
def test_chart_volume_vocabulary_is_normalized(raw, supportive, available):
    assert PositionManager.normalize_volume(raw) == (supportive, available)


@pytest.mark.parametrize(
    "direction,regime,structure,choch,momentum_opposing",
    [
        (TradeDirection.LONG, MarketRegime.BULL, StructureType.BULLISH, "BEARISH_CHOCH", False),
        (TradeDirection.SHORT, MarketRegime.BEAR, StructureType.BEARISH, "BULLISH_CHOCH", False),
    ],
)
def test_opposite_choch_alone_weakens_but_does_not_exit(direction, regime, structure, choch, momentum_opposing):
    mark = 97 if direction == TradeDirection.LONG else 103
    decision = _manage(direction, mark, regime=regime, structure=structure, last_choch=choch, momentum_support=False, momentum_opposing=momentum_opposing)
    assert decision.state != PositionManagementState.EXIT_EARLY
    assert decision.thesis_valid
    assert "THESIS_WEAKENING_CHOCH" in decision.reason_codes


@pytest.mark.parametrize(
    "direction,regime,structure,choch",
    [
        (TradeDirection.LONG, MarketRegime.BEAR, StructureType.BULLISH, "BEARISH_CHOCH"),
        (TradeDirection.SHORT, MarketRegime.BULL, StructureType.BEARISH, "BULLISH_CHOCH"),
    ],
)
def test_opposite_choch_with_regime_confirmation_exits(direction, regime, structure, choch):
    mark = 97 if direction == TradeDirection.LONG else 103
    decision = _manage(direction, mark, regime=regime, structure=structure, last_choch=choch, momentum_support=False)
    assert decision.state == PositionManagementState.EXIT_EARLY
    assert "OPPOSITE_CHOCH_WITH_CONFIRMATION" in decision.reason_codes


@pytest.mark.parametrize(
    "direction,mark,regime",
    [(TradeDirection.LONG, 97, MarketRegime.BULL), (TradeDirection.SHORT, 103, MarketRegime.BEAR)],
)
def test_recovery_wait_reports_opposing_momentum_without_fake_support(direction, mark, regime):
    decision = _manage(direction, mark, regime=regime, momentum_support=False, momentum_opposing=True)
    assert decision.state == PositionManagementState.RECOVERY_WAIT
    assert decision.momentum_support is False
    assert decision.momentum_opposing is True
    assert "MOMENTUM_OPPOSING_BUT_NOT_STRUCTURALLY_CONFIRMED" in decision.reason_codes


def test_unavailable_5m_analysis_keeps_existing_protection_untouched():
    client = _ProtectionClient()
    executor = _safer(client)
    executor._entry_context = {"initial_stop": 90, "initial_size": 1, "management_profile": "BALANCED"}
    snapshot = {
        "candles": {"5m": [{"time": 2_000}]},
        "market": {"mark_price": 116},
        "sources": {"binance": {"status": "HEALTHY", "market_data_trading_safe": True}},
        "decision": {"regime": "STRONG_BULL", "volatility": "NORMAL"},
        "chart_intelligence": {"timeframes": {"5m": {"status": "UNAVAILABLE", "closed_candles": 0, "structure": "MIXED", "trend": "UNAVAILABLE", "volume_state": "UNAVAILABLE"}}},
        "zones": [{"center": 125}],
    }
    before = list(client.orders)
    result = executor.manage_adaptive_position(snapshot, BotState(), {"position_amt": 1, "entry_price": 100, "mark_price": 116})
    assert result["status"] == "NO_CHANGE"
    assert result["position_intelligence"]["reason_codes"] == ["MARKET_ANALYSIS_UNAVAILABLE_KEEP_EXISTING_PROTECTION"]
    assert result["position_intelligence"]["momentum_support"] is False
    assert result["position_intelligence"]["volume_support"] is False
    assert client.orders == before
    assert client.calls == []


def _trade():
    return TradeRecord(
        trade_id="BT-PARITY", setup_type=SetupType.BREAKOUT_RETEST, direction=TradeDirection.LONG,
        entry_time=0, entry_price=100, stop_loss=90, tp1=110, tp2=120,
        size_btc=1, size_usdt=100, is_closed=False,
    )


def test_backtest_applies_stop_and_tp2_transitions_without_redefining_initial_risk():
    from backtest.simulator import BacktestSimulator

    simulator = BacktestSimulator(BotSettings(_env_file=None), management_mode=BacktestSimulator.ADAPTIVE_MANAGEMENT_V1)
    trade = _trade()
    stop = PositionManagementDecision(
        state=PositionManagementState.TIGHTEN_STOP, reason_codes=["TEST"],
        stop_action={"action": "TIGHTEN_STOP", "new_stop": 100},
    )
    simulator._apply_adaptive_transition(trade, stop)
    assert trade.stop_loss == 100
    assert simulator._state_for(trade)["initial_stop"] == 90
    target = PositionManagementDecision(
        state=PositionManagementState.TARGET_REPLAN, reason_codes=["TEST"], target_replan_count=1,
        last_target_replan_at=300_000, target_action={"action": "REPLACE_TP2", "new_tp2": 125},
    )
    simulator._apply_adaptive_transition(trade, target)
    assert trade.tp2 == 125
    assert simulator._state_for(trade)["current_tp2"] == 125


def test_backtest_hold_and_recovery_preserve_mutable_protection():
    from backtest.simulator import BacktestSimulator

    simulator = BacktestSimulator(BotSettings(_env_file=None), management_mode=BacktestSimulator.ADAPTIVE_MANAGEMENT_V1)
    trade = _trade()
    for state in (PositionManagementState.HOLD, PositionManagementState.RECOVERY_WAIT):
        simulator._apply_adaptive_transition(trade, PositionManagementDecision(state=state))
    assert (trade.stop_loss, trade.tp2) == (90, 120)


def _closed_candle(close):
    return Candle(timestamp=1_800_000, open=close, high=close, low=close, close=close, volume=1, is_closed=True)


def test_backtest_shared_manager_generates_and_applies_stop_tightening():
    from backtest.simulator import BacktestSimulator

    provider = lambda _trade, _candle: {
        "frame_status": "AVAILABLE", "structure": StructureType.MIXED,
        "regime": MarketRegime.BULL, "trend": "UP", "volume_state": "NORMAL",
    }
    simulator = BacktestSimulator(
        BotSettings(_env_file=None), management_mode=BacktestSimulator.ADAPTIVE_MANAGEMENT_V1,
        management_context_provider=provider,
    )
    trade = _trade()
    decision = simulator._adaptive_decision(trade, _closed_candle(116))
    assert decision.state == PositionManagementState.TIGHTEN_STOP
    simulator._apply_adaptive_transition(trade, decision)
    assert trade.stop_loss == 102.5
    assert simulator._state_for(trade)["initial_stop"] == 90


def test_backtest_shared_manager_generates_and_applies_tp2_replan():
    from backtest.simulator import BacktestSimulator

    provider = lambda _trade, _candle: {
        "frame_status": "AVAILABLE", "structure": StructureType.BULLISH,
        "regime": MarketRegime.STRONG_BULL, "trend": "UP", "volume_state": "EXPANSION",
        "candidate_tp2": 125,
    }
    simulator = BacktestSimulator(
        BotSettings(_env_file=None), management_mode=BacktestSimulator.ADAPTIVE_MANAGEMENT_V1,
        management_context_provider=provider,
    )
    trade = _trade()
    decision = simulator._adaptive_decision(trade, _closed_candle(116))
    assert decision.state == PositionManagementState.TARGET_REPLAN
    simulator._apply_adaptive_transition(trade, decision)
    assert trade.tp2 == 125
    assert simulator._state_for(trade)["target_replan_count"] == 1
