"""Binance Futures USDT-M REST Client supporting public data and testnet/live trading."""

import time
import hmac
import hashlib
from typing import List, Dict, Any, Optional
from urllib.parse import urlencode
import requests
from loguru import logger

from core.models import Candle


class BinanceFuturesClient:
    """
    Direct REST client for Binance Futures (USDT-M).
    Handles market data (OHLCV, Open Interest, Funding, Taker Ratio)
    and order management on Live / Testnet endpoints.
    """

    PROD_URL = "https://fapi.binance.com"
    TESTNET_URL = "https://testnet.binancefuture.com"

    def __init__(
        self,
        api_key: Optional[str] = None,
        api_secret: Optional[str] = None,
        testnet: bool = False,
        timeout: int = 5,
    ):
        self.api_key = api_key
        self.api_secret = api_secret
        self.testnet = testnet
        self.base_url = self.TESTNET_URL if testnet else self.PROD_URL
        self.timeout = timeout
        self.session = requests.Session()
        if api_key:
            self.session.headers.update({"X-MBX-APIKEY": api_key})

    def _sign(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Adds timestamp and HMAC-SHA256 signature to parameters."""
        if not self.api_secret:
            raise ValueError("API secret is required for signed endpoints")
        params["timestamp"] = int(time.time() * 1000)
        query_string = urlencode(params)
        signature = hmac.new(
            self.api_secret.encode("utf-8"),
            query_string.encode("utf-8"),
            hashlib.sha256
        ).hexdigest()
        params["signature"] = signature
        return params

    def get_klines(
        self,
        symbol: str = "BTCUSDT",
        interval: str = "5m",
        limit: int = 500,
        start_time: Optional[int] = None,
        end_time: Optional[int] = None,
    ) -> List[Candle]:
        """
        Fetches historical klines and returns closed candles only.
        The very last candle from Binance is typically still open/forming,
        so it is filtered out unless explicitly past its close time.
        """
        url = f"{self.base_url}/fapi/v1/klines"
        params: Dict[str, Any] = {
            "symbol": symbol,
            "interval": interval,
            "limit": min(limit, 1000),
        }
        if start_time:
            params["startTime"] = start_time
        if end_time:
            params["endTime"] = end_time

        resp = self.session.get(url, params=params, timeout=self.timeout)
        resp.raise_for_status()
        data = resp.json()

        candles: List[Candle] = []
        now_ms = int(time.time() * 1000)

        for item in data:
            # item format:
            # 0: open time, 1: open, 2: high, 3: low, 4: close, 5: volume, 6: close time, ...
            open_time = int(item[0])
            close_time = int(item[6])

            # A candle is strictly closed only if current time > close_time
            is_closed = now_ms > close_time

            candle = Candle(
                timestamp=open_time,
                open=float(item[1]),
                high=float(item[2]),
                low=float(item[3]),
                close=float(item[4]),
                volume=float(item[5]),
                is_closed=is_closed,
            )
            # Guarantee: only include closed candles
            if is_closed:
                candles.append(candle)

        return candles

    def get_ticker_price(self, symbol: str = "BTCUSDT") -> float:
        """Fetches current mark/last price."""
        url = f"{self.base_url}/fapi/v1/ticker/price"
        resp = self.session.get(url, params={"symbol": symbol}, timeout=self.timeout)
        resp.raise_for_status()
        return float(resp.json()["price"])

    def get_mark_price(self, symbol: str = "BTCUSDT") -> float:
        """Fetches mark price and funding rate info."""
        url = f"{self.base_url}/fapi/v1/premiumIndex"
        resp = self.session.get(url, params={"symbol": symbol}, timeout=self.timeout)
        resp.raise_for_status()
        return float(resp.json()["markPrice"])

    def get_open_interest(self, symbol: str = "BTCUSDT") -> float:
        """Fetches current Open Interest in BTC contracts."""
        url = f"{self.base_url}/fapi/v1/openInterest"
        resp = self.session.get(url, params={"symbol": symbol}, timeout=self.timeout)
        resp.raise_for_status()
        return float(resp.json()["openInterest"])

    def get_funding_rate(self, symbol: str = "BTCUSDT") -> float:
        """Fetches latest funding rate."""
        url = f"{self.base_url}/fapi/v1/premiumIndex"
        resp = self.session.get(url, params={"symbol": symbol}, timeout=self.timeout)
        resp.raise_for_status()
        return float(resp.json()["lastFundingRate"])

    def get_long_short_ratio(self, symbol: str = "BTCUSDT", period: str = "5m", limit: int = 1) -> float:
        """Fetches Global Long/Short Account Ratio."""
        url = f"{self.base_url}/futures/data/globalLongShortAccountRatio"
        try:
            resp = self.session.get(url, params={"symbol": symbol, "period": period, "limit": limit}, timeout=self.timeout)
            resp.raise_for_status()
            data = resp.json()
            if data and len(data) > 0:
                return float(data[-1]["longShortRatio"])
        except Exception as e:
            logger.warning(f"Error fetching long/short ratio from Binance: {e}")
        return 1.0

    def get_taker_volume_ratio(self, symbol: str = "BTCUSDT", period: str = "5m", limit: int = 1) -> float:
        """Fetches Taker Buy / Sell Volume Ratio."""
        url = f"{self.base_url}/futures/data/takerlongshortRatio"
        try:
            resp = self.session.get(url, params={"symbol": symbol, "period": period, "limit": limit}, timeout=self.timeout)
            resp.raise_for_status()
            data = resp.json()
            if data and len(data) > 0:
                return float(data[-1]["buySellRatio"])
        except Exception as e:
            logger.warning(f"Error fetching taker ratio: {e}")
        return 1.0

    def get_account_balance(self) -> float:
        """Fetches USDT available equity."""
        if not self.api_key or not self.api_secret:
            return 10_000.0  # Default simulation balance

        url = f"{self.base_url}/fapi/v2/account"
        params = self._sign({})
        resp = self.session.get(url, params=params, timeout=self.timeout)
        resp.raise_for_status()
        data = resp.json()
        for asset in data.get("assets", []):
            if asset.get("asset") == "USDT":
                return float(asset.get("walletBalance", 0.0))
        return 0.0

    def place_order(
        self,
        symbol: str = "BTCUSDT",
        side: str = "BUY",
        order_type: str = "MARKET",
        quantity: float = 0.001,
        price: Optional[float] = None,
        stop_price: Optional[float] = None,
        reduce_only: bool = False,
    ) -> Dict[str, Any]:
        """Places a Binance Futures order with precision and signature."""
        url = f"{self.base_url}/fapi/v1/order"
        params: Dict[str, Any] = {
            "symbol": symbol,
            "side": side.upper(),
            "type": order_type.upper(),
            "quantity": f"{quantity:.3f}",  # BTCUSDT 3 decimal places
        }
        if price:
            params["price"] = f"{price:.2f}"
            params["timeInForce"] = "GTC"
        if stop_price:
            params["stopPrice"] = f"{stop_price:.2f}"
        if reduce_only:
            params["reduceOnly"] = "true"

        signed_params = self._sign(params)
        resp = self.session.post(url, params=signed_params, timeout=self.timeout)
        resp.raise_for_status()
        return resp.json()
