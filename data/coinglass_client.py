"""CoinGlass V4 client with authentication backoff and strict provenance."""
from __future__ import annotations

import time
from typing import Any, Dict, Optional

import requests
from loguru import logger

from config.constants import DataSource


class CoinGlassClient:
    BASE_URL = "https://open-api-v4.coinglass.com"
    AUTH_BACKOFF_SECONDS = 300

    def __init__(self, api_key: Optional[str] = None, timeout: int = 5, cache_seconds: int = 60, time_fn=time.time):
        self.api_key = api_key
        self.timeout = timeout
        self.cache_seconds = cache_seconds
        self.time_fn = time_fn
        self.session = requests.Session()
        self.session.headers.update({"accept": "application/json"})
        if api_key:
            self.session.headers.update({"CG-API-KEY": api_key})
        self._cache: Dict[str, tuple[float, Dict[str, Any]]] = {}
        self._auth_cache: Optional[tuple[float, Dict[str, Any]]] = None
        self._connected_logged = False

    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    @staticmethod
    def _number(value: Any) -> Optional[float]:
        if value in (None, ""):
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _http_category(status_code: Optional[int]) -> str:
        return {401: "AUTH_ERROR", 403: "PLAN_FORBIDDEN", 429: "RATE_LIMITED"}.get(status_code, "UNAVAILABLE")

    @classmethod
    def _exception_category(cls, exc: Exception) -> str:
        status_code = getattr(getattr(exc, "response", None), "status_code", None)
        return cls._http_category(status_code) if status_code else "NETWORK_ERROR"

    @staticmethod
    def _payload_ok(payload: Any) -> bool:
        return isinstance(payload, dict) and str(payload.get("code")) == "0"

    @classmethod
    def _payload_category(cls, payload: Any) -> str:
        if not isinstance(payload, dict):
            return "API_SCHEMA_ERROR"
        try:
            code = int(payload.get("code"))
        except (TypeError, ValueError):
            return "API_SCHEMA_ERROR"
        return cls._http_category(code) if code in (401, 403, 429) else "API_SCHEMA_ERROR"

    def _empty(self, status: str = "UNAVAILABLE", **fields: Any) -> Dict[str, Any]:
        return {"source": DataSource.UNAVAILABLE, "status": status, "is_available": False, "observed_at": None, "error_category": status, **fields}

    def authenticate(self, force: bool = False) -> Dict[str, Any]:
        """Validate the key once; authentication failures back off for five minutes."""
        if not self.configured:
            return {"status": "UNAVAILABLE", "configured": False, "observed_at": None}
        now = self.time_fn()
        if not force and self._auth_cache:
            cached_at, cached = self._auth_cache
            ttl = self.AUTH_BACKOFF_SECONDS if cached["status"] == "AUTH_ERROR" else self.cache_seconds
            if now - cached_at < ttl:
                return dict(cached)
        try:
            response = self.session.get(f"{self.BASE_URL}/api/futures/supported-coins", timeout=self.timeout)
            if response.status_code != 200:
                status = self._http_category(response.status_code)
            else:
                payload = response.json()
                status = "CONNECTED" if self._payload_ok(payload) else self._payload_category(payload)
        except Exception as exc:
            status = self._exception_category(exc)
        result = {"status": status, "configured": True, "observed_at": int(now * 1000) if status == "CONNECTED" else None}
        self._auth_cache = (now, dict(result))
        if status == "CONNECTED" and not self._connected_logged:
            logger.info("COINGLASS: CONNECTED")
            self._connected_logged = True
        elif status != "CONNECTED":
            logger.warning("COINGLASS AUTH: {}", status)
        return result

    def _cached_request(self, name: str, unavailable_fields: Dict[str, Any], path: str, params: Dict[str, Any], parser) -> Dict[str, Any]:
        auth = self.authenticate()
        if auth["status"] != "CONNECTED":
            return self._empty(auth["status"], **unavailable_fields)
        now = self.time_fn()
        cached = self._cache.get(name)
        if cached and now - cached[0] < self.cache_seconds:
            return dict(cached[1])
        try:
            response = self.session.get(f"{self.BASE_URL}{path}", params=params, timeout=self.timeout)
            if response.status_code != 200:
                status = self._http_category(response.status_code)
                result = self._empty(status, **unavailable_fields)
            else:
                payload = response.json()
                if not self._payload_ok(payload):
                    result = self._empty(self._payload_category(payload), **unavailable_fields)
                else:
                    values = parser(payload.get("data"))
                    if values is None:
                        result = self._empty("API_SCHEMA_ERROR", **unavailable_fields)
                    else:
                        result = {"source": DataSource.COINGLASS, "status": "CONNECTED", "is_available": True, "observed_at": int(now * 1000), "error_category": None, **values}
        except Exception as exc:
            result = self._empty(self._exception_category(exc), **unavailable_fields)
        self._cache[name] = (now, dict(result))
        return result

    def get_aggregate_oi(self, symbol: str = "BTC") -> Dict[str, Any]:
        def parse(data):
            if not isinstance(data, list) or not data:
                return None
            value = self._number(data[-1].get("close")) if isinstance(data[-1], dict) else None
            return {"aggregate_oi_usd": value} if value is not None else None
        return self._cached_request(
            f"oi:{symbol}", {"aggregate_oi_usd": None},
            "/api/futures/open-interest/aggregated-history",
            {"symbol": symbol, "interval": "1h", "limit": 2, "unit": "usd"}, parse,
        )

    def get_liquidation_data(self, symbol: str = "BTC") -> Dict[str, Any]:
        def parse(data):
            if not isinstance(data, list):
                return None
            row = next((item for item in data if str(item.get("exchange", "")).lower() == "all"), None)
            if not row:
                return None
            total = self._number(row.get("liquidation_usd"))
            long_value = self._number(row.get("long_liquidation_usd"))
            short_value = self._number(row.get("short_liquidation_usd"))
            if None in (total, long_value, short_value):
                return None
            return {"total": total, "long_liquidation_usdt": long_value, "short_liquidation_usdt": short_value}
        return self._cached_request(
            f"liquidation:{symbol}", {"total": None, "long_liquidation_usdt": None, "short_liquidation_usdt": None},
            "/api/futures/liquidation/exchange-list", {"symbol": symbol, "range": "24h"}, parse,
        )
