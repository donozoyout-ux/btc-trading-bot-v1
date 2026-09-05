"""Cached CoinMarketCap macro client with strict unavailable semantics."""
import time
from typing import Any, Dict, Optional
import requests
from loguru import logger
from config.constants import DataSource

class CoinMarketCapClient:
    BASE_URL = "https://pro-api.coinmarketcap.com/v1"
    def __init__(self, api_key: Optional[str] = None, timeout: int = 5, cache_seconds: int = 300, time_fn=time.time):
        self.api_key, self.timeout, self.cache_seconds, self.time_fn = api_key, timeout, cache_seconds, time_fn
        self.session, self._cache, self._connected_logged = requests.Session(), None, False
        if api_key: self.session.headers.update({"X-CMC_PRO_API_KEY": api_key})
    @property
    def configured(self): return bool(self.api_key)
    def _unavailable(self):
        return {"source": DataSource.UNAVAILABLE, "status": "UNAVAILABLE", "is_available": False, "observed_at": None, "btc_dominance": None, "total_market_cap_usd": None, "total_volume_24h_usd": None}
    @staticmethod
    def _number(value):
        if value in (None, ""): return None
        try: return float(value)
        except (TypeError, ValueError): return None
    def get_global_metrics(self) -> Dict[str, Any]:
        if not self.configured: return self._unavailable()
        now = self.time_fn()
        if self._cache and now-self._cache[0] < self.cache_seconds: return dict(self._cache[1])
        try:
            response = self.session.get(f"{self.BASE_URL}/global-metrics/quotes/latest", timeout=self.timeout); response.raise_for_status(); data = response.json().get("data") or {}; usd = (data.get("quote") or {}).get("USD") or {}
            values = {"btc_dominance": self._number(data.get("btc_dominance")), "total_market_cap_usd": self._number(usd.get("total_market_cap")), "total_volume_24h_usd": self._number(usd.get("total_volume_24h"))}
            result = self._unavailable() if any(v is None for v in values.values()) else {"source": DataSource.COINMARKETCAP, "status": "CONNECTED", "is_available": True, "observed_at": int(now*1000), **values}
            if result["is_available"] and not self._connected_logged: logger.info("COINMARKETCAP: CONNECTED"); self._connected_logged = True
        except Exception as exc:
            logger.warning("CoinMarketCap metrics fetch failed: {}", type(exc).__name__); result = self._unavailable()
        self._cache = (now, dict(result)); return result
