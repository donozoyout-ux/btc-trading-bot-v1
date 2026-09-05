"""Render-safe Binance public market-data adapters.

Render can receive HTTP 451 from Binance production USD-M Futures REST. The
runtime therefore uses a strict source hierarchy:

1. Binance production USD-M Futures public data (native / preferred)
2. Binance production SPOT public market-data endpoint as a real-price proxy
3. Binance Futures TESTNET public data for display diagnostics only

The spot proxy is real production market data and may drive TESTNET forward-test
price/structure decisions. It is explicitly labelled as a SPOT proxy, not native
Futures data. Derivatives never come from the spot proxy. TESTNET fallback
values are display-only and can never CONFIRM/WARN/REJECT a strategy decision.
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

import requests
from loguru import logger

from core.models import Candle
from data.binance_client import BinanceFuturesClient


class StrictPublicBinanceFuturesClient(BinanceFuturesClient):
    """Public Futures client whose optional derivatives failures stay explicit."""

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


class BinanceSpotPublicMarketClient:
    """Credential-free Binance production SPOT market data.

    Binance documents ``https://data-api.binance.vision`` as the market-data-only
    base endpoint for public Spot APIs. This client intentionally implements only
    price/candle methods needed by the strategy and exposes no account/order API.
    """

    BASE_URL = "https://data-api.binance.vision"

    def __init__(self, timeout: int = 5) -> None:
        self.timeout = timeout
        self.session = requests.Session()

    def get_klines(
        self,
        symbol: str = "BTCUSDT",
        interval: str = "5m",
        limit: int = 500,
        start_time: Optional[int] = None,
        end_time: Optional[int] = None,
    ) -> List[Candle]:
        params: Dict[str, Any] = {
            "symbol": symbol,
            "interval": interval,
            "limit": min(int(limit), 1000),
        }
        if start_time is not None:
            params["startTime"] = int(start_time)
        if end_time is not None:
            params["endTime"] = int(end_time)
        response = self.session.get(
            f"{self.BASE_URL}/api/v3/klines",
            params=params,
            timeout=self.timeout,
        )
        response.raise_for_status()
        data = response.json()
        now_ms = int(time.time() * 1000)
        candles: List[Candle] = []
        for item in data:
            close_time = int(item[6])
            if now_ms <= close_time:
                continue
            candles.append(
                Candle(
                    timestamp=int(item[0]),
                    open=float(item[1]),
                    high=float(item[2]),
                    low=float(item[3]),
                    close=float(item[4]),
                    volume=float(item[5]),
                    is_closed=True,
                )
            )
        return candles

    def get_ticker_price(self, symbol: str = "BTCUSDT") -> float:
        response = self.session.get(
            f"{self.BASE_URL}/api/v3/ticker/price",
            params={"symbol": symbol},
            timeout=self.timeout,
        )
        response.raise_for_status()
        return float(response.json()["price"])

    def get_mark_price(self, symbol: str = "BTCUSDT") -> float:
        # Spot has no Futures mark price. Keep the method name for the dashboard
        # interface while preserving source/basis metadata in status().
        return self.get_ticker_price(symbol)


class RenderResilientBinanceFuturesMarketClient:
    """Production-first public market client with safe Render fallbacks."""

    MARKET_METHODS = {"get_klines", "get_ticker_price", "get_mark_price"}
    OPTIONAL_METHODS = {"get_long_short_ratio", "get_taker_volume_ratio"}
    DERIVATIVE_METHODS = {
        "get_open_interest",
        "get_funding_rate",
        "get_long_short_ratio",
        "get_taker_volume_ratio",
    }
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
        spot_proxy=None,
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
        self.spot_proxy = spot_proxy or BinanceSpotPublicMarketClient()
        self.restriction_cooldown_seconds = max(30, int(restriction_cooldown_seconds))
        self._clock = clock
        self._production_blocked_until = 0.0
        self.active_environment = "PRODUCTION_FUTURES_PUBLIC"
        self.fallback_active = False
        self.production_public_status = "AVAILABLE"
        self._optional_availability: Dict[str, Optional[bool]] = {
            name: None for name in self.OPTIONAL_METHODS
        }
        self._fallback_derivatives: Dict[str, Dict[str, Any]] = {}
        # A Spot source must successfully serve a real market call before it can
        # grant forward-test authority. Derivatives failure alone cannot do so.
        self._spot_proxy_last_error: Optional[str] = "NOT_VALIDATED"

    @property
    def market_data_trading_safe(self) -> bool:
        # Real production price data is sufficient for TESTNET forward testing.
        # SPOT_PROXY is deliberately exposed so it can never be mistaken for a
        # native Futures feed when the project later considers real money.
        return self.active_environment in {
            "PRODUCTION_FUTURES_PUBLIC",
            "BINANCE_SPOT_PUBLIC_PROXY",
        }

    @property
    def market_basis(self) -> str:
        if self.active_environment == "PRODUCTION_FUTURES_PUBLIC":
            return "FUTURES_NATIVE"
        if self.active_environment == "BINANCE_SPOT_PUBLIC_PROXY":
            return "SPOT_PROXY"
        if self.active_environment == "TESTNET_PUBLIC_FALLBACK":
            return "TESTNET_FUTURES"
        return "UNKNOWN"

    @staticmethod
    def _is_http_451(exc: BaseException) -> bool:
        response = getattr(exc, "response", None)
        return bool(response is not None and getattr(response, "status_code", None) == 451)

    def _production_is_blocked(self) -> bool:
        return self._clock() < self._production_blocked_until

    def _mark_production_restricted(self) -> None:
        self.production_public_status = "HTTP_451_RESTRICTED"
        self._production_blocked_until = self._clock() + self.restriction_cooldown_seconds
        logger.warning(
            "BINANCE FUTURES PRODUCTION PUBLIC: RESTRICTED_HTTP_451 | "
            "TRYING BINANCE_SPOT_PUBLIC_PROXY | RETRY_AFTER_SECONDS: {}",
            self.restriction_cooldown_seconds,
        )

    def begin_cycle(self) -> None:
        """Start a cycle without clearing an active production restriction."""
        if not self._production_is_blocked() and self.production_public_status == "AVAILABLE":
            self.active_environment = "PRODUCTION_FUTURES_PUBLIC"
            self.fallback_active = False

    def _remember_fallback_derivative(self, method: str, value: Optional[float]) -> None:
        if method not in self.DERIVATIVE_METHODS:
            return
        self._fallback_derivatives[method] = {
            "value": value,
            "source": "BINANCE_TESTNET_FALLBACK",
            "observed_at": int(time.time() * 1000),
            "trading_authority": False,
        }

    def _testnet_display_call(self, method: str, *args, **kwargs):
        optional = method in self.OPTIONAL_METHODS
        try:
            result = getattr(self.fallback, method)(*args, **kwargs)
        except self.EXPECTED_ERRORS:
            self.active_environment = "TESTNET_PUBLIC_FALLBACK"
            self.fallback_active = True
            self._remember_fallback_derivative(method, None)
            if optional:
                self._optional_availability[method] = False
                return None
            raise
        self.active_environment = "TESTNET_PUBLIC_FALLBACK"
        self.fallback_active = True
        self._remember_fallback_derivative(method, result)
        if optional:
            self._optional_availability[method] = result is not None
        return result

    def _spot_market_call(self, method: str, *args, **kwargs):
        try:
            result = getattr(self.spot_proxy, method)(*args, **kwargs)
        except self.EXPECTED_ERRORS as exc:
            self._spot_proxy_last_error = type(exc).__name__
            logger.warning(
                "Binance Spot production proxy unavailable; using TESTNET display fallback: {}",
                self._spot_proxy_last_error,
            )
            return self._testnet_display_call(method, *args, **kwargs)
        self._spot_proxy_last_error = None
        self.active_environment = "BINANCE_SPOT_PUBLIC_PROXY"
        self.fallback_active = True
        return result

    def _market_call(self, method: str, *args, **kwargs):
        if self._production_is_blocked():
            return self._spot_market_call(method, *args, **kwargs)

        try:
            result = getattr(self.primary, method)(*args, **kwargs)
        except self.EXPECTED_ERRORS as exc:
            if self._is_http_451(exc):
                self._mark_production_restricted()
            else:
                logger.warning(
                    "Binance Futures production public market data unavailable; "
                    "trying real Spot proxy: {}",
                    type(exc).__name__,
                )
            return self._spot_market_call(method, *args, **kwargs)

        self.production_public_status = "AVAILABLE"
        self._production_blocked_until = 0.0
        self.active_environment = "PRODUCTION_FUTURES_PUBLIC"
        self.fallback_active = False
        return result

    def _derivative_call(self, method: str, *args, **kwargs):
        """Return only authoritative production Futures derivatives data.

        During a production restriction we may query TESTNET solely to retain
        display diagnostics, but the returned strategy value remains ``None``.
        """
        if self._production_is_blocked():
            try:
                self._testnet_display_call(method, *args, **kwargs)
            except self.EXPECTED_ERRORS:
                pass
            # Restore the real price source label only after Spot was validated.
            if self._spot_proxy_last_error is None:
                self.active_environment = "BINANCE_SPOT_PUBLIC_PROXY"
                self.fallback_active = True
            return None

        try:
            result = getattr(self.primary, method)(*args, **kwargs)
        except self.EXPECTED_ERRORS as exc:
            if self._is_http_451(exc):
                self._mark_production_restricted()
            try:
                self._testnet_display_call(method, *args, **kwargs)
            except self.EXPECTED_ERRORS:
                pass
            if self._spot_proxy_last_error is None:
                self.active_environment = "BINANCE_SPOT_PUBLIC_PROXY"
                self.fallback_active = True
            return None

        if method in self.OPTIONAL_METHODS:
            self._optional_availability[method] = result is not None
        self.production_public_status = "AVAILABLE"
        self._production_blocked_until = 0.0
        if self.active_environment != "BINANCE_SPOT_PUBLIC_PROXY":
            self.active_environment = "PRODUCTION_FUTURES_PUBLIC"
            self.fallback_active = False
        return result

    def fallback_derivatives_telemetry(self) -> Dict[str, Dict[str, Any]]:
        return {key: dict(value) for key, value in self._fallback_derivatives.items()}

    def status(self) -> Dict[str, Any]:
        if self._production_is_blocked():
            derivatives_status = "UNAVAILABLE"
        else:
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

        if self._spot_proxy_last_error is None:
            spot_status = "AVAILABLE"
        elif self._spot_proxy_last_error == "NOT_VALIDATED":
            spot_status = "NOT_VALIDATED"
        else:
            spot_status = "UNAVAILABLE"

        retry_after = max(0, int(self._production_blocked_until - self._clock()))
        return {
            "market_data_source": self.active_environment,
            "market_data_trading_safe": self.market_data_trading_safe,
            "market_basis": self.market_basis,
            "production_public_status": self.production_public_status,
            "production_public_retry_after_seconds": retry_after,
            "fallback_active": self.fallback_active,
            "spot_proxy_status": spot_status,
            "spot_proxy_error": None if self._spot_proxy_last_error in {None, "NOT_VALIDATED"} else self._spot_proxy_last_error,
            "derivatives_status": derivatives_status,
        }

    def get_klines(self, *args, **kwargs):
        return self._market_call("get_klines", *args, **kwargs)

    def get_ticker_price(self, *args, **kwargs):
        return self._market_call("get_ticker_price", *args, **kwargs)

    def get_mark_price(self, *args, **kwargs):
        return self._market_call("get_mark_price", *args, **kwargs)

    def get_open_interest(self, *args, **kwargs):
        return self._derivative_call("get_open_interest", *args, **kwargs)

    def get_funding_rate(self, *args, **kwargs):
        return self._derivative_call("get_funding_rate", *args, **kwargs)

    def get_long_short_ratio(self, *args, **kwargs):
        return self._derivative_call("get_long_short_ratio", *args, **kwargs)

    def get_taker_volume_ratio(self, *args, **kwargs):
        return self._derivative_call("get_taker_volume_ratio", *args, **kwargs)
