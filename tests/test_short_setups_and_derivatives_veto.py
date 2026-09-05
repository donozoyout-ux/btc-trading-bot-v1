import pytest

from config.constants import DataSource, DerivativesStatus, LocationQuality, MarketRegime, SetupType, TradeDirection, VolatilityLevel
from core.models import Candle, ConfluenceZone, DerivativesField, LocationResult, RegimeResult
from engines.derivatives_engine import DerivativesEngine
from engines.setup_engine import SetupEngine
from engines.strategy_orchestrator import StrategyOrchestrator
from core.models import DecisionReport
from config.constants import DecisionStatus, RiskDecision, StructureType, TriggerState


def candle(i, close, high=None, low=None, closed=True):
    return Candle(timestamp=i * 900_000, open=close, high=high if high is not None else close + 1,
                  low=low if low is not None else close - 1, close=close, volume=100, is_closed=closed)


def regime(value, adx=20):
    return RegimeResult(regime=value, score=0, confidence="HIGH", volatility=VolatilityLevel.NORMAL,
                        details={"current_adx": adx})


ZONE = ConfluenceZone(level_type="RESISTANCE", price_min=99, price_max=101, center=100, strength=3)


def test_setup_b_long_behavior_remains_available():
    rows = [candle(i, 98) for i in range(15)]
    rows[-9] = candle(6, 102)
    rows[-2] = candle(13, 101.5, high=102, low=100.5)
    rows[-1] = candle(14, 102)
    result = SetupEngine().detect_setup_b_breakout_retest(regime(MarketRegime.BULL), rows, [ZONE])
    assert result.direction == TradeDirection.LONG
    assert result.setup_type == SetupType.BREAKOUT_RETEST


def test_setup_b_short_requires_prior_breakdown_then_closed_retest():
    rows = [candle(i, 102) for i in range(15)]
    rows[-9] = candle(6, 98)
    rows[-2] = candle(13, 98.5, high=100, low=98)
    rows[-1] = candle(14, 98)
    result = SetupEngine().detect_setup_b_breakout_retest(regime(MarketRegime.BEAR), rows, [ZONE])
    assert result.direction == TradeDirection.SHORT
    assert result.setup_type == SetupType.BREAKOUT_RETEST
    assert "holding as resistance" in result.reason


def test_setup_b_short_has_no_signal_without_breakdown_or_retest():
    no_breakdown = [candle(i, 102) for i in range(15)]
    assert SetupEngine().detect_setup_b_breakout_retest(regime(MarketRegime.BEAR), no_breakdown, [ZONE]) is None
    no_retest = [candle(i, 102) for i in range(15)]
    no_retest[-9] = candle(6, 98)
    for i in range(3):
        no_retest[-1-i] = candle(14-i, 96, high=97, low=95)
    assert SetupEngine().detect_setup_b_breakout_retest(regime(MarketRegime.BEAR), no_retest, [ZONE]) is None


def test_setup_b_ignores_open_future_breakdown_bar():
    rows = [candle(i, 102) for i in range(15)]
    rows.append(candle(15, 98, high=100, low=97, closed=False))
    assert SetupEngine().detect_setup_b_breakout_retest(regime(MarketRegime.BEAR), rows, [ZONE]) is None


@pytest.mark.parametrize("market,direction,price,support,resistance,rsi", [
    (MarketRegime.BEAR, TradeDirection.LONG, 90, ConfluenceZone(level_type="SUPPORT", price_min=89, price_max=91, center=90, strength=2), None, 20),
    (MarketRegime.BULL, TradeDirection.SHORT, 110, None, ConfluenceZone(level_type="RESISTANCE", price_min=109, price_max=111, center=110, strength=2), 80),
])
def test_setup_c_is_symmetric(monkeypatch, market, direction, price, support, resistance, rsi):
    engine = SetupEngine()
    monkeypatch.setattr(engine, "calculate_bollinger_bands", lambda *args: (100, 109, 91))
    monkeypatch.setattr(engine, "calculate_rsi_quick", lambda *args: rsi)
    location = LocationResult(quality=LocationQuality.STRONG_LONG_LOCATION if direction == TradeDirection.LONG else LocationQuality.STRONG_SHORT_LOCATION,
                              current_price=price, nearest_support=support, nearest_resistance=resistance,
                              distance_to_support_pct=0, distance_to_resistance_pct=0)
    result = engine.detect_setup_c_counter_trend(regime(market), [candle(i, price) for i in range(25)], location)
    assert result.direction == direction
    assert result.setup_type == SetupType.COUNTER_TREND_REACTION


