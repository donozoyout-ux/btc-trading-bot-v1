"""Data Health Engine ensuring pristine input data and source-level degradation handling."""

import math
from typing import List, Tuple, Dict, Optional
from loguru import logger

from core.models import Candle, DataHealthResult
from config.constants import DataSafetyStatus, SourceHealthStatus


class DataHealthEngine:
    """
    Validates data integrity across all timeframes and data sources.
    Distinguishes critical Binance candle failures (UNSAFE -> NO TRADE)
    from auxiliary external source degradations (CoinGlass/CMC -> DEGRADED/OFFLINE).
    """

    TIMEFRAME_INTERVAL_MS = {
        "5m": 5 * 60 * 1000,
        "15m": 15 * 60 * 1000,
        "1h": 60 * 60 * 1000,
        "4h": 4 * 60 * 60 * 1000,
    }

    MIN_REQUIRED_CANDLES = {
        "5m": 35,
        "15m": 35,
        "1h": 35,
        "4h": 45,
    }

    def validate_candles(self, timeframe: str, candles: List[Candle]) -> Tuple[DataSafetyStatus, str]:
        """
        Validates a single timeframe candle series for critical errors.
        Returns (SAFE, "") or (UNSAFE, failure_reason).
        """
        if not candles:
            return DataSafetyStatus.UNSAFE, f"No candle data available for timeframe {timeframe}"

        min_required = self.MIN_REQUIRED_CANDLES.get(timeframe, 30)
        if len(candles) < min_required:
            return DataSafetyStatus.UNSAFE, f"Insufficient candles for {timeframe}: {len(candles)} < {min_required}"

        expected_interval = self.TIMEFRAME_INTERVAL_MS.get(timeframe)
        seen_timestamps = set()

        for i, c in enumerate(candles):
            # Check 1: Must be strictly closed
            if not c.is_closed:
                return DataSafetyStatus.UNSAFE, f"Open candle detected at index {i} in {timeframe}"

            # Check 2: Finite numeric checks
            for val_name, val in [("open", c.open), ("high", c.high), ("low", c.low), ("close", c.close), ("volume", c.volume)]:
                if math.isnan(val) or math.isinf(val) or val < 0:
                    return DataSafetyStatus.UNSAFE, f"Invalid {val_name}={val} in {timeframe} at ts={c.timestamp}"

            # Check 3: High/Low sanity
            if c.low > c.high or c.open > c.high or c.close > c.high or c.open < c.low or c.close < c.low:
                return DataSafetyStatus.UNSAFE, f"Inconsistent OHLC bar at ts={c.timestamp} in {timeframe}"

            # Check 4: Duplicate timestamps
            if c.timestamp in seen_timestamps:
                return DataSafetyStatus.UNSAFE, f"Duplicate candle timestamp {c.timestamp} in {timeframe}"
            seen_timestamps.add(c.timestamp)

            # Check 5: Monotonic timestamps and interval check
            if i > 0:
                prev = candles[i - 1]
                delta = c.timestamp - prev.timestamp
                if delta <= 0:
                    return DataSafetyStatus.UNSAFE, f"Non-increasing timestamp at index {i} in {timeframe}"
                if expected_interval and delta != expected_interval:
                    if delta > expected_interval * 3:
                        return DataSafetyStatus.UNSAFE, f"Missing bars in {timeframe}: gap of {delta}ms between {prev.timestamp} and {c.timestamp}"

        return DataSafetyStatus.SAFE, "Data healthy"

    def evaluate_health(
        self,
        candle_dict: Dict[str, List[Candle]],
        coinglass_available: bool = True,
        cmc_available: bool = True,
        binance_latency_ms: int = 150,
    ) -> DataHealthResult:
        """
        Synthesizes overall safety status and per-source health states.
        Critical Binance failures force overall_safety = UNSAFE.
        CoinGlass or CMC failures mark auxiliary status without halting overall trading.
        """
        source_health: Dict[str, SourceHealthStatus] = {}
        details: Dict[str, str] = {}

        # 1. Binance Critical Candle Evaluation
        binance_safe = True
        binance_reason = "Binance feed healthy"

        if binance_latency_ms > 3000:
            source_health["BINANCE"] = SourceHealthStatus.DEGRADED
            details["BINANCE"] = f"Elevated latency: {binance_latency_ms}ms"
        else:
            source_health["BINANCE"] = SourceHealthStatus.HEALTHY

        for tf in ["4h", "1h", "15m", "5m"]:
            candles = candle_dict.get(tf, [])
            status, reason = self.validate_candles(tf, candles)
            if status == DataSafetyStatus.UNSAFE:
                binance_safe = False
                binance_reason = f"{tf}: {reason}"
                source_health["BINANCE"] = SourceHealthStatus.STALE
                details["BINANCE"] = binance_reason
                break

        # 2. CoinGlass Auxiliary Evaluation
        if coinglass_available:
            source_health["COINGLASS"] = SourceHealthStatus.HEALTHY
            details["COINGLASS"] = "CoinGlass feed healthy"
        else:
            source_health["COINGLASS"] = SourceHealthStatus.OFFLINE
            details["COINGLASS"] = "CoinGlass unavailable (degraded to native Binance metrics)"

        # 3. CoinMarketCap Auxiliary Evaluation
        if cmc_available:
            source_health["COINMARKETCAP"] = SourceHealthStatus.HEALTHY
            details["COINMARKETCAP"] = "CoinMarketCap feed healthy"
        else:
            source_health["COINMARKETCAP"] = SourceHealthStatus.OFFLINE
            details["COINMARKETCAP"] = "CoinMarketCap unavailable (global context marked UNAVAILABLE)"

        overall_safety = DataSafetyStatus.SAFE if binance_safe else DataSafetyStatus.UNSAFE
        overall_reason = binance_reason if not binance_safe else "All critical feeds safe"

        return DataHealthResult(
            overall_safety=overall_safety,
            source_health=source_health,
            details=details,
            reason=overall_reason,
        )

    def check_all_timeframes(self, candle_dict: Dict[str, List[Candle]]) -> Tuple[DataSafetyStatus, str]:
        """Convenience method returning overall safety and reason."""
        result = self.evaluate_health(candle_dict)
        return result.overall_safety, result.reason
