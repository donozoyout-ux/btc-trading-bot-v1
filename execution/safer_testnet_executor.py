"""Safer TESTNET executor extensions.

Adds exchange-valid TP1/TP2 quantity splitting and richer journal context while
preserving the existing TESTNET-only execution boundary and recovery logic.
"""

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

from data.binance_execution_client import ExecutionError
from execution.testnet_executor import TestnetExecutor


class SaferTestnetExecutor(TestnetExecutor):
    """TestnetExecutor with split profit targets and contextual journaling."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._entry_context: Dict[str, Any] = {}

    @staticmethod
    def _safe_float(value: Any) -> Optional[float]:
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    def _capture_context(self, snapshot: Dict[str, Any]) -> Dict[str, Any]:
        decision = snapshot.get("decision") or {}
        strategy = snapshot.get("strategy") or {}
        meta = snapshot.get("meta") or {}
        plan = strategy.get("trade_plan") or decision.get("trade_plan") or {}
        return {
            "setup_type": strategy.get("setup_type") or decision.get("setup"),
            "direction": strategy.get("direction") or decision.get("setup_direction"),
            "regime": decision.get("regime"),
            "volatility": decision.get("volatility"),
            "location": decision.get("location"),
            "trigger": strategy.get("entry_trigger_state") or decision.get("trigger_state"),
            "derivatives": decision.get("derivatives"),
            "risk_reward": plan.get("risk_reward"),
            "market_data_source": meta.get("market_data_source"),
            "market_basis": meta.get("market_basis"),
        }

    def _record_order(
        self,
        decision_id: str,
        action: str,
        order: Dict[str, Any],
        before: Dict[str, Any],
        after: Dict[str, Any],
    ) -> None:
        self.last_order = order
        self.execution_journal.record(
            decision_id=decision_id,
            action=action,
            side=order.get("side"),
            quantity=order.get("executed_quantity") or order.get("requested_quantity"),
            price=order.get("average_fill_price"),
            binance_order_id=order.get("binance_order_id"),
            status=str(order.get("status") or "UNKNOWN"),
            position_before=before,
            position_after=after,
            context=self._entry_context,
        )

    def _split_target_quantities(
        self,
        quantity: float,
        plan: Dict[str, Any],
    ) -> Tuple[Optional[float], float]:
        """Return exchange-valid TP1/TP2 quantities without exceeding position.

        Very small positions may be impossible to split because of Binance lot
        size. In that case TP1 is omitted and TP2 safely covers the full size.
        """
        if quantity <= 0:
            raise ExecutionError("INVALID_POSITION_SIZE")
        try:
            tp1_qty = self.client.normalize_quantity(
                "BTCUSDT",
                quantity * 0.5,
                market=False,
                price=self._safe_float(plan.get("tp1")),
            )
        except Exception:
            return None, quantity
        if tp1_qty <= 0 or tp1_qty >= quantity:
            return None, quantity
        remainder = quantity - tp1_qty
        try:
            tp2_qty = self.client.normalize_quantity(
                "BTCUSDT",
                remainder,
                market=False,
                price=self._safe_float(plan.get("tp2")),
            )
        except Exception:
            return None, quantity
        tolerance = max(1e-12, quantity * 1e-9)
        if tp2_qty <= 0 or tp2_qty > remainder + tolerance:
            return None, quantity
        if tp1_qty + tp2_qty > quantity + tolerance:
            return None, quantity
        return tp1_qty, tp2_qty

    def _place_protection(
        self,
        decision_id: str,
        side: str,
        quantity: float,
        plan: Dict[str, Any],
    ) -> list[Dict[str, Any]]:
        close_side = "SELL" if side == "BUY" else "BUY"
        tp1_qty, tp2_qty = self._split_target_quantities(quantity, plan)

        stop = self.client.place_protective_order(
            "BTCUSDT",
            close_side,
            "STOP_MARKET",
            quantity,
            float(plan["stop_loss"]),
        )
        stop["role"] = "STOP"
        orders = [stop]

        if tp1_qty is not None:
            tp1 = self.client.place_protective_order(
                "BTCUSDT",
                close_side,
                "TAKE_PROFIT_MARKET",
                tp1_qty,
                float(plan["tp1"]),
            )
            tp1["role"] = "TP1"
            orders.append(tp1)

        tp2 = self.client.place_protective_order(
            "BTCUSDT",
            close_side,
            "TAKE_PROFIT_MARKET",
            tp2_qty,
            float(plan["tp2"]),
        )
        tp2["role"] = "TP2" if tp1_qty is not None else "TP_FINAL"
        orders.append(tp2)

        open_ids = {row.get("algoId") for row in self.client.get_open_algo_orders("BTCUSDT")}
        if not all(order.get("binance_order_id") in open_ids for order in orders):
            raise ExecutionError("PROTECTION_FAILURE")

        self._protective_orders = list(orders)
        for order in orders:
            self._record_order(
                decision_id,
                f"PROTECTIVE_{order.get('role', 'ORDER')}",
                order,
                self._known_position,
                self._known_position,
            )
        return orders

    def process_snapshot(self, snapshot: Dict[str, Any], state) -> Optional[Dict[str, Any]]:
        self._entry_context = self._capture_context(snapshot)
        result = super().process_snapshot(snapshot, state)
        if result and result.get("status") == "OPENED":
            result["protection_plan"] = {
                "mode": "SPLIT_TP_WHEN_EXCHANGE_ALLOWS",
                "stop_quantity": abs(float((result.get("position") or {}).get("position_amt") or 0)),
                "tp_roles": [row.get("role") for row in result.get("protective_orders", [])],
            }
        return result
