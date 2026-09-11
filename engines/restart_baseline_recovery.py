"""Deterministic reconstruction of an active position's immutable baseline."""

from __future__ import annotations

from typing import Any, Dict, Iterable, Optional


def _number(value: Any) -> Optional[float]:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _time(row: Dict[str, Any]) -> int:
    return int(row.get("createTime") or row.get("time") or row.get("updateTime") or row.get("timestamp") or 0)


class RestartBaselineRecovery:
    """Prove entry fills and the first lifecycle-linked protective stop."""

    STOP_WINDOW_MS = 30 * 60 * 1000

    def __init__(self, symbol: str = "BTCUSDT"):
        self.symbol = symbol

    @staticmethod
    def _position_amount(position: Dict[str, Any]) -> float:
        return _number(position.get("position_amt") or position.get("positionAmt")) or 0.0

    def recover_entry(self, position: Dict[str, Any], trades: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
        expected = self._position_amount(position)
        if not expected:
            return {"verified": False, "reason": "ACTIVE_POSITION_UNAVAILABLE"}
        signed_position = 0.0
        lifecycle: list[Dict[str, Any]] = []
        for trade in sorted(trades, key=_time):
            if str(trade.get("symbol") or self.symbol).upper() != self.symbol:
                continue
            quantity = abs(_number(trade.get("qty") or trade.get("quantity") or trade.get("executedQty")) or 0.0)
            if quantity <= 0:
                continue
            signed = quantity if str(trade.get("side") or "").upper() == "BUY" else -quantity
            previous = signed_position
            signed_position += signed
            if abs(previous) < 1e-12:
                lifecycle = []
            lifecycle.append({**trade, "_signed_quantity": signed})
            if abs(signed_position) < 1e-12:
                lifecycle = []
        tolerance = max(1e-8, abs(expected) * 1e-6)
        if not lifecycle or abs(signed_position - expected) > tolerance:
            return {"verified": False, "reason": "ACTIVE_LIFECYCLE_FILLS_UNPROVEN"}
        entry_sign = 1 if expected > 0 else -1
        entry_fills = [row for row in lifecycle if row["_signed_quantity"] * entry_sign > 0]
        if not entry_fills:
            return {"verified": False, "reason": "ENTRY_FILLS_UNAVAILABLE"}
        order_ids = {row.get("orderId") or row.get("order_id") for row in entry_fills}
        if None in order_ids or len(order_ids) != 1:
            return {"verified": False, "reason": "ENTRY_ORDER_AMBIGUOUS"}
        initial_quantity = sum(abs(row["_signed_quantity"]) for row in entry_fills)
        notional = sum(abs(row["_signed_quantity"]) * (_number(row.get("price")) or 0.0) for row in entry_fills)
        entry_price = notional / initial_quantity if initial_quantity else None
        exchange_entry = _number(position.get("entry_price") or position.get("entryPrice"))
        price_tolerance = max(1e-8, (exchange_entry or 0.0) * 1e-5)
        if entry_price is None or exchange_entry is None or abs(entry_price - exchange_entry) > price_tolerance:
            return {"verified": False, "reason": "ENTRY_PRICE_MISMATCH"}
        return {
            "verified": True,
            "actual_entry_price": entry_price,
            "actual_initial_position_size": initial_quantity,
            "entry_opened_at": min(_time(row) for row in entry_fills),
            "entry_completed_at": max(_time(row) for row in entry_fills),
            "entry_order_id": next(iter(order_ids)),
            "direction": "LONG" if expected > 0 else "SHORT",
        }

    @staticmethod
    def _type(order: Dict[str, Any]) -> str:
        return str(order.get("orderType") or order.get("type") or "").upper()

    @staticmethod
    def _trigger(order: Dict[str, Any]) -> Optional[float]:
        return _number(order.get("triggerPrice") or order.get("stopPrice") or order.get("stop_price"))

    @staticmethod
    def _quantity(order: Dict[str, Any]) -> Optional[float]:
        return _number(order.get("quantity") or order.get("origQty") or order.get("orig_quantity"))

    @staticmethod
    def _protective_semantics(order: Dict[str, Any]) -> bool:
        keys = [key for key in ("reduceOnly", "reduce_only", "closePosition", "close_position") if key in order]
        if not keys:
            return True
        return any(str(order.get(key)).strip().lower() in {"true", "1", "yes"} for key in keys)

    def recover(self, position: Dict[str, Any], trades: Iterable[Dict[str, Any]], normal_orders: Iterable[Dict[str, Any]], algo_orders: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
        entry = self.recover_entry(position, trades)
        if not entry.get("verified"):
            return entry
        direction = entry["direction"]
        closing_side = "SELL" if direction == "LONG" else "BUY"
        first_time = int(entry["entry_completed_at"])
        last_time = first_time + self.STOP_WINDOW_MS
        initial_quantity = float(entry["actual_initial_position_size"])
        quantity_tolerance = max(1e-8, initial_quantity * 1e-6)
        candidates = []
        for order in [*normal_orders, *algo_orders]:
            created = _time(order)
            trigger = self._trigger(order)
            quantity = self._quantity(order)
            close_position = str(order.get("closePosition") or order.get("close_position") or "").lower() in {"true", "1", "yes"}
            if str(order.get("symbol") or "").upper() != self.symbol:
                continue
            if self._type(order) != "STOP_MARKET" or str(order.get("side") or "").upper() != closing_side:
                continue
            if not self._protective_semantics(order) or not (first_time <= created <= last_time):
                continue
            if not close_position and (quantity is None or abs(quantity - initial_quantity) > quantity_tolerance):
                continue
            if trigger is None or (direction == "LONG" and trigger >= entry["actual_entry_price"]) or (direction == "SHORT" and trigger <= entry["actual_entry_price"]):
                continue
            candidates.append((created, order, trigger))
        if not candidates:
            return {**entry, "verified": False, "reason": "ORIGINAL_INITIAL_STOP_UNVERIFIED"}
        candidates.sort(key=lambda item: (item[0], int(item[1].get("algoId") or item[1].get("orderId") or 0)))
        created, stop_order, trigger = candidates[0]
        return {
            **entry,
            "verified": True,
            "actual_initial_stop": trigger,
            "initial_stop_source": "BINANCE_HISTORICAL_ORDER",
            "historical_stop_order_id": stop_order.get("algoId") or stop_order.get("orderId"),
            "historical_stop_created_at": created,
            "exchange_baseline_verified": True,
            "context_status": "RECONSTRUCTED_VERIFIED",
        }

    @staticmethod
    def rebuild_excursions(direction: str, entry: float, initial_stop: float, entry_opened_at: int, candles: Iterable[Dict[str, Any]]) -> Dict[str, float]:
        risk = abs(entry - initial_stop)
        mfe, mae = 0.0, 0.0
        if risk <= 0:
            return {"mfe_r": mfe, "mae_r": mae}
        for candle in candles:
            if candle.get("is_closed") is False or int(candle.get("timestamp") or candle.get("time", 0) * 1000) < entry_opened_at:
                continue
            high, low = _number(candle.get("high")), _number(candle.get("low"))
            if high is None or low is None:
                continue
            favorable = (high - entry) / risk if direction == "LONG" else (entry - low) / risk
            adverse = (low - entry) / risk if direction == "LONG" else (entry - high) / risk
            mfe, mae = max(mfe, favorable), min(mae, adverse)
        return {"mfe_r": mfe, "mae_r": mae}
