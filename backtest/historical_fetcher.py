"""Historical data fetcher with pagination support for multi-year Binance klines."""

import time
from typing import List, Optional, Dict
from loguru import logger

from core.models import Candle
from data.binance_client import BinanceFuturesClient


class HistoricalDataFetcher:
    """
    Fetches multi-year historical klines from Binance Futures with pagination.
    Supports batch fetching across multiple timeframes.
    """

    MAX_LIMIT = 1000
    REQUEST_DELAY_MS = 250  # To respect rate limits

    INTERVAL_MS = {
        "1m": 60_000,
        "5m": 5 * 60_000,
        "15m": 15 * 60_000,
        "1h": 60 * 60_000,
        "4h": 4 * 60 * 60_000,
    }

    def __init__(self, testnet: bool = False):
        # Production endpoint required: testnet has no multi-year history
        self.client = BinanceFuturesClient(testnet=testnet)

    def fetch_klines_paginated(
        self,
        symbol: str,
        interval: str,
        start_time: int,
        end_time: int,
    ) -> List[Candle]:
        """
        Fetch all closed klines between start_time and end_time using pagination.
        Returns only strictly closed candles.
        """
        all_candles: List[Candle] = []
        current_start = start_time
        now_ms = int(time.time() * 1000)
        interval_ms = self.INTERVAL_MS.get(interval, 5 * 60_000)

        while current_start < end_time:
            params: Dict = {
                "symbol": symbol,
                "interval": interval,
                "limit": self.MAX_LIMIT,
                "startTime": current_start,
            }
            if end_time:
                params["endTime"] = min(current_start + self.MAX_LIMIT * interval_ms, end_time)

            try:
                resp = self.client.session.get(
                    f"{self.client.base_url}/fapi/v1/klines",
                    params=params,
                    timeout=self.client.timeout,
                )
                resp.raise_for_status()
                data = resp.json()

                if not data:
                    break

                for item in data:
                    open_time = int(item[0])
                    close_time = int(item[6])
                    is_closed = now_ms > close_time

                    if is_closed:
                        candle = Candle(
                            timestamp=open_time,
                            open=float(item[1]),
                            high=float(item[2]),
                            low=float(item[3]),
                            close=float(item[4]),
                            volume=float(item[5]),
                            is_closed=True,
                        )
                        all_candles.append(candle)

                last_open = int(data[-1][0])
                next_start = last_open + interval_ms
                # Progress guard: break if API returns no forward progress
                if next_start <= current_start:
                    logger.warning(f"Pagination stalled at {current_start}, breaking")
                    break
                current_start = next_start

                # Rate limit respect
                time.sleep(self.REQUEST_DELAY_MS / 1000.0)

            except Exception as e:
                logger.error(f"Error fetching {symbol} {interval} at {current_start}: {e}")
                break

        return all_candles

    def fetch_all_timeframes(
        self,
        symbol: str = "BTCUSDT",
        start_time: Optional[int] = None,
        end_time: Optional[int] = None,
    ) -> Dict[str, List[Candle]]:
        """
        Fetch all timeframes (5m, 15m, 1h, 4h) with pagination.
        """
        now = int(time.time() * 1000)
        if end_time is None:
            end_time = now
        if start_time is None:
            end_time = now
            start_time = end_time - (3 * 365 * 24 * 60 * 60 * 1000)  # 3 years ago

        logger.info(f"Fetching historical data for {symbol} from {start_time} to {end_time}")
        logger.info(f"Estimated range: {(end_time - start_time) / (365.25 * 24 * 60 * 60 * 1000):.1f} years")

        result: Dict[str, List[Candle]] = {}

        for tf, interval in [("5m", "5m"), ("15m", "15m"), ("1h", "1h"), ("4h", "4h")]:
            logger.info(f"Fetching {tf} ({interval}) klines...")
            candles = self.fetch_klines_paginated(symbol, interval, start_time, end_time)
            result[tf] = candles
            logger.info(f"  {tf}: {len(candles)} candles")

        return result

    def get_dataset_stats(self, dataset: Dict[str, List[Candle]]) -> Dict:
        """Compute dataset quality statistics."""
        stats = {}
        for tf, candles in dataset.items():
            if not candles:
                stats[tf] = {"count": 0, "issues": ["No data"]}
                continue

            timestamps = [c.timestamp for c in candles]
            closes = [c.close for c in candles]

            # Check for issues
            issues = []
            duplicates = len(timestamps) - len(set(timestamps))
            if duplicates > 0:
                issues.append(f"{duplicates} duplicate timestamps")

            invalid_ohlc = sum(1 for c in dataset[tf] if c.low > c.high or c.open > c.high or c.close > c.high)
            if invalid_ohlc > 0:
                issues.append(f"{invalid_ohlc} invalid OHLC bars")

            non_positive = sum(1 for c in dataset[tf] if c.close <= 0 or c.open <= 0)
            if non_positive > 0:
                issues.append(f"{non_positive} non-positive prices")

            gaps = []
            for i in range(1, len(timestamps)):
                delta = timestamps[i] - timestamps[i - 1]
                if delta > 0:
                    expected = {"5m": 300000, "15m": 900000, "1h": 3600000, "4h": 14400000}.get(tf, 300000)
                    if delta > expected * 3:
                        gaps.append((timestamps[i - 1], timestamps[i]))

            if gaps:
                issues.append(f"{len(gaps)} large timestamp gaps")

            stats[tf] = {
                "count": len(candles),
                "start": timestamps[0],
                "end": timestamps[-1],
                "duplicates": duplicates,
                "invalid_ohlc": invalid_ohlc,
                "non_positive": non_positive,
                "large_gaps": len(gaps),
                "issues": issues,
            }

        return stats
