"""CoinMarketCap API client for macro context with zero synthetic dominance fallback."""

from typing import Optional, Dict, Any
import requests
from loguru import logger
from config.constants import DataSource


class CoinMarketCapClient:
    """
    Client for CoinMarketCap API.
    Strict policy: Never synthesizes fake dominance or market cap data when offline.
    """

    BASE_URL = "https://pro-api.coinmarketcap.com/v1"

    def __init__(self, api_key: Optional[str] = None, timeout: int = 5):
        self.api_key = api_key
        self.timeout = timeout
        self.session = requests.Session()
        if api_key:
            self.session.headers.update({"X-CMC_PRO_API_KEY": api_key})

    def get_global_metrics(self) -> Dict[str, Any]:
        """Fetches global cryptocurrency market metrics. Returns unavailable if no key or failure."""
        if not self.api_key:
            return {
                "source": DataSource.UNAVAILABLE,
                "is_available": False,
                "btc_dominance": None,
                "total_market_cap_usd": None,
                "total_volume_24h_usd": None,
            }

        try:
            url = f"{self.BASE_URL}/global-metrics/quotes/latest"
            resp = self.session.get(url, timeout=self.timeout)
            resp.raise_for_status()
            data = resp.json().get("data", {})
            return {
                "source": DataSource.COINMARKETCAP,
                "is_available": True,
                "btc_dominance": float(data.get("btc_dominance", 0.0)),
                "total_market_cap_usd": float(data.get("quote", {}).get("USD", {}).get("total_market_cap", 0.0)),
                "total_volume_24h_usd": float(data.get("quote", {}).get("USD", {}).get("total_volume_24h", 0.0)),
            }
        except Exception as e:
            logger.warning(f"CoinMarketCap metrics fetch failed: {e}")
            return {
                "source": DataSource.UNAVAILABLE,
                "is_available": False,
                "btc_dominance": None,
                "total_market_cap_usd": None,
                "total_volume_24h_usd": None,
            }
