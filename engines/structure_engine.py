"""Market Structure Engine identifying Swing Points with dual timestamps, BOS, and CHoCH."""

from typing import List, Optional, Tuple
from core.models import Candle, SwingPoint, MarketStructure
from config.constants import StructureType


class MarketStructureEngine:
    """
    Identifies market swings using 2 bars left and 2 bars right.
    Strictly lookahead-free:
    - swing_time: open timestamp of swing candle t
    - confirmed_at: exact close timestamp of 2nd right confirmation bar (t + right_bars)
    When current_time < confirmed_at, the swing point is completely hidden.
    """

    TIMEFRAME_INTERVAL_MS = {
        "5m": 5 * 60 * 1000,
        "15m": 15 * 60 * 1000,
        "1h": 60 * 60 * 1000,
        "4h": 4 * 60 * 60 * 1000,
        "1d": 24 * 60 * 60 * 1000,
    }

    def __init__(self, left_bars: int = 2, right_bars: int = 2):
        self.left_bars = left_bars
        self.right_bars = right_bars

    def get_candle_interval_ms(self, candles: List[Candle], timeframe: Optional[str] = None) -> int:
        """Determines the duration of one candle in milliseconds."""
        if timeframe and timeframe in self.TIMEFRAME_INTERVAL_MS:
            return self.TIMEFRAME_INTERVAL_MS[timeframe]
        if len(candles) >= 2:
            return candles[1].timestamp - candles[0].timestamp
        return 5 * 60 * 1000  # 5m default

    def find_confirmed_swings(
        self,
        candles: List[Candle],
        timeframe: Optional[str] = None,
        as_of_time: Optional[int] = None,
    ) -> Tuple[List[SwingPoint], List[SwingPoint]]:
        """
        Finds all confirmed swing highs and lows up to the latest closed candle.
        Ensures dual-timestamp tracking (swing_time, confirmed_at).
        If as_of_time is provided, filters out any swing where confirmed_at > as_of_time.
        """
        n = len(candles)
        swing_highs: List[SwingPoint] = []
        swing_lows: List[SwingPoint] = []

        if n < (self.left_bars + self.right_bars + 1):
            return swing_highs, swing_lows

        interval_ms = self.get_candle_interval_ms(candles, timeframe)
        last_evaluable_idx = n - 1 - self.right_bars

        for i in range(self.left_bars, last_evaluable_idx + 1):
            curr_c = candles[i]
            confirming_bar = candles[i + self.right_bars]
            confirmed_at = confirming_bar.timestamp + interval_ms

            # Enforce as_of_time ceiling if provided
            if as_of_time is not None and confirmed_at > as_of_time:
                continue

            # Check Swing High
            is_high = True
            for offset in range(1, self.left_bars + 1):
                if candles[i - offset].high >= curr_c.high:
                    is_high = False
                    break
            if is_high:
                for offset in range(1, self.right_bars + 1):
                    if candles[i + offset].high >= curr_c.high:
                        is_high = False
                        break

            if is_high:
                swing_highs.append(
                    SwingPoint(
                        swing_time=curr_c.timestamp,
                        confirmed_at=confirmed_at,
                        price=curr_c.high,
                        is_high=True,
                        candle_index=i,
                    )
                )

            # Check Swing Low
            is_low = True
            for offset in range(1, self.left_bars + 1):
                if candles[i - offset].low <= curr_c.low:
                    is_low = False
                    break
            if is_low:
                for offset in range(1, self.right_bars + 1):
                    if candles[i + offset].low <= curr_c.low:
                        is_low = False
                        break

            if is_low:
                swing_lows.append(
                    SwingPoint(
                        swing_time=curr_c.timestamp,
                        confirmed_at=confirmed_at,
                        price=curr_c.low,
                        is_high=False,
                        candle_index=i,
                    )
                )

        return swing_highs, swing_lows

    def analyze_structure(
        self,
        timeframe: str,
        candles: List[Candle],
        as_of_time: Optional[int] = None,
    ) -> MarketStructure:
        """
        Evaluates full market structure for the given timeframe.
        Computes HH/HL, LH/LL, BOS, and CHoCH strictly on confirmed swings.
        """
        if len(candles) < 10:
            return MarketStructure(timeframe=timeframe, structure=StructureType.MIXED)

        swing_highs, swing_lows = self.find_confirmed_swings(candles, timeframe, as_of_time)
        latest_close = candles[-1].close

        recent_hh = False
        recent_hl = False
        recent_lh = False
        recent_ll = False

        if len(swing_highs) >= 2:
            recent_hh = swing_highs[-1].price > swing_highs[-2].price
            recent_lh = swing_highs[-1].price < swing_highs[-2].price

        if len(swing_lows) >= 2:
            recent_hl = swing_lows[-1].price > swing_lows[-2].price
            recent_ll = swing_lows[-1].price < swing_lows[-2].price

        # Structure classification per Section 8
        if recent_hh and recent_hl:
            structure_type = StructureType.BULLISH
        elif recent_lh and recent_ll:
            structure_type = StructureType.BEARISH
        else:
            structure_type = StructureType.MIXED

        # Detect BOS & CHoCH
        last_bos: Optional[str] = None
        last_choch: Optional[str] = None

        if swing_highs and latest_close > swing_highs[-1].price:
            if structure_type == StructureType.BULLISH:
                last_bos = "BULLISH_BOS"
            elif structure_type == StructureType.BEARISH:
                last_choch = "BULLISH_CHOCH"

        if swing_lows and latest_close < swing_lows[-1].price:
            if structure_type == StructureType.BEARISH:
                last_bos = "BEARISH_BOS"
            elif structure_type == StructureType.BULLISH:
                last_choch = "BEARISH_CHOCH"

        return MarketStructure(
            timeframe=timeframe,
            structure=structure_type,
            swing_highs=swing_highs,
            swing_lows=swing_lows,
            last_bos=last_bos,
            last_choch=last_choch,
            recent_hh=recent_hh,
            recent_hl=recent_hl,
            recent_lh=recent_lh,
            recent_ll=recent_ll,
        )
