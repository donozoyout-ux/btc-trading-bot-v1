"""Binance USD-M Futures REST clients.

The public client is used for credential-free production market data.  The
account client is a separate, TESTNET-only, read-only surface.  Keeping these
clients separate prevents dashboard code from accidentally inheriting order
submission capabilities.
"""

import time
import hmac
import hashlib
from typing import List, Dict, Any, Optional
from urllib.parse import urlencode
import requests
from loguru import logger

from core.models import Candle


class BinanceAccountError(RuntimeError):
    """A sanitized Binance account error safe to return through the API."""

    def __init__(self, category: str):
        super().__init__(category)
        self.category = category


class AccountConnectionBlocked(BinanceAccountError):
    """Raised when signed account access is attempted outside testnet."""

    def __init__(self):
        super().__init__("ACCOUNT_CONNECTION_BLOCKED")


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
        read_only: bool = False,
        recv_window_ms: int = 5000,
    ):
        self.api_key = api_key
        self.api_secret = api_secret
        self.testnet = testnet
        self.read_only = bool(read_only)
        self.recv_window_ms = max(1000, min(int(recv_window_ms), 60000))
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

    def get_account_balance(self) -> Optional[float]:
        """Fetches USDT wallet balance without inventing a fallback value.

        This legacy method remains for the explicit execution runtime.  The
        dashboard uses :class:`BinanceFuturesAccountClient` instead.
        """
        if not self.api_key or not self.api_secret:
            if self.read_only:
                raise RuntimeError("Binance account credentials are not configured")
            return None

        url = f"{self.base_url}/fapi/v2/account"
        params = self._sign({})
        resp = self.session.get(url, params=params, timeout=self.timeout)
        resp.raise_for_status()
        data = resp.json()
        for asset in data.get("assets", []):
            if asset.get("asset") == "USDT":
                return float(asset.get("walletBalance", 0.0))
        return None

    def _account_reader(self):
        """Compatibility bridge to the TESTNET-only account reader."""
        return BinanceFuturesAccountClient(
            api_key=self.api_key,
            api_secret=self.api_secret,
            testnet=self.testnet,
            timeout=self.timeout,
            recv_window=self.recv_window_ms,
        )

    def get_account_information(self) -> Dict[str, Any]:
        return self._account_reader()._signed_get("/fapi/v2/account")

    def get_position_risk(self, symbol: Optional[str] = None) -> List[Dict[str, Any]]:
        params = {"symbol": symbol} if symbol else None
        payload = self._account_reader()._signed_get("/fapi/v2/positionRisk", params)
        return payload if isinstance(payload, list) else []

    def get_open_orders(self, symbol: Optional[str] = None) -> List[Dict[str, Any]]:
        params = {"symbol": symbol} if symbol else None
        payload = self._account_reader()._signed_get("/fapi/v1/openOrders", params)
        return payload if isinstance(payload, list) else []

    @staticmethod
    def _float(value: Any) -> Optional[float]:
        if value in (None, ""):
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    def get_account_summary(self) -> Dict[str, Any]:
        """V1-compatible summary; the dashboard uses the safer account client."""
        account = self.get_account_information()
        position_rows = self.get_position_risk()
        order_rows = self.get_open_orders()
        usdt = next((row for row in account.get("assets", []) if row.get("asset") == "USDT"), {})
        positions = []
        for row in position_rows:
            amount = self._float(row.get("positionAmt")) or 0.0
            if amount == 0.0:
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
                    "mark_price": self._float(row.get("markPrice")),
                    "unrealized_pnl": self._float(row.get("unRealizedProfit")),
                }
            )
        return {
            "environment": "TESTNET" if self.testnet else "MAINNET",
            "read_only": True,
            "orders_enabled": False,
            "wallet_balance_usdt": self._float(usdt.get("walletBalance")),
            "available_balance_usdt": self._float(usdt.get("availableBalance")),
            "margin_balance_usdt": self._float(usdt.get("marginBalance")),
            "unrealized_pnl_usdt": self._float(usdt.get("unrealizedProfit")),
            "open_position_count": len(positions),
            "open_order_count": len(order_rows),
            "positions": positions,
            "open_orders": order_rows,
        }

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
        """Order submission is deliberately unavailable in the demo phase."""
        if self.read_only:
            raise PermissionError("Order submission is disabled for this read-only Binance client")
        raise RuntimeError("ORDER_SUBMISSION_DISABLED")


