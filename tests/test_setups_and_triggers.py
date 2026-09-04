"""Unit tests for Setup Detection and 5M Entry Trigger Engines."""

import pytest
from core.models import Candle, RegimeResult, MarketStructure, LocationResult, ConfluenceZone
from config.constants import (
    MarketRegime,
    StructureType,
    TradeDirection,
    SetupType,
    LocationQuality,
    TriggerState,
    VolatilityLevel,
)
from engines.setup_engine import SetupEngine
from engines.trigger_engine import EntryTriggerEngine


def test_setup_a_trend_pullback_detection():
    engine = SetupEngine()

    regime = RegimeResult(
        regime=MarketRegime.BULL,
        score=50.0,
        confidence="HIGH",
        volatility=VolatilityLevel.NORMAL,
        overextended_up=False,
    )
    struct_1h = MarketStructure(timeframe="1h", structure=StructureType.BULLISH)

    # 15M candles pulling back
    candles_15m = []
    for i in range(15):
        candles_15m.append(
            Candle(
                timestamp=1000 + i * 900000,
                open=65000 - i * 30,
                high=65020 - i * 30,
                low=64950 - i * 30,
                close=64970 - i * 30,
                volume=100 - i * 2,  # Declining volume
                is_closed=True,
            )
        )

    sup_zone = ConfluenceZone(
        level_type="SUPPORT",
        price_min=64500,
        price_max=64600,
        center=64550,
        strength=3,
        sources=["1H Swing Low", "Fib 0.618", "PDL"],
    )

    location = LocationResult(
        quality=LocationQuality.STRONG_LONG_LOCATION,
        current_price=64580,
        nearest_support=sup_zone,
        distance_to_support_pct=0.002,
    )

    setup = engine.detect_setup_a_trend_pullback(regime, struct_1h, candles_15m, location)
    assert setup is not None
    assert setup.detected is True
    assert setup.setup_type == SetupType.TREND_PULLBACK
    assert setup.direction == TradeDirection.LONG


def test_5m_trigger_wick_rejection_and_micro_bos():
    trigger_engine = EntryTriggerEngine()

    # Build 5M candles: prior bars consolidating, latest bar with strong lower wick + close above recent high
    candles_5m = []
    for i in range(10):
        candles_5m.append(
            Candle(
                timestamp=1000 + i * 300000,
                open=64500,
                high=64520,
                low=64480,
                close=64510,
                volume=50.0,
                is_closed=True,
            )
        )

    # Latest trigger candle: dips to 64400, aggressively rejects up to 64550 (close > recent highs)
    candles_5m.append(
        Candle(
            timestamp=1000 + 10 * 300000,
            open=64490,
            high=64560,
            low=64400,
            close=64550,
            volume=200.0,  # High volume expansion
            is_closed=True,
        )
    )

    is_confirmed, pattern = trigger_engine.evaluate_5m_patterns(candles_5m, TradeDirection.LONG)
    assert is_confirmed is True
    assert "Wick Rejection" in pattern or "Micro BOS" in pattern
