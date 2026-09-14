"""Authoritative position-lifecycle accounting from Binance TESTNET fills.

Binance user-trade rows are fills, not completed trades. This module rebuilds
flat -> position -> flat lifecycles, accounts for commissions, preserves
entry/exit prices, and annotates exchange-observable exit reasons.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional


EPSILON = 1e-12


def _float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _time(row: Dict[str, Any]) -> int:
    return int(row.get("time") or row.get("timestamp") or 0)


def _order_id(row: Dict[str, Any]) -> Optional[str]:
    value = row.get("orderId") or row.get("order_id")
    return str(value) if value not in (None, "") else None


@dataclass
class _Lifecycle:
    opened_at: Optional[int]
    direction: Optional[str]
    initial_quantity: float = 0.0
    entry_notional: float = 0.0
    closed_at: Optional[int] = None
    exit_quantity: float = 0.0
    exit_notional: float = 0.0
    realized_pnl: float = 0.0
    commission: float = 0.0
    fill_count: int = 0
    entry_order_ids: List[str] = field(default_factory=list)
    exit_order_ids: List[str] = field(default_factory=list)
    left_censored: bool = False

    @property
    def entry_price(self) -> Optional[float]:
        return self.entry_notional / self.initial_quantity if self.initial_quantity else None

    @property
    def exit_price(self) -> Optional[float]:
        return self.exit_notional / self.exit_quantity if self.exit_quantity else None

    @property
    def net_pnl(self) -> float:
        # Binance USER_DATA trade commissions are positive costs.
        return self.realized_pnl - self.commission


class DailyTradeLedger:
    """Build authoritative daily trade details from signed Binance evidence."""

    @staticmethod
    def _classify_exit(
        lifecycle: _Lifecycle,
        order_history: Iterable[Dict[str, Any]],
        algo_history: Iterable[Dict[str, Any]],
    ) -> str:
        ids = set(lifecycle.exit_order_ids)
        candidates: List[Dict[str, Any]] = []
        for row in order_history:
            oid = row.get("orderId")
            if oid is not None and str(oid) in ids:
                candidates.append(row)
        for row in algo_history:
            identifiers = {
                row.get("actualOrderId"),
                row.get("orderId"),
                row.get("algoId"),
            }
            if any(value is not None and str(value) in ids for value in identifiers):
                candidates.append(row)

        types = " ".join(
            str(row.get("type") or row.get("orderType") or "").upper()
            for row in candidates
        )
        clients = " ".join(
            str(
                row.get("clientOrderId")
                or row.get("clientAlgoId")
                or row.get("origClientOrderId")
                or ""
            ).lower()
            for row in candidates
        )
        if "smoke" in clients:
            return "SMOKE_TEST"
        if "STOP" in types:
            return "STOP_LOSS"
        if "TAKE_PROFIT" in types:
            return "TAKE_PROFIT"
        if lifecycle.net_pnl > 0:
            return "MARKET_EXIT_PROFIT"
        if lifecycle.net_pnl < 0:
            return "MARKET_EXIT_LOSS"
        return "MARKET_EXIT"

    @classmethod
    def lifecycles(
        cls,
        rows: Iterable[Dict[str, Any]],
        symbol: str = "BTCUSDT",
        *,
        ending_position: Optional[float] = None,
    ) -> List[_Lifecycle]:
        trade_rows = [
            dict(row)
            for row in rows
            if str(row.get("symbol") or symbol).upper() == symbol.upper()
        ]
        trade_rows.sort(key=lambda row: (_time(row), int(row.get("id") or 0)))

        deltas: List[float] = []
        for row in trade_rows:
            qty = abs(_float(row.get("qty") or row.get("quantity") or row.get("executedQty")))
            if qty <= EPSILON:
                deltas.append(0.0)
                continue
            deltas.append(qty if str(row.get("side") or "").upper() == "BUY" else -qty)

        position = (
            float(ending_position) - sum(deltas)
            if ending_position is not None
            else 0.0
        )
        active: Optional[_Lifecycle] = None
        completed: List[_Lifecycle] = []
        if abs(position) > EPSILON:
            active = _Lifecycle(
                opened_at=None,
                direction="LONG" if position > 0 else "SHORT",
                left_censored=True,
            )

        for row, signed in zip(trade_rows, deltas):
            if abs(signed) <= EPSILON:
                continue
            qty = abs(signed)
            price = _float(row.get("price") or row.get("avgPrice"))
            commission = abs(_float(row.get("commission")))
            realized = _float(row.get("realizedPnl") or row.get("realized_pnl"))
            oid = _order_id(row)
            previous = position
            next_position = previous + signed

            if abs(previous) <= EPSILON:
                active = _Lifecycle(
                    opened_at=_time(row),
                    direction="LONG" if signed > 0 else "SHORT",
                )
                active.initial_quantity += qty
                active.entry_notional += qty * price
                active.commission += commission
                active.fill_count += 1
                if oid:
                    active.entry_order_ids.append(oid)
                position = next_position
                continue

            same_direction_add = previous * signed > 0
            closing_fill = previous * signed < 0

            if same_direction_add:
                if active is None:
                    active = _Lifecycle(
                        opened_at=None,
                        direction="LONG" if previous > 0 else "SHORT",
                        left_censored=True,
                    )
                active.initial_quantity += qty
                active.entry_notional += qty * price
                active.commission += commission
                active.fill_count += 1
                if oid:
                    active.entry_order_ids.append(oid)
                position = next_position
                continue

            if closing_fill:
                if active is None:
                    active = _Lifecycle(
                        opened_at=None,
                        direction="LONG" if previous > 0 else "SHORT",
                        left_censored=True,
                    )
                closing_qty = min(abs(previous), qty)
                closing_fraction = closing_qty / qty if qty else 1.0
                active.exit_quantity += closing_qty
                active.exit_notional += closing_qty * price
                active.realized_pnl += realized
                active.commission += commission * closing_fraction
                active.fill_count += 1
                if oid:
                    active.exit_order_ids.append(oid)

                returned_flat = abs(next_position) <= EPSILON
                reversed_side = previous * next_position < 0
                if returned_flat or reversed_side:
                    active.closed_at = _time(row)
                    if not active.left_censored:
                        completed.append(active)

                    if reversed_side:
                        residual = abs(next_position)
                        opening_fraction = residual / qty if qty else 0.0
                        active = _Lifecycle(
                            opened_at=_time(row),
                            direction="LONG" if next_position > 0 else "SHORT",
                            initial_quantity=residual,
                            entry_notional=residual * price,
                            commission=commission * opening_fraction,
                            fill_count=1,
                            entry_order_ids=[oid] if oid else [],
                        )
                    else:
                        active = None

            position = next_position

        if active is not None and not active.left_censored:
            # Keep an observable open lifecycle so opened/current counts stay
            # accurate, but it is never counted as a completed trade.
            completed.append(active)
        return completed

    def build(
        self,
        rows: Iterable[Dict[str, Any]],
        *,
        day_start_ms: int,
        day_end_ms: int,
        symbol: str = "BTCUSDT",
        ending_position: Optional[float] = None,
        order_history: Iterable[Dict[str, Any]] = (),
        algo_history: Iterable[Dict[str, Any]] = (),
    ) -> Dict[str, Any]:
        lifecycles = self.lifecycles(
            rows,
            symbol,
            ending_position=ending_position,
        )
        opened = [
            x for x in lifecycles
            if x.opened_at is not None and day_start_ms <= x.opened_at < day_end_ms
        ]
        closed = [
            x for x in lifecycles
            if x.closed_at is not None and day_start_ms <= x.closed_at < day_end_ms
        ]
        trades = []
        for index, lifecycle in enumerate(closed, start=1):
            exit_reason = self._classify_exit(lifecycle, order_history, algo_history)
            trades.append(
                {
                    "trade_no": index,
                    "direction": lifecycle.direction,
                    "entry_opened_at": lifecycle.opened_at,
                    "closed_at": lifecycle.closed_at,
                    "entry_price": lifecycle.entry_price,
                    "exit_price": lifecycle.exit_price,
                    "initial_quantity": lifecycle.initial_quantity,
                    "realized_pnl_usdt": lifecycle.realized_pnl,
                    "commission_usdt": -lifecycle.commission,
                    "net_pnl_usdt": lifecycle.net_pnl,
                    "fill_count": lifecycle.fill_count,
                    "exit_reason": exit_reason,
                    "exit_order_ids": list(dict.fromkeys(lifecycle.exit_order_ids)),
                }
            )

        return {
            "status": "AVAILABLE",
            "source": "BINANCE_TESTNET_USER_TRADES",
            "opened_trades_today": len(opened),
            "currently_opened_today": sum(1 for x in opened if x.closed_at is None),
            "closed_trades_today": len(closed),
            "winning_trades_today": sum(1 for x in closed if x.net_pnl > 0),
            "losing_trades_today": sum(1 for x in closed if x.net_pnl < 0),
            "net_closed_pnl_usdt": sum(x.net_pnl for x in closed),
            "realized_closed_pnl_usdt": sum(x.realized_pnl for x in closed),
            "commission_closed_usdt": -sum(x.commission for x in closed),
            "trades": trades,
            "lifecycles": [
                {
                    "entry_opened_at": x.opened_at,
                    "direction": x.direction,
                    "entry_price": x.entry_price,
                    "exit_price": x.exit_price,
                    "initial_quantity": x.initial_quantity,
                    "closed_at": x.closed_at,
                    "realized_pnl": x.realized_pnl,
                    "commission_usdt": -x.commission,
                    "net_pnl_usdt": x.net_pnl,
                    "fill_count": x.fill_count,
                }
                for x in lifecycles
            ],
        }
