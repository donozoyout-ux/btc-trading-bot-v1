import pytest

from config.constants import DataSource, DerivativesStatus, LocationQuality, MarketRegime, SetupType, TradeDirection, VolatilityLevel
from core.models import Candle, ConfluenceZone, DerivativesField, LocationResult, MarketStructure, RegimeResult
from engines.derivatives_engine import DerivativesEngine
from engines.setup_engine import SetupEngine
from engines.strategy_orchestrator import StrategyOrchestrator
from core.models import DecisionReport
from config.constants import DecisionStatus, RiskDecision, StructureType, TriggerState
from config.settings import BotSettings


def candle(i, close, high=None, low=None, closed=True):
    return Candle(timestamp=i * 900_000, open=close, high=high if high is not None else close + 1,
                  low=low if low is not None else close - 1, close=close, volume=100, is_closed=closed)


def regime(value, adx=20):
    return RegimeResult(regime=value, score=0, confidence="HIGH", volatility=VolatilityLevel.NORMAL,
                        details={"current_adx": adx})


ZONE = ConfluenceZone(level_type="RESISTANCE", price_min=99, price_max=101, center=100, strength=3)


def test_experimental_short_settings_default_disabled():
    settings = BotSettings(_env_file=None)
    assert settings.ENABLE_SETUP_B_SHORT is False
    assert settings.ENABLE_SETUP_C_SHORT is False
    assert settings.COUNTER_TREND_RSI_OVERSOLD == 32.0


def test_setup_b_long_behavior_remains_available():
    rows = [candle(i, 98) for i in range(15)]
    rows[-9] = candle(6, 102)
    rows[-2] = Candle(timestamp=13 * 900_000, open=100.5, high=102, low=100.5, close=101.5, volume=100, is_closed=True)
    rows[-1] = candle(14, 102)
    result = SetupEngine().detect_setup_b_breakout_retest(regime(MarketRegime.BULL), rows, [ZONE])
    assert result.direction == TradeDirection.LONG
    assert result.setup_type == SetupType.BREAKOUT_RETEST


def test_setup_b_long_preserves_prior_last_10_window_parity():
    rows = [candle(i, 98) for i in range(15)]
    rows[-3] = candle(12, 102, high=103, low=100.5)
    rows[-2] = Candle(timestamp=13 * 900_000, open=100, high=101.5, low=100, close=101, volume=100, is_closed=True)
    rows[-1] = candle(14, 102)
    result = SetupEngine().detect_setup_b_breakout_retest(regime(MarketRegime.BULL), rows, [ZONE])
    assert result is not None
    assert result.direction == TradeDirection.LONG


def test_setup_b_short_requires_prior_breakdown_then_closed_retest():
    rows = [candle(i, 102) for i in range(15)]
    rows[-9] = candle(6, 98)
    rows[-2] = Candle(timestamp=13 * 900_000, open=100, high=100, low=98, close=98.5, volume=100, is_closed=True)
    rows[-1] = candle(14, 98)
    result = SetupEngine(enable_setup_b_short=True).detect_setup_b_breakout_retest(regime(MarketRegime.BEAR), rows, [ZONE])
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


def test_setup_b_failed_level_cannot_be_revived_by_later_recovery():
    long_rows = [candle(i, 98) for i in range(15)]
    long_rows[-9] = candle(6, 102)
    long_rows[-6] = Candle(timestamp=9 * 900_000, open=100.5, high=102, low=100.5, close=101.5, volume=100, is_closed=True)
    long_rows[-4] = candle(11, 98, high=100, low=97)
    long_rows[-1] = candle(14, 102)
    assert SetupEngine().detect_setup_b_breakout_retest(regime(MarketRegime.BULL), long_rows, [ZONE]) is None

    short_rows = [candle(i, 102) for i in range(15)]
    short_rows[-9] = candle(6, 98)
    short_rows[-6] = Candle(timestamp=9 * 900_000, open=100, high=100, low=98, close=98.5, volume=100, is_closed=True)
    short_rows[-4] = candle(11, 102, high=103, low=100)
    short_rows[-1] = candle(14, 98)
    assert SetupEngine(enable_setup_b_short=True).detect_setup_b_breakout_retest(regime(MarketRegime.BEAR), short_rows, [ZONE]) is None


def test_setup_b_short_is_disabled_by_default_and_explicitly_enabled():
    rows = [candle(i, 102) for i in range(15)]
    rows[-9] = candle(6, 98)
    rows[-2] = Candle(timestamp=13 * 900_000, open=100, high=100, low=98, close=98.5, volume=100, is_closed=True)
    rows[-1] = candle(14, 98)
    blocked = SetupEngine().detect_setup_b_breakout_retest(regime(MarketRegime.BEAR), rows, [ZONE])
    assert blocked is None
    enabled = SetupEngine(enable_setup_b_short=True).detect_setup_b_breakout_retest(regime(MarketRegime.BEAR), rows, [ZONE])
    assert enabled.direction == TradeDirection.SHORT


