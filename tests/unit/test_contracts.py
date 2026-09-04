"""Unit tests for core/models.py and config contracts (Phase 0 Gate)."""

import pytest
from core.models import (
    Candle,
    SwingPoint,
    DerivativesState,
    GlobalContextState,
    TradePlan,
    RiskAssessment,
)
from config.constants import (
    DerivativesStatus,
    GlobalContextStatus,
    DataSource,
    TradeDirection,
    SetupType,
    RiskDecision,
    FundingClass,
    CrowdingStatus,
)
from config.hypotheses import INITIAL_HYPOTHESES
from config.settings import get_settings


def test_swing_point_dual_timestamps():
    # Verify SwingPoint model has both swing_time and confirmed_at
    sp = SwingPoint(
        swing_time=1700000000000,
        confirmed_at=1700028800000,  # 8 hours later (2 4H bars)
        price=65000.0,
        is_high=True,
        candle_index=10,
        confirmed=True,
    )
    assert sp.swing_time == 1700000000000
    assert sp.confirmed_at > sp.swing_time
    assert sp.is_high is True


def test_derivatives_state_provenance():
    # Verify DerivativesState carries provenance metadata and field-level containers
    from core.models import DerivativesField
    ds = DerivativesState(
        status=DerivativesStatus.CONFIRM,
        open_interest=DerivativesField(value=50000.0, source=DataSource.BINANCE),
        oi_change_pct=DerivativesField(value=0.001, source=DataSource.BINANCE),
        funding_rate=DerivativesField(value=0.0001, source=DataSource.BINANCE),
        funding_class=FundingClass.NORMAL,
        long_short_ratio=DerivativesField(value=1.0, source=DataSource.UNAVAILABLE),
        crowding=CrowdingStatus.BALANCED,
        liquidations_24h_usdt=DerivativesField(value=1500000.0, source=DataSource.COINGLASS),
        taker_buy_volume_ratio=DerivativesField(value=0.5, source=DataSource.BINANCE),
    )
    assert ds.status == DerivativesStatus.CONFIRM
    assert ds.open_interest.value == 50000.0
    assert ds.open_interest.source == DataSource.BINANCE
    assert ds.liquidations_24h_usdt.source == DataSource.COINGLASS
    assert ds.long_short_ratio.source == DataSource.UNAVAILABLE


def test_global_context_state():
    # Verify GlobalContextState default is UNAVAILABLE
    gc = GlobalContextState()
    assert gc.status == GlobalContextStatus.UNAVAILABLE
    assert gc.source == DataSource.UNAVAILABLE


def test_trade_plan_contract():
    tp = TradePlan(
        setup_type=SetupType.TREND_PULLBACK,
        direction=TradeDirection.LONG,
        entry_price=65000.0,
        stop_loss=64000.0,
        tp1=66500.0,
        tp2=67500.0,
        invalidation=64100.0,
        risk_reward=1.50,
        is_valid=True,
    )
    assert tp.entry_price == 65000.0
    assert tp.risk_reward == 1.50
    assert tp.direction == TradeDirection.LONG


def test_hypotheses_registry_completeness():
    # Verify all expected keys are present in INITIAL_HYPOTHESES
    required_keys = [
        "wick_rejection_ratio",
        "directional_body_ratio",
        "volume_rvol_threshold",
        "sr_clustering_tolerance_pct",
        "location_proximity_pct",
        "counter_trend_rsi_oversold",
        "min_risk_reward_ratio",
        "trend_risk_per_trade_pct",
        "counter_trend_risk_pct",
        "max_daily_loss_pct",
        "max_consecutive_losses",
        "exit_policy_tp1_close_pct",
        "exit_policy_auto_breakeven",
    ]
    for k in required_keys:
        assert k in INITIAL_HYPOTHESES, f"Missing hypothesis key: {k}"


def test_settings_load_from_hypotheses():
    settings = get_settings()
    assert settings.MIN_RISK_REWARD == INITIAL_HYPOTHESES["min_risk_reward_ratio"]
    assert settings.TREND_RISK_PCT == INITIAL_HYPOTHESES["trend_risk_per_trade_pct"]
    assert settings.MAX_DAILY_LOSS_PCT == INITIAL_HYPOTHESES["max_daily_loss_pct"]
