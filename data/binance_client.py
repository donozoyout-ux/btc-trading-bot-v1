"""Binance Futures USDT-M REST client for public data and guarded account access."""

import hashlib
import hmac
import time
from typing import Any, Dict, List, Optional
from urllib.parse import urlencode

import requests
from loguru import logger

from core.models import Candle


class BinanceFuturesClient:
    """Direct REST client for Binance USD-M Futures.

    `read_only=True` hard-blocks order submission. This is used by the demo
    dashboard account connector so signed account reads can never become an
    accidental execution path.
    """

    PROD_URL = "https://fapi.binance.com"
    TESTNET_URL = "https://testnet.binancefuture.com"

    def __init__(
        self,
        api_key: Optional[str] = None,
        api_secret: Optional[str] = None,
        testnet: bool = False,
        timeout: int = 5,
        read_only: bool = False,
        recv_window_ms: int = 5000,
    ):
        self.api_key = api_key or None
        self.api_secret = api_secret or None
        self.testnet = bool(testnet)
        self.read_only = bool(read_only)
        self.base_url = self.TESTNET_URL if self.testnet else self.PROD_URL
        self.timeout = timeout
        self.recv_window_ms = max(1000, min(int(recv_window_ms), 60000))
        self.session = requests.Session()
        self._server_time_offset_ms = 0
        self._last_time_sync_monotonic = 0.0
        if self.api_key:
            self.session.headers.update({"X-MBX-APIKEY": self.api_key})

    @property
    def account_configured(self) -> bool:
        return bool(self.api_key and self.api_secret)

    def _require_credentials(self) -> None:
        if not self.api_key or not self.api_secret:
            raise RuntimeError("Binance account credentials are not configured")

    def get_server_time(self) -> int:
        """Return Binance server time in milliseconds."""
        url = f"{self.base_url}/fapi/v1/time"
        resp = self.session.get(url, timeout=self.timeout)
        resp.raise_for_status()
        return int(resp.json()["serverTime"])

    def sync_server_time(self, force: bool = False) -> int:
        """Synchronize local signed-request clock with Binance server time."""
        now_mono = time.monotonic()
        if not force and (now_mono - self._last_time_sync_monotonic) < 60:
            return self._server_time_offset_ms
        local_before = int(time.time() * 1000)
        server_ms = self.get_server_time()
        local_after = int(time.time() * 1000)
        midpoint = (local_before + local_after) // 2
        self._server_time_offset_ms = server_ms - midpoint
        self._last_time_sync_monotonic = now_mono
        return self._server_time_offset_ms

    def _sign(self, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Return signed params without ever logging the signature/query string."""
        self._require_credentials()
        self.sync_server_time()
        signed = dict(params or {})
        signed["timestamp"] = int(time.time() * 1000) + self._server_time_offset_ms
        signed["recvWindow"] = self.recv_window_ms
        query_string = urlencode(signed)
        signed["signature"] = hmac.new(
            self.api_secret.encode("utf-8"),
            query_string.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        return signed

    def _signed_get(self, path: str, params: Optional[Dict[str, Any]] = None) -> Any:
        url = f"{self.base_url}{path}"
        signed = self._sign(params)
        resp = self.session.get(url, params=signed, timeout=self.timeout)
        if resp.status_code == 400 and "timestamp" in resp.text.lower():
            self.sync_server_time(force=True)
            signed = self._sign(params)
            resp = self.session.get(url, params=signed, timeout=self.timeout)
        resp.raise_for_status()
        return resp.json()

    def get_klines(
        self,
        symbol: str = "BTCUSDT",
        interval: str = "5m",
        limit: int = 500,
        start_time: Optional[int] = None,
        end_time: Optional[int] = None,
    ) -> List[Candle]:
        """Fetch historical klines and return strictly closed candles only."""
        url = f"{self.base_url}/fapi/v1/klines"
        params: Dict[str, Any] = {
            "symbol": symbol,
            "interval": interval,
            "limit": min(limit, 1000),
        }
        if start_time is not None:
            params["startTime"] = start_time
        if end_time is not None:
            params["endTime"] = end_time

        resp = self.session.get(url, params=params, timeout=self.timeout)
        resp.raise_for_status()
        data = resp.json()
        now_ms = int(time.time() * 1000)
        candles: List[Candle] = []
        for item in data:
            open_time = int(item[0])
            close_time = int(item[6])
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
            if is_closed:
                candles.append(candle)
        return candles

    def get_ticker_price(self, symbol: str = "BTCUSDT") -> float:
        url = f"{self.base_url}/fapi/v1/ticker/price"
        resp = self.session.get(url, params={"symbol": symbol}, timeout=self.timeout)
        resp.raise_for_status()
        return float(resp.json()["price"])

    def get_mark_price(self, symbol: str = "BTCUSDT") -> float:
        url = f"{self.base_url}/fapi/v1/premiumIndex"
        resp = self.session.get(url, params={"symbol": symbol}, timeout=self.timeout)
        resp.raise_for_status()
        return float(resp.json()["markPrice"])

    def get_open_interest(self, symbol: str = "BTCUSDT") -> float:
        url = f"{self.base_url}/fapi/v1/openInterest"
        resp = self.session.get(url, params={"symbol": symbol}, timeout=self.timeout)
        resp.raise_for_status()
        return float(resp.json()["openInterest"])

    def get_funding_rate(self, symbol: str = "BTCUSDT") -> float:
        url = f"{self.base_url}/fapi/v1/premiumIndex"
        resp = self.session.get(url, params={"symbol": symbol}, timeout=self.timeout)
        resp.raise_for_status()
        return float(resp.json()["lastFundingRate"])

    def get_long_short_ratio(
        self, symbol: str = "BTCUSDT", period: str = "5m", limit: int = 1
    ) -> Optional[float]:
        url = f"{self.base_url}/futures/data/globalLongShortAccountRatio"
        try:
            resp = self.session.get(
                url,
                params={"symbol": symbol, "period": period, "limit": limit},
                timeout=self.timeout,
            )
            resp.raise_for_status()
            data = resp.json()
            if data:
                return float(data[-1]["longShortRatio"])
        except Exception as exc:
            logger.warning(f"Binance long/short ratio unavailable: {type(exc).__name__}")
        return None

    def get_taker_volume_ratio(
        self, symbol: str = "BTCUSDT", period: str = "5m", limit: int = 1
    ) -> Optional[float]:
        url = f"{self.base_url}/futures/data/takerlongshortRatio"
        try:
            resp = self.session.get(
                url,
                params={"symbol": symbol, "period": period, "limit": limit},
                timeout=self.timeout,
            )
            resp.raise_for_status()
            data = resp.json()
            if data:
                return float(data[-1]["buySellRatio"])
        except Exception as exc:
            logger.warning(f"Binance taker ratio unavailable: {type(exc).__name__}")
        return None

    # ------------------------------------------------------------------
    # Signed account reads
    # ------------------------------------------------------------------
    def get_account_information(self) -> Dict[str, Any]:
        return self._signed_get("/fapi/v2/account")

    def get_position_risk(self, symbol: Optional[str] = None) -> List[Dict[str, Any]]:
        params = {"symbol": symbol} if symbol else None
        data = self._signed_get("/fapi/v2/positionRisk", params)
        return data if isinstance(data, list) else []

    def get_open_orders(self, symbol: Optional[str] = None) -> List[Dict[str, Any]]:
        params = {"symbol": symbol} if symbol else None
        data = self._signed_get("/fapi/v1/openOrders", params)
        return data if isinstance(data, list) else []

    def get_account_balance(self) -> float:
        """Fetch real USDT Futures wallet balance; never synthesize a fallback."""
        data = self.get_account_information()
        for asset in data.get("assets", []):
            if asset.get("asset") == "USDT":
                return float(asset.get("walletBalance", 0.0))
        raise RuntimeError("USDT balance not present in Binance Futures account response")

    @staticmethod
    def _float(value: Any) -> Optional[float]:
        if value in (None, ""):
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    def get_account_summary(self) -> Dict[str, Any]:
        """Return a sanitized account snapshot suitable for a dashboard API."""
        account = self.get_account_information()
        position_rows = self.get_position_risk()
        order_rows = self.get_open_orders()

        usdt = next((a for a in account.get("assets", []) if a.get("asset") == "USDT"), {})
        positions: List[Dict[str, Any]] = []
        for row in position_rows:
            amount = self._float(row.get("positionAmt")) or 0.0
            if abs(amount) <= 0.0:
                continue
            position_side = row.get("positionSide")
            if position_side in (None, "", "BOTH"):
                side = "LONG" if amount > 0 else "SHORT"
            else:
                side = str(position_side)
            positions.append(
                {
                    "symbol": row.get("symbol"),
                    "side": side,
                    "position_amount": amount,
                    "entry_price": self._float(row.get("entryPrice")),
                    "break_even_price": self._float(row.get("breakEvenPrice")),
                    "mark_price": self._float(row.get("markPrice")),
                    "notional": self._float(row.get("notional")),
                    "leverage": self._float(row.get("leverage")),
                    "margin_type": row.get("marginType"),
                    "unrealized_pnl": self._float(row.get("unRealizedProfit")),
                    "liquidation_price": self._float(row.get("liquidationPrice")),
                    "isolated_wallet": self._float(row.get("isolatedWallet")),
                    "update_time": row.get("updateTime"),
                }
            )

        orders: List[Dict[str, Any]] = []
        for row in order_rows:
            orders.append(
                {
                    "symbol": row.get("symbol"),
                    "side": row.get("side"),
                    "type": row.get("type"),
                    "quantity": self._float(row.get("origQty")),
                    "executed_quantity": self._float(row.get("executedQty")),
                    "price": self._float(row.get("price")),
                    "stop_price": self._float(row.get("stopPrice")),
                    "status": row.get("status"),
                    "reduce_only": bool(row.get("reduceOnly", False)),
                    "order_id": row.get("orderId"),
                    "update_time": row.get("updateTime") or row.get("time"),
                }
            )

        wallet = self._float(usdt.get("walletBalance"))
        available = self._float(usdt.get("availableBalance"))
        unrealized = self._float(usdt.get("unrealizedProfit"))
        margin_balance = self._float(usdt.get("marginBalance"))

        # V2 account response also exposes aggregate values at the top-level.
        if wallet is None:
            wallet = self._float(account.get("totalWalletBalance"))
        if available is None:
            available = self._float(account.get("availableBalance"))
        if unrealized is None:
            unrealized = self._float(account.get("totalUnrealizedProfit"))
        if margin_balance is None:
            margin_balance = self._float(account.get("totalMarginBalance"))

        return {
            "environment": "TESTNET" if self.testnet else "PRODUCTION",
            "connected": True,
            "read_only": self.read_only,
            "orders_enabled": not self.read_only,
            "asset": "USDT",
            "wallet_balance_usdt": wallet,
            "available_balance_usdt": available,
            "margin_balance_usdt": margin_balance,
            "unrealized_pnl_usdt": unrealized,
            "total_initial_margin": self._float(account.get("totalInitialMargin")),
            "total_maint_margin": self._float(account.get("totalMaintMargin")),
            "total_position_initial_margin": self._float(account.get("totalPositionInitialMargin")),
            "total_open_order_initial_margin": self._float(account.get("totalOpenOrderInitialMargin")),
            "cross_wallet_balance": self._float(usdt.get("crossWalletBalance")),
            "cross_unrealized_pnl": self._float(usdt.get("crossUnPnl")),
            "positions": positions,
            "open_orders": orders,
            "open_position_count": len(positions),
            "open_order_count": len(orders),
            "update_time": account.get("updateTime"),
        }

    # ------------------------------------------------------------------
    # Execution. Dashboard/account clients use read_only=True and cannot call.
    # ------------------------------------------------------------------
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
        if self.read_only:
            raise PermissionError("Order submission is disabled for this read-only Binance client")
        url = f"{self.base_url}/fapi/v1/order"
        params: Dict[str, Any] = {
            "symbol": symbol,
            "side": side.upper(),
            "type": order_type.upper(),
            "quantity": f"{quantity:.3f}",
        }
        if price is not None:
            params["price"] = f"{price:.2f}"
            params["timeInForce"] = "GTC"
        if stop_price is not None:
            params["stopPrice"] = f"{stop_price:.2f}"
        if reduce_only:
            params["reduceOnly"] = "true"
        signed_params = self._sign(params)
        resp = self.session.post(url, params=signed_params, timeout=self.timeout)
        resp.raise_for_status()
        return resp.json()
