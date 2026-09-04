"""Historical multi-timeframe data loader and synthetic market generator."""

import os
import time
from typing import Dict, List, Optional
import numpy as np
from loguru import logger

from core.models import Candle
from data.binance_client import BinanceFuturesClient


class HistoricalDataLoader:
    """
    Loads historical multi-timeframe candles from Binance Futures API
    or generates deterministic synthetic market datasets for zero-lookahead testing.
    """

    def __init__(self, client: Optional[BinanceFuturesClient] = None):
        self.client = client or BinanceFuturesClient()

    def fetch_binance_history(
        self,
        symbol: str = "BTCUSDT",
        limit_4h: int = 300,
        limit_1h: int = 500,
        limit_15m: int = 800,
        limit_5m: int = 1000,
    ) -> Dict[str, List[Candle]]:
        """Fetches historical multi-timeframe candles directly from Binance Futures."""
        logger.info(f"Fetching historical klines for {symbol} from Binance Futures...")
        candles_4h = self.client.get_klines(symbol, "4h", limit=limit_4h)
        candles_1h = self.client.get_klines(symbol, "1h", limit=limit_1h)
        candles_15m = self.client.get_klines(symbol, "15m", limit=limit_15m)
        candles_5m = self.client.get_klines(symbol, "5m", limit=limit_5m)

        return {
            "4h": candles_4h,
            "1h": candles_1h,
            "15m": candles_15m,
            "5m": candles_5m,
        }

    @staticmethod
    def generate_synthetic_dataset(
        num_5m_bars: int = 2000,
        initial_price: float = 65000.0,
        seed: int = 42,
    ) -> Dict[str, List[Candle]]:
        """
        Generates aligned multi-timeframe candle datasets (4H, 1H, 15M, 5M)
        with realistic trends, pullbacks, and volatility clusters.
        """
        np.random.seed(seed)
        start_ts = int(time.time() * 1000) - (num_5m_bars * 5 * 60 * 1000)

        # Generate 5M micro-steps with geometric Brownian motion + sinusoidal trend regimes
        t = np.linspace(0, 10 * np.pi, num_5m_bars)
        trend_component = np.sin(t * 0.5) * 4000.0 + np.sin(t * 2.0) * 1200.0
        random_walk = np.cumsum(np.random.normal(0, 70.0, num_5m_bars))
        prices = initial_price + trend_component + random_walk

        candles_5m: List[Candle] = []
        for i in range(num_5m_bars):
            ts = start_ts + (i * 5 * 60 * 1000)
            c = prices[i]
            prev_c = prices[i - 1] if i > 0 else initial_price
            o = prev_c
            noise_h = abs(np.random.normal(0, 40.0))
            noise_l = abs(np.random.normal(0, 40.0))
            h = max(o, c) + noise_h
            l = min(o, c) - noise_l
            vol = float(np.random.uniform(50.0, 300.0))

            candles_5m.append(
                Candle(
                    timestamp=ts,
                    open=round(float(o), 2),
                    high=round(float(h), 2),
                    low=round(float(l), 2),
                    close=round(float(c), 2),
                    volume=round(vol, 2),
                    is_closed=True,
                )
            )

        # Aggregate into 15M, 1H, 4H
        candles_15m = HistoricalDataLoader._resample_candles(candles_5m, 3, 15 * 60 * 1000)
        candles_1h = HistoricalDataLoader._resample_candles(candles_5m, 12, 60 * 60 * 1000)
        candles_4h = HistoricalDataLoader._resample_candles(candles_5m, 48, 4 * 60 * 60 * 1000)

        return {
            "5m": candles_5m,
            "15m": candles_15m,
            "1h": candles_1h,
            "4h": candles_4h,
        }

    @staticmethod
    def _resample_candles(base_candles: List[Candle], factor: int, interval_ms: int) -> List[Candle]:
        """Aggregates smaller timeframe candles into a higher timeframe."""
        resampled: List[Candle] = []
        for i in range(0, len(base_candles), factor):
            chunk = base_candles[i : i + factor]
            if not chunk:
                continue
            o = chunk[0].open
            h = max(c.high for c in chunk)
            l = min(c.low for c in chunk)
            c_close = chunk[-1].close
            vol = sum(c.volume for c in chunk)
            ts = chunk[0].timestamp

            resampled.append(
                Candle(
                    timestamp=ts,
                    open=o,
                    high=h,
                    low=l,
                    close=c_close,
                    volume=round(vol, 2),
                    is_closed=True,
                )
            )
        return resampled
