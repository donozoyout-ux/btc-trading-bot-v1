"""Strictly TESTNET-only Binance USD-M execution client.

This module intentionally has no production endpoint and never logs signed
parameters. Binance remains the source of truth after every order mutation.
"""

from __future__ import annotations

import hashlib
import hmac
import time
import uuid
from decimal import Decimal, ROUND_DOWN, ROUND_UP
from typing import Any, Dict, List, Optional
from urllib.parse import urlencode

import requests


class ExecutionError(RuntimeError):
    def __init__(self, category: str):
        self.category = category
        super().__init__(category)


class BinanceFuturesExecutionClient:
    TESTNET_URL = "https://testnet.binancefuture.com"

    def __init__(
        self,
        api_key: Optional[str],
        api_secret: Optional[str],
        *,
        testnet: bool,
        timeout: int = 8,
        recv_window: int = 5000,
    ):
        if not testnet:
            raise ExecutionError("MAINNET_EXECUTION_BLOCKED")
        self.api_key = (api_key or "").strip() or None
        self.api_secret = (api_secret or "").strip() or None
        self.testnet = True
        self.base_url = self.TESTNET_URL
        self.timeout = timeout
        self.recv_window = max(1000, min(int(recv_window), 60000))
        self.session = requests.Session()
        if self.api_key:
            self.session.headers.update({"X-MBX-APIKEY": self.api_key})
        self._offset_ms: Optional[int] = None
        self._last_sync = 0.0
        self._exchange_info: Optional[Dict[str, Any]] = None

    @property
    def configured(self) -> bool:
        return bool(self.api_key and self.api_secret)

    def _require_credentials(self) -> None:
        if not self.configured:
            raise ExecutionError("ACCOUNT_UNAVAILABLE")

    @staticmethod
    def _category(response: requests.Response) -> str:
        try:
            code = int(response.json().get("code"))
        except (AttributeError, TypeError, ValueError):
            code = None
        return {
            -2014: "INVALID_API_KEY",
            -2015: "INVALID_API_KEY",
            -1022: "INVALID_SIGNATURE",
            -1021: "TIMESTAMP_ERROR",
            -2010: "ORDER_REJECTED",
            -2021: "ORDER_REJECTED",
            -2022: "REDUCE_ONLY_REJECTED",
        }.get(code, "EXCHANGE_ERROR")

    def get_server_time(self) -> int:
        try:
            response = self.session.get(f"{self.base_url}/fapi/v1/time", timeout=self.timeout)
            response.raise_for_status()
            return int(response.json()["serverTime"])
        except requests.RequestException:
            raise ExecutionError("NETWORK_ERROR") from None
        except (KeyError, TypeError, ValueError):
            raise ExecutionError("EXCHANGE_ERROR") from None

    def sync_server_time(self, force: bool = False) -> int:
        if not force and self._offset_ms is not None and time.monotonic() - self._last_sync < 900:
            return self._offset_ms
        before = int(time.time() * 1000)
        server = self.get_server_time()
        after = int(time.time() * 1000)
        self._offset_ms = server - ((before + after) // 2)
        self._last_sync = time.monotonic()
        return self._offset_ms

    def _signed(self, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        self._require_credentials()
        values = dict(params or {})
        values["recvWindow"] = self.recv_window
        values["timestamp"] = int(time.time() * 1000) + self.sync_server_time()
        query = urlencode(values)
        values["signature"] = hmac.new(
            self.api_secret.encode("utf-8"), query.encode("utf-8"), hashlib.sha256
        ).hexdigest()
        return values

    def _request(self, method: str, path: str, params: Optional[Dict[str, Any]] = None) -> Any:
        try:
            for attempt in range(2):
                response = self.session.request(
                    method,
                    f"{self.base_url}{path}",
                    params=self._signed(params),
                    timeout=self.timeout,
                )
                if response.status_code < 400:
                    return response.json()
                category = self._category(response)
                if category == "TIMESTAMP_ERROR" and attempt == 0:
                    self.sync_server_time(force=True)
                    continue
                raise ExecutionError(category)
            raise ExecutionError("TIMESTAMP_ERROR")
        except ExecutionError:
            raise
        except requests.RequestException:
            raise ExecutionError("NETWORK_ERROR") from None
        except (TypeError, ValueError):
            raise ExecutionError("EXCHANGE_ERROR") from None

    def get_account(self) -> Dict[str, Any]:
        return self._request("GET", "/fapi/v2/account")

    def get_account_summary(self) -> Dict[str, Any]:
        account = self.get_account()
        usdt = next((row for row in account.get("assets", []) if row.get("asset") == "USDT"), {})
        positions = [
            row
            for row in self.get_positions()
            if Decimal(str(row.get("positionAmt", "0"))) != 0
        ]
        orders = self.get_open_orders() + self.get_open_algo_orders()
        number = lambda value: None if value in (None, "") else float(value)
        return {
            "wallet_balance": number(usdt.get("walletBalance")),
            "available_balance": number(usdt.get("availableBalance")),
            "margin_balance": number(usdt.get("marginBalance") or account.get("totalMarginBalance")),
            "unrealized_pnl": number(account.get("totalUnrealizedProfit")),
            "positions": positions,
            "open_orders": orders,
        }

    def get_positions(self, symbol: Optional[str] = None) -> List[Dict[str, Any]]:
        params = {"symbol": symbol} if symbol else None
        payload = self._request("GET", "/fapi/v2/positionRisk", params)
        return payload if isinstance(payload, list) else []

    def get_position(self, symbol: str = "BTCUSDT") -> Dict[str, Any]:
        rows = self.get_positions(symbol)
        active = next((row for row in rows if Decimal(str(row.get("positionAmt", "0"))) != 0), None)
        if active is None:
            return {"symbol": symbol, "position_amt": 0.0, "side": "FLAT"}
        amount = float(active.get("positionAmt", 0))
        return {
            "symbol": symbol,
            "position_amt": amount,
            "side": "LONG" if amount > 0 else "SHORT",
            "entry_price": float(active.get("entryPrice") or 0),
            "mark_price": float(active.get("markPrice") or 0),
            "unrealized_pnl": float(active.get("unRealizedProfit") or 0),
            "leverage": int(float(active.get("leverage") or 0)),
        }

    def get_open_orders(self, symbol: Optional[str] = None) -> List[Dict[str, Any]]:
        params = {"symbol": symbol} if symbol else None
        payload = self._request("GET", "/fapi/v1/openOrders", params)
        return payload if isinstance(payload, list) else []

    def get_order(self, symbol: str, order_id: Optional[int] = None, client_order_id: Optional[str] = None) -> Dict[str, Any]:
        params: Dict[str, Any] = {"symbol": symbol}
        if order_id is not None:
            params["orderId"] = order_id
        elif client_order_id:
            params["origClientOrderId"] = client_order_id
        else:
            raise ValueError("order_id or client_order_id is required")
        return self._request("GET", "/fapi/v1/order", params)

    def cancel_order(self, symbol: str, order_id: int) -> Dict[str, Any]:
        return self._request("DELETE", "/fapi/v1/order", {"symbol": symbol, "orderId": order_id})

    def get_open_algo_orders(self, symbol: Optional[str] = None) -> List[Dict[str, Any]]:
        """Return current TP/SL orders from Binance's dedicated algo surface."""
        params: Dict[str, Any] = {"algoType": "CONDITIONAL"}
        if symbol:
            params["symbol"] = symbol
        payload = self._request("GET", "/fapi/v1/openAlgoOrders", params)
        return payload if isinstance(payload, list) else []

    def get_algo_order(
        self,
        *,
        algo_id: Optional[int] = None,
        client_algo_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        params: Dict[str, Any] = {}
        if algo_id is not None:
            params["algoId"] = algo_id
        elif client_algo_id:
            params["clientAlgoId"] = client_algo_id
        else:
            raise ValueError("algo_id or client_algo_id is required")
        return self._request("GET", "/fapi/v1/algoOrder", params)

    def cancel_algo_order(
        self,
        *,
        algo_id: Optional[int] = None,
        client_algo_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        params: Dict[str, Any] = {}
        if algo_id is not None:
            params["algoId"] = algo_id
        elif client_algo_id:
            params["clientAlgoId"] = client_algo_id
        else:
            raise ValueError("algo_id or client_algo_id is required")
        return self._request("DELETE", "/fapi/v1/algoOrder", params)

    def cancel_all_algo_open_orders(self, symbol: str) -> Dict[str, Any]:
        return self._request("DELETE", "/fapi/v1/algoOpenOrders", {"symbol": symbol})

    def get_exchange_info(self, force: bool = False) -> Dict[str, Any]:
        if self._exchange_info is not None and not force:
            return self._exchange_info
        try:
            response = self.session.get(f"{self.base_url}/fapi/v1/exchangeInfo", timeout=self.timeout)
            response.raise_for_status()
            self._exchange_info = response.json()
            return self._exchange_info
        except requests.RequestException:
            raise ExecutionError("NETWORK_ERROR") from None

    def _filters(self, symbol: str) -> Dict[str, Dict[str, Any]]:
        info = self.get_exchange_info()
        row = next((item for item in info.get("symbols", []) if item.get("symbol") == symbol), None)
        if row is None:
            raise ExecutionError("SYMBOL_UNAVAILABLE")
        return {item.get("filterType"): item for item in row.get("filters", [])}

    def normalize_quantity(self, symbol: str, quantity: float, *, market: bool = True, price: Optional[float] = None) -> float:
        filters = self._filters(symbol)
        lot = filters.get("MARKET_LOT_SIZE") if market else None
        if not lot or Decimal(str(lot.get("stepSize", "0"))) == 0:
            lot = filters.get("LOT_SIZE", {})
        step = Decimal(str(lot.get("stepSize", "0")))
        minimum = Decimal(str(lot.get("minQty", "0")))
        maximum = Decimal(str(lot.get("maxQty", "999999999")))
        value = Decimal(str(quantity))
        if step <= 0:
            raise ExecutionError("INVALID_EXCHANGE_FILTER")
        value = (value / step).to_integral_value(rounding=ROUND_DOWN) * step
        if value < minimum:
            value = minimum
        if value > maximum:
            raise ExecutionError("QUANTITY_EXCEEDS_MAXIMUM")
        notional_filter = filters.get("MIN_NOTIONAL") or filters.get("NOTIONAL") or {}
        min_notional = Decimal(str(notional_filter.get("notional") or notional_filter.get("minNotional") or "0"))
        if price is not None and min_notional > 0 and value * Decimal(str(price)) < min_notional:
            required = min_notional / Decimal(str(price))
            value = (required / step).to_integral_value(rounding=ROUND_UP) * step
            if value > maximum:
                raise ExecutionError("MIN_NOTIONAL_NOT_MET")
        return float(value)

    def normalize_price(self, symbol: str, price: float) -> float:
        price_filter = self._filters(symbol).get("PRICE_FILTER", {})
        tick = Decimal(str(price_filter.get("tickSize", "0")))
        if tick <= 0:
            raise ExecutionError("INVALID_EXCHANGE_FILTER")
        return float((Decimal(str(price)) / tick).to_integral_value(rounding=ROUND_DOWN) * tick)

    @staticmethod
    def _order_record(order: Dict[str, Any], requested_quantity: float, reduce_only: bool) -> Dict[str, Any]:
        return {
            "client_order_id": order.get("clientOrderId"),
            "binance_order_id": order.get("orderId"),
            "symbol": order.get("symbol"),
            "side": order.get("side"),
            "type": order.get("type"),
            "requested_quantity": requested_quantity,
            "executed_quantity": float(order.get("executedQty") or 0),
            "average_fill_price": float(order.get("avgPrice") or 0),
            "status": order.get("status"),
            "reduce_only": bool(order.get("reduceOnly", reduce_only)),
            "timestamp": order.get("updateTime") or order.get("time") or int(time.time() * 1000),
        }

    @staticmethod
    def _algo_order_record(order: Dict[str, Any], requested_quantity: float) -> Dict[str, Any]:
        return {
            "client_order_id": order.get("clientAlgoId"),
            "binance_order_id": order.get("algoId"),
            "symbol": order.get("symbol"),
            "side": order.get("side"),
            "type": order.get("orderType"),
            "requested_quantity": requested_quantity,
            "executed_quantity": float(order.get("actualQty") or 0),
            "average_fill_price": float(order.get("actualPrice") or 0),
            "status": order.get("algoStatus"),
            "reduce_only": bool(order.get("reduceOnly", True)),
            "trigger_price": float(order.get("triggerPrice") or 0),
            "timestamp": order.get("updateTime") or order.get("createTime") or int(time.time() * 1000),
        }

    def place_market_order(self, symbol: str, side: str, quantity: float, *, reduce_only: bool = False, client_order_id: Optional[str] = None) -> Dict[str, Any]:
        cid = client_order_id or f"btc-demo-{uuid.uuid4().hex[:20]}"
        params: Dict[str, Any] = {
            "symbol": symbol,
            "side": side.upper(),
            "type": "MARKET",
            "quantity": format(Decimal(str(quantity)), "f"),
            "newClientOrderId": cid,
            "newOrderRespType": "RESULT",
        }
        if reduce_only:
            params["reduceOnly"] = "true"
        posted = self._request("POST", "/fapi/v1/order", params)
        confirmed = self.get_order(symbol, order_id=posted.get("orderId"))
        return self._order_record(confirmed, quantity, reduce_only)

    def place_protective_order(self, symbol: str, side: str, order_type: str, quantity: float, stop_price: float) -> Dict[str, Any]:
        normalized_price = self.normalize_price(symbol, stop_price)
        params = {
            "algoType": "CONDITIONAL",
            "symbol": symbol,
            "side": side.upper(),
            "type": order_type,
            "quantity": format(Decimal(str(quantity)), "f"),
            "triggerPrice": format(Decimal(str(normalized_price)), "f"),
            "reduceOnly": "true",
            "workingType": "MARK_PRICE",
            "clientAlgoId": f"btc-protect-{uuid.uuid4().hex[:16]}",
        }
        posted = self._request("POST", "/fapi/v1/algoOrder", params)
        algo_id = posted.get("algoId")
        if algo_id is None:
            raise ExecutionError("PROTECTION_FAILURE")
        confirmed = self.get_algo_order(algo_id=int(algo_id))
        return self._algo_order_record(confirmed, quantity)

    def close_position_market(self, symbol: str = "BTCUSDT") -> Optional[Dict[str, Any]]:
        position = self.get_position(symbol)
        amount = float(position.get("position_amt") or 0)
        if amount == 0:
            return None
        side = "SELL" if amount > 0 else "BUY"
        quantity = self.normalize_quantity(symbol, abs(amount), market=True, price=position.get("mark_price"))
        return self.place_market_order(symbol, side, quantity, reduce_only=True)
