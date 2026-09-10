"""Position-lifecycle accounting for the Turkish dashboard.

Binance user-trade rows are fills, not trades.  This module folds fills into
flat-to-position-to-flat lifecycles so partial exits never inflate trade counts.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional


def _float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _time(row: Dict[str, Any]) -> int:
    return int(row.get("time") or row.get("timestamp") or 0)


@dataclass
class _Lifecycle:
    opened_at: int
    initial_quantity: float
    entry_notional: float
    closed_at: Optional[int] = None
    realized_pnl: float = 0.0

    @property
    def entry_price(self) -> Optional[float]:
        return self.entry_notional / self.initial_quantity if self.initial_quantity else None


class DailyTradeLedger:
    """Build authoritative daily counts from signed Binance fill history."""

    @staticmethod
    def lifecycles(rows: Iterable[Dict[str, Any]], symbol: str = "BTCUSDT") -> List[_Lifecycle]:
        position = 0.0
        active: Optional[_Lifecycle] = None
        completed: List[_Lifecycle] = []
        for row in sorted(rows, key=_time):
            if str(row.get("symbol") or symbol).upper() != symbol.upper():
                continue
            qty = abs(_float(row.get("qty") or row.get("quantity") or row.get("executedQty")))
            if qty <= 0:
                continue
            signed = qty if str(row.get("side") or "").upper() == "BUY" else -qty
            previous = position
            next_position = previous + signed
            # A fill that increases exposure from flat starts one lifecycle.
            if abs(previous) < 1e-12:
                active = _Lifecycle(
                    opened_at=_time(row),
                    initial_quantity=qty,
                    entry_notional=qty * _float(row.get("price")),
                )
            elif active is not None and previous * signed > 0:
                # Averaging is disabled, but preserve correct accounting if an
                # externally-created fill is observed.
                active.initial_quantity += qty
                active.entry_notional += qty * _float(row.get("price"))
            if active is not None:
                active.realized_pnl += _float(row.get("realizedPnl") or row.get("realized_pnl"))
            if previous and (abs(next_position) < 1e-12 or previous * next_position < 0):
                if active is not None:
                    active.closed_at = _time(row)
                    completed.append(active)
                active = None
                if previous * next_position < 0:
                    residual = abs(next_position)
                    active = _Lifecycle(_time(row), residual, residual * _float(row.get("price")))
            position = next_position
        if active is not None:
            completed.append(active)
        return completed

    def build(
        self,
        rows: Iterable[Dict[str, Any]],
        *,
        day_start_ms: int,
        day_end_ms: int,
        symbol: str = "BTCUSDT",
    ) -> Dict[str, Any]:
        lifecycles = self.lifecycles(rows, symbol)
        opened = [x for x in lifecycles if day_start_ms <= x.opened_at < day_end_ms]
        closed = [x for x in lifecycles if x.closed_at is not None and day_start_ms <= x.closed_at < day_end_ms]
        return {
            "status": "AVAILABLE",
            "source": "BINANCE_TESTNET_USER_TRADES",
            "opened_trades_today": len(opened),
            "currently_opened_today": sum(1 for x in opened if x.closed_at is None),
            "closed_trades_today": len(closed),
            "winning_trades_today": sum(1 for x in closed if x.realized_pnl > 0),
            "losing_trades_today": sum(1 for x in closed if x.realized_pnl < 0),
            "lifecycles": [
                {
                    "entry_opened_at": x.opened_at,
                    "entry_price": x.entry_price,
                    "initial_quantity": x.initial_quantity,
                    "closed_at": x.closed_at,
                    "realized_pnl": x.realized_pnl,
                }
                for x in lifecycles
            ],
        }
