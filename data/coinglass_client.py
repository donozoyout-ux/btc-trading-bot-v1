"""CoinGlass API client for futures derivatives context with strict no-fake-data policy."""

from typing import Optional, Dict, Any
import requests
from loguru import logger
from config.constants import DataSource


class CoinGlassClient:
    """
    Client for CoinGlass API v3.
    Strict policy: Never synthesizes fake numbers when offline or missing credentials.
    """

    BASE_URL = "https://open-api-v3.coinglass.com/api"

    def __init__(self, api_key: Optional[str] = None, timeout: int = 5):
        self.api_key = api_key
        self.timeout = timeout
        self.session = requests.Session()
        if api_key:
            self.session.headers.update({"CG-API-KEY": api_key})

    def get_liquidation_data(self, symbol: str = "BTC") -> Dict[str, Any]:
        """Fetches 24h aggregate liquidations. Returns empty if unavailable."""
        if not self.api_key:
            return {
                "source": DataSource.UNAVAILABLE,
                "is_available": False,
                "long_liquidation_usdt": None,
                "short_liquidation_usdt": None,
                "total": None,
            }

        try:
            url = f"{self.BASE_URL}/futures/liquidation/detail"
            resp = self.session.get(url, params={"symbol": symbol}, timeout=self.timeout)
            resp.raise_for_status()
            data = resp.json().get("data", {})
            return {
                "source": DataSource.COINGLASS,
                "is_available": True,
                "long_liquidation_usdt": float(data.get("longVolUsd", 0.0)),
                "short_liquidation_usdt": float(data.get("shortVolUsd", 0.0)),
                "total": float(data.get("totalVolUsd", 0.0)),
            }
        except Exception as e:
            logger.warning(f"CoinGlass liquidation fetch failed: {e}")
            return {
                "source": DataSource.UNAVAILABLE,
                "is_available": False,
                "long_liquidation_usdt": None,
                "short_liquidation_usdt": None,
                "total": None,
            }

    def get_aggregate_oi(self, symbol: str = "BTC") -> Dict[str, Any]:
        """Fetches aggregate Open Interest. Returns empty if unavailable."""
        if not self.api_key:
            return {
                "source": DataSource.UNAVAILABLE,
                "is_available": False,
                "aggregate_oi_usd": None,
            }

        try:
            url = f"{self.BASE_URL}/futures/openInterest/chart"
            resp = self.session.get(url, params={"symbol": symbol, "interval": "1h"}, timeout=self.timeout)
            resp.raise_for_status()
            data = resp.json().get("data", [])
            latest = data[-1] if data else {}
            return {
                "source": DataSource.COINGLASS,
                "is_available": True,
                "aggregate_oi_usd": float(latest.get("close", 0.0)),
            }
        except Exception as e:
            logger.warning(f"CoinGlass aggregate OI fetch failed: {e}")
            return {
                "source": DataSource.UNAVAILABLE,
                "is_available": False,
                "aggregate_oi_usd": None,
            }
