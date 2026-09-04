"""5M Entry Trigger Engine implementing the 6-state machine and multi-factor candle triggers."""

from typing import List, Tuple
import numpy as np
from core.models import Candle, SetupSignal, TriggerResult
from config.constants import TriggerState, TradeDirection, SetupType


class EntryTriggerEngine:
    """
    Manages the 5M Entry Trigger state machine:
    NO_SETUP -> WATCH -> SETUP_DETECTED -> WAITING_TRIGGER -> ENTRY_READY -> IN_POSITION
    Requires multi-factor candle behavior (rejection wick, engulfing, micro BOS, volume).
    """

    def __init__(self, min_wick_ratio: float = 0.35, min_body_ratio: float = 0.65):
        self.min_wick_ratio = min_wick_ratio
        self.min_body_ratio = min_body_ratio

    def evaluate_5m_patterns(self, candles_5m: List[Candle], direction: TradeDirection) -> Tuple[bool, str]:
        """
        Analyzes the latest closed 5M candles for multi-factor trigger confluence.
        Returns (is_confirmed, pattern_description).
        """
        if len(candles_5m) < 5:
            return False, "Insufficient 5M candles"

        curr = candles_5m[-1]
        prev = candles_5m[-2]

        total_range = curr.total_range
        lower_wick_ratio = curr.lower_wick / total_range
        upper_wick_ratio = curr.upper_wick / total_range
        body_ratio = curr.body_size / total_range

        # Volume confirmation (above recent 5M average)
        volumes = [c.volume for c in candles_5m[-21:-1]]
        avg_vol = np.mean(volumes) if volumes else 1.0
        vol_confirmed = curr.volume >= avg_vol * 0.95

        confluence_points = 0
        patterns_found = []

        if direction == TradeDirection.LONG:
            # Factor 1: Lower wick rejection
            if lower_wick_ratio >= self.min_wick_ratio and curr.close >= curr.open:
                confluence_points += 1
                patterns_found.append(f"Wick Rejection ({lower_wick_ratio*100:.0f}%)")

            # Factor 2: Bullish Engulfing
            if curr.is_bullish and prev.is_bearish and curr.close > prev.high and curr.open <= prev.close:
                confluence_points += 1
                patterns_found.append("Bullish Engulfing")

            # Factor 3: Strong Directional Body
            if curr.is_bullish and body_ratio >= self.min_body_ratio:
                confluence_points += 1
                patterns_found.append(f"Strong Bullish Body ({body_ratio*100:.0f}%)")

            # Factor 4: 5M Micro Structure Break (Close above recent 3 bars high)
            recent_high = max(c.high for c in candles_5m[-4:-1])
            if curr.close > recent_high:
                confluence_points += 1
                patterns_found.append("Micro BOS High Break")

            # Factor 5: Volume confirmation
            if vol_confirmed:
                confluence_points += 1
                patterns_found.append("Volume Expansion")

            # Quality trigger requires at least 2 confluence factors per Section 28
            is_triggered = confluence_points >= 2
            return is_triggered, " + ".join(patterns_found)

        elif direction == TradeDirection.SHORT:
            # Factor 1: Upper wick rejection
            if upper_wick_ratio >= self.min_wick_ratio and curr.close <= curr.open:
                confluence_points += 1
                patterns_found.append(f"Upper Wick Rejection ({upper_wick_ratio*100:.0f}%)")

            # Factor 2: Bearish Engulfing
            if curr.is_bearish and prev.is_bullish and curr.close < prev.low and curr.open >= prev.close:
                confluence_points += 1
                patterns_found.append("Bearish Engulfing")

            # Factor 3: Strong Bearish Body
            if curr.is_bearish and body_ratio >= self.min_body_ratio:
                confluence_points += 1
                patterns_found.append(f"Strong Bearish Body ({body_ratio*100:.0f}%)")

            # Factor 4: Micro Structure Break (Close below recent 3 bars low)
            recent_low = min(c.low for c in candles_5m[-4:-1])
            if curr.close < recent_low:
                confluence_points += 1
                patterns_found.append("Micro BOS Low Break")

            # Factor 5: Volume confirmation
            if vol_confirmed:
                confluence_points += 1
                patterns_found.append("Volume Expansion")

            is_triggered = confluence_points >= 2
            return is_triggered, " + ".join(patterns_found)

        return False, "No valid trade direction"

    def process_trigger(
        self,
        current_state: TriggerState,
        setup: SetupSignal,
        candles_5m: List[Candle],
        is_in_position: bool,
    ) -> TriggerResult:
        """
        Executes state machine transitions and produces TriggerResult.
        """
        if is_in_position:
            return TriggerResult(
                state=TriggerState.IN_POSITION,
                is_triggered=False,
                direction=TradeDirection.WAIT,
                reason="Already in active position",
            )

        if not setup.detected or setup.direction == TradeDirection.WAIT:
            return TriggerResult(
                state=TriggerState.NO_SETUP,
                is_triggered=False,
                direction=TradeDirection.WAIT,
                reason="No setup active to trigger",
            )

        # Setup is detected -> transition to WAITING_TRIGGER
        is_pattern_confirmed, pattern_desc = self.evaluate_5m_patterns(candles_5m, setup.direction)

        if is_pattern_confirmed:
            curr_c = candles_5m[-1]
            return TriggerResult(
                state=TriggerState.ENTRY_READY,
                is_triggered=True,
                direction=setup.direction,
                pattern=pattern_desc,
                trigger_price=curr_c.close,
                reason=f"5M Entry trigger confirmed: {pattern_desc}",
            )
        else:
            return TriggerResult(
                state=TriggerState.WAITING_TRIGGER,
                is_triggered=False,
                direction=setup.direction,
                reason=f"Setup {setup.setup_type.value} active; waiting for 5M candle trigger confirmation",
            )
