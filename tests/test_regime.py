"""Unit tests for Market Regime Engine and scoring formulas."""

import pytest
from core.models import Candle, MarketStructure
from config.constants import MarketRegime, StructureType
from engines.regime_engine import MarketRegimeEngine


def generate_trending_4h_candles(count=220, start_price=30000.0, step=150.0):
    """Generates strong upward trending 4H candles."""
    candles = []
    curr = start_price
    for i in range(count):
        curr += step
        candles.append(
            Candle(
                timestamp=100000 + i * (4 * 3600 * 1000),
                open=curr - 50.0,
                high=curr + 50.0,
                low=curr - 60.0,
                close=curr,
                volume=500.0,
                is_closed=True,
            )
        )
    return candles


def test_regime_scoring_strong_bull():
    engine = MarketRegimeEngine()
    candles_4h = generate_trending_4h_candles(count=220, start_price=30000.0, step=200.0)

    struct_4h = MarketStructure(timeframe="4h", structure=StructureType.BULLISH)
    struct_1h = MarketStructure(timeframe="1h", structure=StructureType.BULLISH)

    result = engine.evaluate_regime(candles_4h, struct_4h, struct_1h)

    assert result.regime in [MarketRegime.STRONG_BULL, MarketRegime.BULL]
    assert result.score >= 30.0
    assert result.confidence in ["HIGH", "MEDIUM"]
    assert "slope_score" in result.details
    assert "ema_score" in result.details


def test_regime_overextended_up():
    engine = MarketRegimeEngine()
    candles_4h = generate_trending_4h_candles(count=220, start_price=30000.0, step=100.0)
    # Huge spike on the last candle
    last = candles_4h[-1]
    last.close = last.close + 10000.0
    last.high = last.close + 200.0

    struct_4h = MarketStructure(timeframe="4h", structure=StructureType.BULLISH)
    struct_1h = MarketStructure(timeframe="1h", structure=StructureType.BULLISH)

    result = engine.evaluate_regime(candles_4h, struct_4h, struct_1h)
    assert result.overextended_up is True
