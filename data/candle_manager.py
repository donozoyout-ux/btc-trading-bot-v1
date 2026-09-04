"""Multi-timeframe candle manager that maintains strictly closed historical candles."""

from typing import Dict, List, Optional
from collections import deque
from core.models import Candle


class CandleManager:
    """
    Maintains clean, chronological buffers of closed candles across multiple timeframes.
    Guarantees no open candle leakage into analytical engines.
    """

    TIMEFRAME_MS = {
        "5m": 5 * 60 * 1000,
        "15m": 15 * 60 * 1000,
        "1h": 60 * 60 * 1000,
        "4h": 4 * 60 * 60 * 1000,
        "1d": 24 * 60 * 60 * 1000,
    }

    def __init__(self, max_buffer_size: int = 1000):
        self.max_buffer_size = max_buffer_size
        self._buffers: Dict[str, deque[Candle]] = {
            "5m": deque(maxlen=max_buffer_size),
            "15m": deque(maxlen=max_buffer_size),
            "1h": deque(maxlen=max_buffer_size),
            "4h": deque(maxlen=max_buffer_size),
            "1d": deque(maxlen=max_buffer_size),
        }

    def add_candle(self, timeframe: str, candle: Candle) -> bool:
        """
        Adds a candle to the specified timeframe buffer if valid.
        Ensures strict chronological ordering and no duplicates.
        """
        if timeframe not in self._buffers:
            self._buffers[timeframe] = deque(maxlen=self.max_buffer_size)

        buf = self._buffers[timeframe]

        if not candle.is_closed:
            # Reject incomplete candle
            return False

        if len(buf) > 0:
            last = buf[-1]
            if candle.timestamp < last.timestamp:
                # Out of order candle, reject or ignore
                return False
            if candle.timestamp == last.timestamp:
                # Update existing candle with confirmed close
                buf[-1] = candle
                return True

        buf.append(candle)
        return True

    def load_candles(self, timeframe: str, candles: List[Candle]) -> None:
        """Bulk loads candles into the timeframe buffer, sorted by timestamp."""
        if timeframe not in self._buffers:
            self._buffers[timeframe] = deque(maxlen=self.max_buffer_size)

        # Sort and deduplicate
        sorted_candles = sorted([c for c in candles if c.is_closed], key=lambda x: x.timestamp)
        deduped: List[Candle] = []
        seen = set()
        for c in sorted_candles:
            if c.timestamp not in seen:
                seen.add(c.timestamp)
                deduped.append(c)

        self._buffers[timeframe] = deque(deduped[-self.max_buffer_size:], maxlen=self.max_buffer_size)

    def get_candles(self, timeframe: str, count: Optional[int] = None) -> List[Candle]:
        """Returns the most recent N closed candles."""
        if timeframe not in self._buffers:
            return []
        buf = list(self._buffers[timeframe])
        if count is None or count >= len(buf):
            return buf
        return buf[-count:]

    def get_latest_closed(self, timeframe: str) -> Optional[Candle]:
        """Returns the latest closed candle for the specified timeframe."""
        if timeframe in self._buffers and len(self._buffers[timeframe]) > 0:
            return self._buffers[timeframe][-1]
        return None

    def candle_count(self, timeframe: str) -> int:
        """Returns number of closed candles buffered for timeframe."""
        return len(self._buffers.get(timeframe, []))
