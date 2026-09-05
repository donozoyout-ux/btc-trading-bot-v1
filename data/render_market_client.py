"""Render-safe Binance public market-data adapters.

Render can receive HTTP 451 from Binance production public USD-M endpoints.
This module keeps signed TESTNET execution completely separate while giving the
web/runtime market-data path a temporary TESTNET-public fallback. Missing
long/short or taker-ratio data is represented as ``None``; it is never replaced
with a fake neutral value such as ``1.0``.
"""

from __future__ import annotations

import time
from typing import Any, Dict, Optional

import requests
from loguru import logger

from data.binance_client import BinanceFuturesClient


class StrictPublicBinanceFuturesClient(BinanceFuturesClient):
    """Public Binance client whose optional derivatives calls fail transparently.

    The legacy public client historically swallowed ratio endpoint failures and
    returned ``1.0``.  That prevents a resilient wrapper from detecting HTTP 451
    and incorrectly turns unavailable data into a neutral signal.  Render uses
    this strict adapter instead.
    """

    def get_long_short_ratio(
        self,
        symbol: str = "BTCUSDT",
        period: str = "5m",
        limit: int = 1,
    ) -> Optional[float]:
        url = f"{self.base_url}/futures/data/globalLongShortAccountRatio"
        response = self.session.get(
            url,
            params={"symbol": symbol, "period": period, "limit": limit},
            timeout=self.timeout,
        )
        response.raise_for_status()
        data = response.json()
        if not data:
            return None
        return float(data[-1]["longShortRatio"])

    def get_taker_volume_ratio(
        self,
        symbol: str = "BTCUSDT",
        period: str = "5m",
        limit: int = 1,
    ) -> Optional[float]:
        url = f"{self.base_url}/futures/data/takerlongshortRatio"
        response = self.session.get(
            url,
            params={"symbol": symbol, "period": period, "limit": limit},
            timeout=self.timeout,
        )
        response.raise_for_status()
        data = response.json()
        if not data:
            return None
        return float(data[-1]["buySellRatio"])


class RenderResilientBinanceFuturesMarketClient:
    """Credential-free public market client with an HTTP-451 circuit breaker."""

    OPTIONAL_METHODS = {"get_long_short_ratio", "get_taker_volume_ratio"}
    EXPECTED_ERRORS = (
        requests.RequestException,
        KeyError,
        TypeError,
        ValueError,
        IndexError,
    )

    def __init__(
        self,
        primary=None,
        fallback=None,
        *,
        restriction_cooldown_seconds: int = 300,
        clock=time.monotonic,
    ) -> None:
        self.api_key = None
        self.api_secret = None
        self.testnet = False
        self.primary = primary or StrictPublicBinanceFuturesClient(
            api_key=None, api_secret=None, testnet=False
        )
        self.fallback = fallback or StrictPublicBinanceFuturesClient(
            api_key=None, api_secret=None, testnet=True
        )
        self.restriction_cooldown_seconds = max(30, int(restriction_cooldown_seconds))
        self._clock = clock
        self._production_blocked_until = 0.0
        self.active_environment = "PRODUCTION_PUBLIC"
        self.fallback_active = False
        self.production_public_status = "AVAILABLE"
        self._optional_availability: Dict[str, Optional[bool]] = {
            name: None for name in self.OPTIONAL_METHODS
        }

    @staticmethod
    def _is_http_451(exc: BaseException) -> bool:
        response = getattr(exc, "response", None)
        return bool(response is not None and getattr(response, "status_code", None) == 451)

    def _production_is_blocked(self) -> bool:
        return self._clock() < self._production_blocked_until

    def _mark_production_restricted(self) -> None:
        self.production_public_status = "HTTP_451_RESTRICTED"
        self._production_blocked_until = self._clock() + self.restriction_cooldown_seconds
        self.active_environment = "TESTNET_PUBLIC_FALLBACK"
        self.fallback_active = True
        logger.warning(
            "BINANCE PRODUCTION PUBLIC: RESTRICTED_HTTP_451 | "
            "MARKET DATA SOURCE: TESTNET_PUBLIC_FALLBACK | RETRY_AFTER_SECONDS: {}",
            self.restriction_cooldown_seconds,
        )

    def begin_cycle(self) -> None:
        """Start a dashboard cycle without clearing an active 451 cooldown."""
        if self._production_is_blocked():
            self.active_environment = "TESTNET_PUBLIC_FALLBACK"
            self.fallback_active = True
            return
        if self.production_public_status == "AVAILABLE":
            self.active_environment = "PRODUCTION_PUBLIC"
            self.fallback_active = False

    def _fallback_call(self, method: str, *args, **kwargs):
        optional = method in self.OPTIONAL_METHODS
        try:
            result = getattr(self.fallback, method)(*args, **kwargs)
        except self.EXPECTED_ERRORS:
            self.active_environment = "TESTNET_PUBLIC_FALLBACK"
            self.fallback_active = True
            if optional:
                self._optional_availability[method] = False
                return None
            raise

        self.active_environment = "TESTNET_PUBLIC_FALLBACK"
        self.fallback_active = True
        if optional:
            self._optional_availability[method] = result is not None
        return result

    def _call(self, method: str, *args, **kwargs):
        optional = method in self.OPTIONAL_METHODS

        if self._production_is_blocked():
            return self._fallback_call(method, *args, **kwargs)

        try:
            result = getattr(self.primary, method)(*args, **kwargs)
        except self.EXPECTED_ERRORS as exc:
            if self._is_http_451(exc):
                self._mark_production_restricted()
            else:
                logger.warning(
                    "Binance production public market data unavailable; "
                    "using TESTNET public fallback: {}",
                    type(exc).__name__,
                )
            return self._fallback_call(method, *args, **kwargs)

        # The cooldown has expired and production succeeded again.
        self.production_public_status = "AVAILABLE"
        self._production_blocked_until = 0.0
        self.active_environment = "PRODUCTION_PUBLIC"
        self.fallback_active = False

        if optional:
            if result is None:
                return self._fallback_call(method, *args, **kwargs)
            self._optional_availability[method] = True
        return result

    def status(self) -> Dict[str, Any]:
        attempted = [
            value for value in self._optional_availability.values() if value is not None
        ]
        if not attempted:
            derivatives_status = "UNKNOWN"
        elif len(attempted) == len(self.OPTIONAL_METHODS) and all(attempted):
            derivatives_status = "AVAILABLE"
        elif any(attempted):
            derivatives_status = "DEGRADED"
        else:
            derivatives_status = "UNAVAILABLE"

        retry_after = max(0, int(self._production_blocked_until - self._clock()))
        return {
            "market_data_source": self.active_environment,
            "production_public_status": self.production_public_status,
            "production_public_retry_after_seconds": retry_after,
            "fallback_active": self.fallback_active,
            "derivatives_status": derivatives_status,
        }

    def get_klines(self, *args, **kwargs):
        return self._call("get_klines", *args, **kwargs)

    def get_ticker_price(self, *args, **kwargs):
        return self._call("get_ticker_price", *args, **kwargs)

    def get_mark_price(self, *args, **kwargs):
        return self._call("get_mark_price", *args, **kwargs)

    def get_open_interest(self, *args, **kwargs):
        return self._call("get_open_interest", *args, **kwargs)

    def get_funding_rate(self, *args, **kwargs):
        return self._call("get_funding_rate", *args, **kwargs)

    def get_long_short_ratio(self, *args, **kwargs):
        return self._call("get_long_short_ratio", *args, **kwargs)

    def get_taker_volume_ratio(self, *args, **kwargs):
        return self._call("get_taker_volume_ratio", *args, **kwargs)
