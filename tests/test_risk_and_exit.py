"""Unit tests for Risk Engine, Position Sizer, and Exit Engine using TradePlan-based API."""

import pytest
from config.settings import BotSettings
from config.constants import TradeDirection, SetupType, RiskDecision, TriggerState
from core.models import TradeRecord, Candle, TradePlan
from core.models import TradeRecord, Candle
from core.state import BotState
from engines.risk_engine import RiskEngine
from engines.exit_engine import ExitEngine
from engines.trade_plan_engine import TradePlanEngine


def test_risk_engine_sizing_and_rr():
    settings = BotSettings(INITIAL_CAPITAL_USDT=10_000.0, TREND_RISK_PCT=0.0050, MIN_RISK_REWARD=1.5)
    risk_engine = RiskEngine(settings)
    state = BotState(account_balance_usdt=10_000.0)

    # Create a TradePlan
    plan_engine = TradePlanEngine()
    setup = SetupSignal(
        setup_type=SetupType.TREND_PULLBACK,
        direction=TradeDirection.LONG,
        detected=True,
        invalidation_level=64000.0,
        target_level=67000.0,
    )
    trigger = TriggerResult(
        state=TriggerState.ENTRY_READY,
        is_triggered=True,
        direction=TradeDirection.LONG,
        trigger_price=65000.0,
    )
    trade_plan = plan_engine.generate_plan(setup=setup, trigger=trigger, current_atr=500.0)

    assessment = risk_engine.evaluate_risk(trade_plan=trade_plan, state=state)
    assert assessment.decision == RiskDecision.ACCEPT_TRADE
    assert assessment.risk_reward >= 1.5
    assert abs(assessment.risk_amount_usdt - 50.0) < 1.0
    assert assessment.position_size_btc > 0


def test_risk_engine_rr_rejection():
    settings = BotSettings(INITIAL_CAPITAL_USDT=10_000.0, MIN_RISK_REWARD=2.0)
    risk_engine = RiskEngine(settings)
    state = BotState(account_balance_usdt=10_000.0)

    plan_engine = TradePlanEngine()
    setup = SetupSignal(
        setup_type=SetupType.TREND_PULLBACK,
        direction=TradeDirection.LONG,
        detected=True,
        invalidation_level=64000.0,
        target_level=65500.0,
    )
    trigger = TriggerResult(
        state=TriggerState.ENTRY_READY,
        is_triggered=True,
        direction=TradeDirection.LONG,
        trigger_price=65000.0,
    )
    trade_plan = plan_engine.generate_plan(setup=setup, trigger=trigger, current_atr=200.0)

    assessment = risk_engine.evaluate_risk(trade_plan=trade_plan, state=state)
    assert assessment.decision == RiskDecision.REJECT_TRADE
    assert "Insufficient R:R" in assessment.rejection_reason


def test_kill_switch_consecutive_losses():
    settings = BotSettings(MAX_CONSECUTIVE_LOSSES=3)
    risk_engine = RiskEngine(settings)
    state = BotState(consecutive_losses=3)

    plan_engine = TradePlanEngine()
    setup = SetupSignal(
        setup_type=SetupType.TREND_PULLBACK,
        direction=TradeDirection.LONG,
        detected=True,
        invalidation_level=64000.0,
        target_level=67000.0,
    )
    trigger = TriggerResult(
        state=TriggerState.ENTRY_READY,
        is_triggered=True,
        direction=TradeDirection.LONG,
        trigger_price=65000.0,
    )
    trade_plan = plan_engine.generate_plan(setup=setup, trigger=trigger, current_atr=300.0)

    assessment = risk_engine.evaluate_risk(trade_plan=trade_plan, state=state)
    assert assessment.decision == RiskDecision.REJECT_TRADE
    assert "CONSECUTIVE_LOSS_GUARD" in assessment.rejection_reason or "NEW TRADES DISABLED" in assessment.rejection_reason


def test_exit_engine_tp1_move_to_be():
    exit_engine = ExitEngine()
    trade = TradeRecord(
        trade_id="T1",
        setup_type=SetupType.TREND_PULLBACK,
        direction=TradeDirection.LONG,
        entry_time=1000,
        entry_price=65000.0,
        stop_loss=64500.0,
        tp1=65800.0,
        tp2=66500.0,
        size_btc=0.1,
        size_usdt=6500.0,
    )

    c = Candle(
        timestamp=2000,
        open=65400.0,
        high=65900.0,
        low=65300.0,
        close=65700.0,
        volume=10.0,
        is_closed=True,
    )

    is_closed, reason, exit_p = exit_engine.evaluate_exit(trade, c)
    assert not is_closed
    assert trade.stop_loss == 65000.0  # Stop Loss moved to Break-Even!


def test_exit_engine_intrabar_ambiguity_conservative():
    """When both SL and TP are hit in same candle without sub_candles, SL wins."""
    exit_engine = ExitEngine()
    trade = TradeRecord(
        trade_id="T2",
        setup_type=SetupType.TREND_PULLBACK,
        direction=TradeDirection.LONG,
        entry_time=1000,
        entry_price=65000.0,
        stop_loss=64800.0,
        tp1=65800.0,
        tp2=66500.0,
        size_btc=0.1,
        size_usdt=6500.0,
    )

    # Candle that hits BOTH SL (low=64700) and TP1 (high=65900)
    c = Candle(
        timestamp=2000,
        open=65100.0,
        high=65900.0,
        low=64700.0,
        close=65100.0,
        volume=10.0,
        is_closed=True,
    )

    is_closed, reason, exit_p = exit_engine.evaluate_exit(trade, c)
    assert is_closed
    assert reason == "STOP_LOSS"  # Conservative: SL assumed hit first


def test_exit_engine_intrabar_ambiguity_with_sub_candles():
    """When sub_candles are provided, use them to resolve ambiguity."""
    exit_engine = ExitEngine()
    trade = TradeRecord(
        trade_id="T3",
        setup_type=SetupType.TREND_PULLBACK,
        direction=TradeDirection.LONG,
        entry_time=1000,
        entry_price=65000.0,
        stop_loss=64800.0,
        tp1=65800.0,
        tp2=66500.0,
        size_btc=0.1,
        size_usdt=6500.0,
    )

    # Main candle hits both SL and TP
    main_candle = Candle(
        timestamp=2000, open=65100.0, high=65900.0, low=64700.0, close=65100.0, volume=10.0, is_closed=True
    )
    # Sub-candle shows TP hit first, then SL
    sub_candles = [
        Candle(timestamp=2000, open=65100.0, high=65900.0, low=65200.0, close=65800.0, volume=5.0, is_closed=True),
        Candle(timestamp=2001, open=65800.0, high=65800.0, low=64700.0, close=64750.0, volume=5.0, is_closed=True),
    ]

    is_closed, reason, exit_p = exit_engine.evaluate_exit(trade, main_candle, sub_candles=sub_candles)
    assert is_closed
    assert reason == "STOP_LOSS"  # First sub-bar doesn't hit SL, second does -> SL first


# Need to import SetupSignal and TriggerResult for TradePlan creation
from core.models import SetupSignal, TriggerResult