def test_disabled_setup_b_short_falls_through_to_setup_c_long_and_enabled_b_keeps_priority(monkeypatch):
    rows_15m = [candle(i, 102) for i in range(15)]
    rows_15m[-9] = candle(6, 98)
    rows_15m[-2] = Candle(timestamp=13 * 900_000, open=100, high=100, low=98, close=98.5, volume=100, is_closed=True)
    rows_15m[-1] = candle(14, 98)
    support = ConfluenceZone(level_type="SUPPORT", price_min=89, price_max=91, center=90, strength=2)
    location = LocationResult(
        quality=LocationQuality.STRONG_LONG_LOCATION,
        current_price=90,
        nearest_support=support,
        distance_to_support_pct=0,
    )
    struct = MarketStructure(timeframe="1h", structure=StructureType.MIXED)
    rows_5m = [candle(i, 90) for i in range(25)]

    disabled = SetupEngine()
    monkeypatch.setattr(disabled, "calculate_bollinger_bands", lambda *args: (100, 109, 91))
    monkeypatch.setattr(disabled, "calculate_rsi_quick", lambda *args: 31)
    selected = disabled.evaluate_setups(
        regime(MarketRegime.BEAR), struct, rows_15m, rows_5m, location, [ZONE]
    )
    assert selected.setup_type == SetupType.COUNTER_TREND_REACTION
    assert selected.direction == TradeDirection.LONG

    enabled = SetupEngine(enable_setup_b_short=True)
    monkeypatch.setattr(enabled, "calculate_bollinger_bands", lambda *args: (100, 109, 91))
    monkeypatch.setattr(enabled, "calculate_rsi_quick", lambda *args: 31)
    selected = enabled.evaluate_setups(
        regime(MarketRegime.BEAR), struct, rows_15m, rows_5m, location, [ZONE]
    )
    assert selected.setup_type == SetupType.BREAKOUT_RETEST
    assert selected.direction == TradeDirection.SHORT


@pytest.mark.parametrize("market,direction,price,support,resistance,rsi", [
    (MarketRegime.BEAR, TradeDirection.LONG, 90, ConfluenceZone(level_type="SUPPORT", price_min=89, price_max=91, center=90, strength=2), None, 20),
    (MarketRegime.BULL, TradeDirection.SHORT, 110, None, ConfluenceZone(level_type="RESISTANCE", price_min=109, price_max=111, center=110, strength=2), 80),
])
def test_setup_c_is_symmetric(monkeypatch, market, direction, price, support, resistance, rsi):
    engine = SetupEngine(enable_setup_c_short=direction == TradeDirection.SHORT)
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


def test_setup_c_long_preserves_rsi_below_32_behavior(monkeypatch):
    engine = SetupEngine(counter_trend_rsi_oversold=32.0)
    monkeypatch.setattr(engine, "calculate_bollinger_bands", lambda *args: (100, 109, 91))
    monkeypatch.setattr(engine, "calculate_rsi_quick", lambda *args: 31.0)
    support = ConfluenceZone(level_type="SUPPORT", price_min=89, price_max=91, center=90, strength=2)
    location = LocationResult(quality=LocationQuality.STRONG_LONG_LOCATION, current_price=90,
                              nearest_support=support, distance_to_support_pct=0)
    result = engine.detect_setup_c_counter_trend(regime(MarketRegime.BEAR), [candle(i, 90) for i in range(25)], location)
    assert result.direction == TradeDirection.LONG


def test_setup_c_short_is_disabled_by_default_and_explicitly_enabled(monkeypatch):
    resistance = ConfluenceZone(level_type="RESISTANCE", price_min=109, price_max=111, center=110, strength=2)
    location = LocationResult(quality=LocationQuality.STRONG_SHORT_LOCATION, current_price=110,
                              nearest_resistance=resistance, distance_to_resistance_pct=0)
    rows = [candle(i, 110) for i in range(25)]
    def configure(engine):
        monkeypatch.setattr(engine, "calculate_bollinger_bands", lambda *args: (100, 109, 91))
        monkeypatch.setattr(engine, "calculate_rsi_quick", lambda *args: 80)
        return engine
    blocked = configure(SetupEngine()).detect_setup_c_counter_trend(regime(MarketRegime.BULL), rows, location)
    assert blocked.setup_type == SetupType.NONE
    assert "EXPERIMENTAL_SETUP_DISABLED" in blocked.reason
    enabled = configure(SetupEngine(enable_setup_c_short=True)).detect_setup_c_counter_trend(regime(MarketRegime.BULL), rows, location)
    assert enabled.direction == TradeDirection.SHORT


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


def test_stale_oi_change_cannot_create_countertrend_reject():
    result = DerivativesEngine().evaluate_derivatives(
        TradeDirection.LONG, SetupType.COUNTER_TREND_REACTION, -0.01,
        oi_field=dfield(100), oi_change_field=DerivativesField(value=0.02, source=DataSource.BINANCE, observed_at=1, is_stale=True),
        funding_field=dfield(0), ls_field=dfield(1), taker_field=dfield(0.7),
    )
    assert result.status != DerivativesStatus.REJECT
