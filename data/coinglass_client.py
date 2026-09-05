"""Cached CoinGlass context client with strict unavailable semantics."""
import time
import re
from typing import Any, Dict, Optional
import requests
from loguru import logger
from config.constants import DataSource

class CoinGlassClient:
    BASE_URL = "https://open-api-v4.coinglass.com/api"
    def __init__(self, api_key: Optional[str] = None, timeout: int = 5, cache_seconds: int = 60, time_fn=time.time):
        self.api_key, self.timeout, self.cache_seconds, self.time_fn = api_key, timeout, cache_seconds, time_fn
        self.session, self._cache, self._connected_logged = requests.Session(), {}, False
        if api_key: self.session.headers.update({"CG-API-KEY": api_key})
    @property
    def configured(self): return bool(self.api_key)
    def _unavailable(self, **fields):
        return {"source": DataSource.UNAVAILABLE, "status": "UNAVAILABLE", "is_available": False, "observed_at": None, "error_category": None, **fields}
    @staticmethod
    def _error_category(exc):
        status = getattr(getattr(exc, "response", None), "status_code", None)
        return f"HTTP_{status}" if status else "NETWORK_ERROR"
    @staticmethod
    def _api_error(payload):
        code = str(payload.get("code", "0"))
        return None if code == "0" else f"API_CODE_{re.sub(r'[^A-Za-z0-9_-]', '', code)[:32] or 'UNKNOWN'}"
    def _cached(self, name, fetcher):
        now, cached = self.time_fn(), self._cache.get(name)
        if cached and now - cached[0] < self.cache_seconds: return dict(cached[1])
        result = fetcher(); self._cache[name] = (now, dict(result)); return result
    @staticmethod
    def _number(value):
        if value in (None, ""): return None
        try: return float(value)
        except (TypeError, ValueError): return None
    def get_liquidation_data(self, symbol: str = "BTC") -> Dict[str, Any]:
        empty = lambda: self._unavailable(long_liquidation_usdt=None, short_liquidation_usdt=None, total=None)
        if not self.configured: return empty()
        def fetch():
            try:
                response = self.session.get(f"{self.BASE_URL}/futures/liquidation/exchange-list", params={"symbol": symbol, "range": "24h"}, timeout=self.timeout); response.raise_for_status()
                payload = response.json(); api_error = self._api_error(payload)
                if api_error: result = empty(); result["error_category"] = api_error; return result
                data = payload.get("data") or []; row = next((item for item in data if str(item.get("exchange", "")).lower() == "all"), data[0] if data else {}) if isinstance(data, list) else data
                long_value = self._number(row.get("long_liquidation_usd", row.get("longVolUsd"))); short_value = self._number(row.get("short_liquidation_usd", row.get("shortVolUsd"))); total = self._number(row.get("liquidation_usd", row.get("totalVolUsd")))
                if total is None and long_value is not None and short_value is not None: total = long_value + short_value
                if total is None:
                    result = empty(); result["error_category"] = "API_EMPTY_RESPONSE"; return result
                if not self._connected_logged: logger.info("COINGLASS: CONNECTED"); self._connected_logged = True
                return {"source": DataSource.COINGLASS, "status": "CONNECTED", "is_available": True, "observed_at": int(self.time_fn()*1000), "long_liquidation_usdt": long_value, "short_liquidation_usdt": short_value, "total": total}
            except Exception as exc:
                logger.warning("CoinGlass liquidation fetch failed: {}", type(exc).__name__); result = empty(); result["error_category"] = self._error_category(exc); return result
        return self._cached(f"liquidation:{symbol}", fetch)
    def get_aggregate_oi(self, symbol: str = "BTC") -> Dict[str, Any]:
        empty = lambda: self._unavailable(aggregate_oi_usd=None)
        if not self.configured: return empty()
        def fetch():
            try:
                response = self.session.get(f"{self.BASE_URL}/futures/open-interest/exchange-list", params={"symbol": symbol}, timeout=self.timeout); response.raise_for_status()
                payload = response.json(); api_error = self._api_error(payload)
                if api_error: result = empty(); result["error_category"] = api_error; return result
                data = payload.get("data") or []; latest = next((item for item in data if str(item.get("exchange", "")).lower() == "all"), data[-1] if data else {}) if isinstance(data, list) else data if isinstance(data, dict) else {}; value = self._number(latest.get("open_interest_usd", latest.get("close")))
                if value is None:
                    result = empty(); result["error_category"] = "API_EMPTY_RESPONSE"; return result
                if not self._connected_logged: logger.info("COINGLASS: CONNECTED"); self._connected_logged = True
                return {"source": DataSource.COINGLASS, "status": "CONNECTED", "is_available": True, "observed_at": int(self.time_fn()*1000), "aggregate_oi_usd": value}
            except Exception as exc:
                logger.warning("CoinGlass aggregate OI fetch failed: {}", type(exc).__name__); result = empty(); result["error_category"] = self._error_category(exc); return result
        return self._cached(f"oi:{symbol}", fetch)
