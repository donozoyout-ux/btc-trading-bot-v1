"""Unit tests for Market Structure Engine and zero-lookahead verification."""

import pytest
from core.models import Candle
from config.constants import StructureType
from engines.structure_engine import MarketStructureEngine


def test_swing_point_detection_zero_lookahead():
    """
    Verifies that a swing point at index i is detected only when i + 2 < len(candles).
    """
    engine = MarketStructureEngine(left_bars=2, right_bars=2)
    candles = []
    # Build candle series with a peak at index 5
    highs = [100, 102, 105, 108, 110, 120, 112, 107, 104, 101]
    for i, h in enumerate(highs):
        candles.append(
            Candle(
                timestamp=1000 + i * 3600,
                open=h - 5,
                high=h,
                low=h - 8,
                close=h - 2,
                volume=100.0,
                is_closed=True,
            )
        )

    # When series only has up to index 6 (i.e. only 1 bar after peak at 5):
    sh_early, _ = engine.find_confirmed_swings(candles[:7])
    # Peak at index 5 requires index 5 + 2 = 7 to be present!
    assert not any(s.candle_index == 5 for s in sh_early)

    # When index 7 is included (2 bars after peak):
    sh_confirmed, _ = engine.find_confirmed_swings(candles[:8])
    assert any(s.candle_index == 5 and s.price == 120 for s in sh_confirmed)


def test_bullish_structure_and_bos():
    engine = MarketStructureEngine(left_bars=2, right_bars=2)
    # Construct sequence with HH + HL
    candles = []
    prices = [
        # Valley 1 (SL1 at idx 2: 90)
        100, 95, 90, 95, 100,
        # Peak 1 (SH1 at idx 6: 120)
        110, 120, 115, 110,
        # Valley 2 (SL2 at idx 10: 105 -> Higher Low)
        108, 105, 112, 118,
        # Peak 2 (SH2 at idx 14: 135 -> Higher High)
        125, 135, 130, 125,
        # BOS break bar
        128, 138
    ]
    for i, p in enumerate(prices):
        candles.append(
            Candle(
                timestamp=1000 + i * 3600,
                open=p - 2,
                high=p + 2,
                low=p - 3,
                close=p,
                volume=100.0,
                is_closed=True,
            )
        )

    ms = engine.analyze_structure("1h", candles)
    assert ms.structure == StructureType.BULLISH
    assert ms.recent_hh is True
    assert ms.recent_hl is True
    assert ms.last_bos == "BULLISH_BOS"