class BinanceFuturesAccountClient:
    """TESTNET-only, signed USER_DATA reader with no order methods."""

    TESTNET_URL = BinanceFuturesClient.TESTNET_URL

    def __init__(
        self,
        api_key: Optional[str],
        api_secret: Optional[str],
        testnet: bool = True,
        timeout: int = 5,
        recv_window: int = 5000,
        read_only: bool = True,
    ):
        self.api_key = (api_key or "").strip() or None
        self.api_secret = (api_secret or "").strip() or None
        self.testnet = bool(testnet)
        self.read_only = True
        self.base_url = self.TESTNET_URL
        self.timeout = timeout
        self.recv_window = max(1000, min(int(recv_window), 60000))
        self.session = requests.Session()
        if self.api_key:
            self.session.headers.update({"X-MBX-APIKEY": self.api_key})
        self._server_time_offset_ms: Optional[int] = None
        self._time_synced_at_monotonic = 0.0

    @property
    def configured(self) -> bool:
        return bool(self.api_key and self.api_secret)

    def _assert_access_allowed(self) -> None:
        if not self.testnet:
            raise AccountConnectionBlocked()
        if not self.configured:
            raise BinanceAccountError("ACCOUNT_UNAVAILABLE")

    def get_server_time(self) -> int:
        """Read Binance testnet server time in milliseconds."""
        if not self.testnet:
            raise AccountConnectionBlocked()
        try:
            response = self.session.get(
                f"{self.TESTNET_URL}/fapi/v1/time", timeout=self.timeout
            )
            response.raise_for_status()
            return int(response.json()["serverTime"])
        except BinanceAccountError:
            raise
        except requests.RequestException:
            raise BinanceAccountError("NETWORK_ERROR") from None
        except (KeyError, TypeError, ValueError):
            raise BinanceAccountError("ACCOUNT_UNAVAILABLE") from None

    def sync_server_time(self, force: bool = False) -> int:
        """Calculate a midpoint-adjusted offset to Binance server time."""
        now_mono = time.monotonic()
        if (
            not force
            and self._server_time_offset_ms is not None
            and now_mono - self._time_synced_at_monotonic < 1800
        ):
            return self._server_time_offset_ms

        before = int(time.time() * 1000)
        server_time = self.get_server_time()
        after = int(time.time() * 1000)
        local_midpoint = (before + after) // 2
        self._server_time_offset_ms = server_time - local_midpoint
        self._time_synced_at_monotonic = now_mono
        return self._server_time_offset_ms

    def _signed_params(self, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Return signed parameters using synchronized server time.

        Callers must never log the returned mapping because it contains a
        signature.  This method deliberately works on a copy.
        """
        self._assert_access_allowed()
        offset = self.sync_server_time()
        signed: Dict[str, Any] = dict(params or {})
        signed["recvWindow"] = self.recv_window
        signed["timestamp"] = int(time.time() * 1000) + offset
        query_string = urlencode(signed)
        signed["signature"] = hmac.new(
            self.api_secret.encode("utf-8"),
            query_string.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        return signed

    @staticmethod
    def _error_category(response: requests.Response) -> str:
        try:
            code = int(response.json().get("code"))
        except (AttributeError, TypeError, ValueError):
            code = None
        return {
            -2014: "INVALID_API_KEY",
            -2015: "INVALID_API_KEY",
            -1022: "INVALID_SIGNATURE",
            -1021: "TIMESTAMP_ERROR",
        }.get(code, "ACCOUNT_UNAVAILABLE")

    def _signed_get(self, path: str, params: Optional[Dict[str, Any]] = None) -> Any:
        """Call one signed USER_DATA endpoint without logging query data."""
        try:
            for attempt in range(2):
                response = self.session.get(
                    f"{self.TESTNET_URL}{path}",
                    params=self._signed_params(params),
                    timeout=self.timeout,
                )
                if response.status_code < 400:
                    return response.json()
                category = self._error_category(response)
                if category == "TIMESTAMP_ERROR" and attempt == 0:
                    self.sync_server_time(force=True)
                    continue
                raise BinanceAccountError(category)
            raise BinanceAccountError("TIMESTAMP_ERROR")
        except BinanceAccountError:
            raise
        except requests.RequestException:
            raise BinanceAccountError("NETWORK_ERROR") from None
        except (TypeError, ValueError):
            raise BinanceAccountError("ACCOUNT_UNAVAILABLE") from None

    @staticmethod
    def _number(value: Any) -> Optional[float]:
        if value in (None, ""):
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    def get_account_balances(self, account: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        """Map every collateral asset returned by the account endpoint."""
        payload = account if account is not None else self._signed_get("/fapi/v2/account")
        balances = []
        for asset in payload.get("assets", []):
            balances.append(
                {
                    "asset": asset.get("asset"),
                    "wallet_balance": self._number(asset.get("walletBalance")),
                    "available_balance": self._number(asset.get("availableBalance")),
                    "cross_wallet_balance": self._number(asset.get("crossWalletBalance")),
                    "cross_unrealized_pnl": self._number(asset.get("crossUnPnl")),
                    "margin_balance": self._number(asset.get("marginBalance")),
                }
            )
        return balances

    def get_open_positions(self) -> List[Dict[str, Any]]:
        """Return only positions whose signed position amount is non-zero."""
        payload = self._signed_get("/fapi/v2/positionRisk")
        positions = []
        for item in payload:
            amount = self._number(item.get("positionAmt"))
            if amount is None or amount == 0:
                continue
            positions.append(
                {
                    "symbol": item.get("symbol"),
                    "side": "LONG" if amount > 0 else "SHORT",
                    "position_amount": amount,
                    "size": abs(amount),
                    "entry_price": self._number(item.get("entryPrice")),
                    "mark_price": self._number(item.get("markPrice")),
                    "notional": self._number(item.get("notional")),
                    "leverage": self._number(item.get("leverage")),
                    "margin_type": item.get("marginType"),
                    "unrealized_pnl": self._number(item.get("unRealizedProfit")),
                    "liquidation_price": self._number(item.get("liquidationPrice")),
                    "isolated_wallet": self._number(item.get("isolatedWallet")),
                    "break_even_price": self._number(item.get("breakEvenPrice")),
                }
            )
        return positions

    def get_open_orders(self) -> List[Dict[str, Any]]:
        """Read existing open orders; this client has no create/cancel surface."""
        payload = self._signed_get("/fapi/v1/openOrders")
        return [
            {
                "symbol": item.get("symbol"),
                "side": item.get("side"),
                "type": item.get("type"),
                "quantity": self._number(item.get("origQty")),
                "price": self._number(item.get("price")),
                "stop_price": self._number(item.get("stopPrice")),
                "status": item.get("status"),
                "reduce_only": bool(item.get("reduceOnly", False)),
                "order_id": item.get("orderId"),
                "update_time": item.get("updateTime") or item.get("time"),
            }
            for item in payload
        ]

    def get_account_summary(self) -> Dict[str, Any]:
        """Return the complete read-only USD-M testnet account snapshot."""
        self._assert_access_allowed()
        account = self._signed_get("/fapi/v2/account")
        balances = self.get_account_balances(account)
        usdt = next((item for item in balances if item.get("asset") == "USDT"), {})
        positions = self.get_open_positions()
        orders = self.get_open_orders()
        return {
            "account_type": "USD-M FUTURES",
            "environment": "TESTNET",
            "connected": True,
            "status": "CONNECTED",
            "error_category": None,
            "asset": "USDT",
            "wallet_balance": usdt.get("wallet_balance"),
            "available_balance": usdt.get("available_balance"),
            "margin_balance": usdt.get("margin_balance")
            if usdt.get("margin_balance") is not None
            else self._number(account.get("totalMarginBalance")),
            "unrealized_pnl": self._number(account.get("totalUnrealizedProfit")),
            "total_initial_margin": self._number(account.get("totalInitialMargin")),
            "total_maint_margin": self._number(account.get("totalMaintMargin")),
            "total_position_initial_margin": self._number(account.get("totalPositionInitialMargin")),
            "total_open_order_initial_margin": self._number(account.get("totalOpenOrderInitialMargin")),
            "update_time": account.get("updateTime"),
            "balances": balances,
            "positions": positions,
            "open_orders": orders,
        }