@pytest.mark.parametrize("market,price,rsi", [(MarketRegime.BEAR, 90, 20), (MarketRegime.BULL, 110, 80)])
def test_setup_c_adx_veto_and_weak_zone_block(monkeypatch, market, price, rsi):
    engine = SetupEngine()
    monkeypatch.setattr(engine, "calculate_bollinger_bands", lambda *args: (100, 109, 91))
    monkeypatch.setattr(engine, "calculate_rsi_quick", lambda *args: rsi)
    zone_type = "SUPPORT" if market == MarketRegime.BEAR else "RESISTANCE"
    zone = ConfluenceZone(level_type=zone_type, price_min=price - 1, price_max=price + 1, center=price, strength=1)
    location = LocationResult(quality=LocationQuality.GOOD_LONG_LOCATION, current_price=price,
                              nearest_support=zone if market == MarketRegime.BEAR else None,
                              nearest_resistance=zone if market == MarketRegime.BULL else None)
    rows = [candle(i, price) for i in range(25)]
    assert engine.detect_setup_c_counter_trend(regime(market, 40), rows, location) is None
    assert engine.detect_setup_c_counter_trend(regime(market, 20), rows, location) is None


def dfield(value, source=DataSource.BINANCE):
    return DerivativesField(value=value, source=source, observed_at=123)


@pytest.mark.parametrize("direction,funding,ratio,taker", [
    (TradeDirection.LONG, 0.0006, 2.3, 0.8),
    (TradeDirection.SHORT, -0.0006, 0.7, 1.2),
])
def test_extreme_crowding_with_contradictory_participation_rejects(direction, funding, ratio, taker):
    result = DerivativesEngine().evaluate_derivatives(
        direction, SetupType.TREND_PULLBACK, 0, oi_field=dfield(100), funding_field=dfield(funding),
        ls_field=dfield(ratio), taker_field=dfield(taker), liquidation_field=dfield(None, DataSource.UNAVAILABLE))
    assert result.status == DerivativesStatus.REJECT


@pytest.mark.parametrize("direction,price_change,taker", [
    (TradeDirection.LONG, -0.01, 0.8),
    (TradeDirection.SHORT, 0.01, 1.2),
])
def test_countertrend_expanding_oi_contradiction_rejects(direction, price_change, taker):
    result = DerivativesEngine().evaluate_derivatives(
        direction, SetupType.COUNTER_TREND_REACTION, price_change, oi_field=dfield(100),
        oi_change_field=dfield(0.01), funding_field=dfield(0), ls_field=dfield(1), taker_field=dfield(taker))
    assert result.status == DerivativesStatus.REJECT


def test_warn_degraded_and_unavailable_semantics_are_distinct():
    engine = DerivativesEngine()
    warning = engine.evaluate_derivatives(TradeDirection.LONG, SetupType.TREND_PULLBACK, 0,
        oi_field=dfield(100), funding_field=dfield(0.0006), ls_field=dfield(2.3), taker_field=dfield(1.0))
    assert warning.status == DerivativesStatus.WARN
    assert engine.evaluate_derivatives(TradeDirection.LONG, SetupType.TREND_PULLBACK, 0,
        oi_field=dfield(100)).status == DerivativesStatus.DEGRADED
    unavailable = engine.evaluate_derivatives(TradeDirection.LONG, SetupType.TREND_PULLBACK, 0)
    assert unavailable.status == DerivativesStatus.UNAVAILABLE
    assert unavailable.oi_change_pct.value is None
    assert unavailable.oi_change_pct.source == DataSource.UNAVAILABLE


def test_strategy_observability_preserves_short_candidate_and_derivatives_blocker():
    report = DecisionReport(
        timestamp=1, price=100, regime=MarketRegime.BEAR, regime_score=-50, confidence="HIGH",
        volatility=VolatilityLevel.NORMAL, structure_4h=StructureType.BEARISH,
        structure_1h=StructureType.BEARISH, location=LocationQuality.GOOD_SHORT_LOCATION,
        setup=SetupType.BREAKOUT_RETEST, setup_direction=TradeDirection.SHORT,
        trigger_state=TriggerState.ENTRY_READY, derivatives=DerivativesStatus.REJECT,
        risk_status=RiskDecision.REJECT_TRADE, final_decision=DecisionStatus.NO_TRADE,
        reason="Derivatives Veto: contradictory participation",
    )
    summary = StrategyOrchestrator().summarize(report, {}, {}, {})
    assert summary["direction"] == "SHORT"
    assert "DERIVATIVES_REJECT" in summary["blocking_reasons"]
    assert summary["trade_plan"] is None
