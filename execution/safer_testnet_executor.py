"""Safer TESTNET executor extensions.

Adds exchange-valid TP1/TP2 quantity splitting and richer journal context while
preserving the existing TESTNET-only execution boundary and recovery logic.
"""

from __future__ import annotations

import time
from typing import Any, Dict, Optional, Tuple

from config.constants import ManagementProfile, MarketRegime, PositionManagementState, StructureType, TradeDirection, VolatilityLevel
from data.binance_execution_client import ExecutionError
from engines.position_manager import PositionManager
from execution.testnet_executor import TestnetExecutor


class SaferTestnetExecutor(TestnetExecutor):
    """TestnetExecutor with split profit targets and contextual journaling."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        persisted = self.execution_journal.read_state()
        self._entry_context: Dict[str, Any] = dict(persisted.get("entry_context") or {})
        self.last_management_closed_5m_timestamp = persisted.get("last_management_closed_5m_timestamp")
        self.target_replan_count = int(persisted.get("target_replan_count") or 0)
        self.last_target_replan_at = persisted.get("last_target_replan_at")
        self.management_mfe_r = float(persisted.get("management_mfe_r") or 0.0)
        self.management_mae_r = float(persisted.get("management_mae_r") or 0.0)
        self.last_management_decision = persisted.get("position_intelligence") or {}
        self.protection_reconciliation_required = bool(persisted.get("protection_reconciliation_required", False))
        self.last_partial_reconciliation = persisted.get("last_partial_reconciliation") or {}
        self._missing_context_warning_emitted = bool(persisted.get("missing_context_warning_emitted", False))
        self.position_manager = PositionManager(
            recovery_wait_enabled=getattr(self.settings, "RECOVERY_WAIT_ENABLED", True),
            early_exit_enabled=getattr(self.settings, "EARLY_EXIT_ENABLED", True),
            breakeven_min_r=getattr(self.settings, "BREAKEVEN_MIN_R", 1.0),
            stop_tighten_min_r=getattr(self.settings, "STOP_TIGHTEN_MIN_R", 1.5),
            stop_lock_r=getattr(self.settings, "STOP_LOCK_R", 0.25),
            target_replan_enabled=getattr(self.settings, "TARGET_REPLAN_ENABLED", True),
            target_replan_min_r=getattr(self.settings, "TARGET_REPLAN_MIN_R", 1.5),
            target_replan_cooldown_bars=getattr(self.settings, "TARGET_REPLAN_COOLDOWN_BARS", 3),
            max_target_replans=getattr(self.settings, "MAX_TARGET_REPLANS", 2),
        )

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
            "planned_entry": plan.get("entry_price") or decision.get("price"),
            "planned_stop": plan.get("stop_loss"),
            "tp1": plan.get("tp1"),
            "tp2": plan.get("tp2"),
            "management_profile": plan.get("management_profile", ManagementProfile.BALANCED.value),
            "market_data_source": meta.get("market_data_source"),
            "market_basis": meta.get("market_basis"),
        }

    def _has_verified_exchange_baseline(self) -> bool:
        required = ("actual_entry_price", "actual_initial_position_size", "actual_initial_stop", "entry_decision_id", "entry_opened_at")
        return self._entry_context.get("exchange_baseline_verified") is True and all(
            self._entry_context.get(key) is not None for key in required
        )

    def _capture_verified_exchange_baseline(self, snapshot: Dict[str, Any], result: Dict[str, Any]) -> None:
        """Freeze exchange-normalized entry risk only after protection exists."""
        position = result.get("position") or {}
        entry_order = result.get("entry") or {}
        stop_order = next((row for row in result.get("protective_orders", []) if row.get("role") == "STOP"), {})
        actual_entry = self._safe_float(position.get("entry_price")) or self._safe_float(entry_order.get("average_fill_price"))
        actual_size = abs(self._safe_float(position.get("position_amt")) or 0.0)
        actual_stop = self._trigger_price(stop_order)
        if not actual_entry or actual_size <= 0 or actual_stop is None:
            raise ExecutionError("EXCHANGE_BASELINE_UNAVAILABLE")
        decision_id = str(snapshot.get("decision_id") or (snapshot.get("decision") or {}).get("evaluation_id") or "")
        self._entry_context.update({
            "exchange_baseline_verified": True,
            "actual_entry_price": actual_entry,
            "actual_initial_position_size": actual_size,
            "actual_initial_stop": actual_stop,
            "entry_decision_id": decision_id,
            "entry_opened_at": entry_order.get("timestamp") or entry_order.get("transaction_time") or entry_order.get("update_time") or int(time.time() * 1000),
        })

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
        profile = str(plan.get("management_profile") or ManagementProfile.BALANCED.value)
        fraction = {
            ManagementProfile.CONSERVATIVE.value: getattr(self.settings, "TP_SPLIT_CONSERVATIVE", 0.70),
            ManagementProfile.TREND_RUNNER.value: getattr(self.settings, "TP_SPLIT_TREND_RUNNER", 0.35),
        }.get(profile, getattr(self.settings, "TP_SPLIT_BALANCED", 0.50))
        try:
            tp1_qty = self.client.normalize_quantity(
                "BTCUSDT",
                quantity * fraction,
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
        previous_context = dict(self._entry_context)
        self._entry_context = self._capture_context(snapshot)
        risk = (snapshot.get("decision") or {}).get("risk_assessment") or {}
        self._entry_context["planned_size"] = risk.get("position_size_btc")
        try:
            result = super().process_snapshot(snapshot, state)
        except Exception:
            self._entry_context = previous_context
            self._write_runtime_state()
            raise
        if result and result.get("status") == "OPENED":
            self._capture_verified_exchange_baseline(snapshot, result)
            self._missing_context_warning_emitted = False
            self.protection_reconciliation_required = False
            self.last_management_closed_5m_timestamp = None
            self.target_replan_count = 0
            self.last_target_replan_at = None
            self.management_mfe_r = 0.0
            self.management_mae_r = 0.0
            self.last_management_decision = {}
            self._write_runtime_state(last_execution_result="OPENED")
            result["protection_plan"] = {
                "mode": "SPLIT_TP_WHEN_EXCHANGE_ALLOWS",
                "stop_quantity": abs(float((result.get("position") or {}).get("position_amt") or 0)),
                "tp_roles": [row.get("role") for row in result.get("protective_orders", [])],
            }
        else:
            # A rejected/duplicate candidate must never overwrite the immutable
            # baseline of an exchange position already under management.
            self._entry_context = previous_context
            self._write_runtime_state()
        return result

    def _write_runtime_state(self, *args, **kwargs) -> None:
        super()._write_runtime_state(*args, **kwargs)
        persisted = self.execution_journal.read_state()
        persisted.update({
            "last_management_closed_5m_timestamp": getattr(self, "last_management_closed_5m_timestamp", None),
            "target_replan_count": getattr(self, "target_replan_count", 0),
            "last_target_replan_at": getattr(self, "last_target_replan_at", None),
            "management_mfe_r": getattr(self, "management_mfe_r", 0.0),
            "management_mae_r": getattr(self, "management_mae_r", 0.0),
            "position_intelligence": getattr(self, "last_management_decision", {}),
            "entry_context": getattr(self, "_entry_context", {}),
            "protection_reconciliation_required": getattr(self, "protection_reconciliation_required", False),
            "last_partial_reconciliation": getattr(self, "last_partial_reconciliation", {}),
            "missing_context_warning_emitted": getattr(self, "_missing_context_warning_emitted", False),
        })
        self.execution_journal.write_state(persisted)

    @staticmethod
    def _order_type(order: Dict[str, Any]) -> str:
        return str(order.get("orderType") or order.get("type") or "").upper()

    @staticmethod
    def _trigger_price(order: Dict[str, Any]) -> Optional[float]:
        return SaferTestnetExecutor._safe_float(order.get("triggerPrice") or order.get("trigger_price"))

    @staticmethod
    def _order_quantity(order: Dict[str, Any]) -> Optional[float]:
        return SaferTestnetExecutor._safe_float(
            order.get("quantity") or order.get("origQty") or order.get("requested_quantity")
        )

    def _validate_stop(self, order: Dict[str, Any], position: Dict[str, Any], expected_quantity: float) -> bool:
        close_side = "SELL" if float(position.get("position_amt") or 0) > 0 else "BUY"
        quantity = self._order_quantity(order)
        tolerance = max(1e-12, expected_quantity * 1e-9)
        return all([
            self._order_type(order) == "STOP_MARKET",
            str(order.get("side") or "").upper() == close_side,
            self._is_reduce_only(order),
            quantity is not None and abs(quantity - expected_quantity) <= tolerance,
        ])

    def _mark_protection_reconciliation_required(self, position: Dict[str, Any], reason: str) -> None:
        self.protection_reconciliation_required = True
        self.execution_journal.record(
            decision_id=self._entry_context.get("entry_decision_id"),
            action="PROTECTION_RECONCILIATION_REQUIRED",
            status="FAIL_CLOSED",
            reason=reason,
            position_after=position,
        )
        self._write_runtime_state(last_execution_result="PROTECTION_RECONCILIATION_REQUIRED")

    def _replace_stop_safely(self, position: Dict[str, Any], new_stop: float) -> None:
        orders = self.client.get_open_algo_orders("BTCUSDT")
        old_stops = [order for order in orders if self._order_type(order) == "STOP_MARKET"]
        if not old_stops:
            raise ExecutionError("UNPROTECTED_TESTNET_POSITION")
        side = "SELL" if float(position["position_amt"]) > 0 else "BUY"
        quantity = abs(float(position["position_amt"]))
        new_order = self.client.place_protective_order("BTCUSDT", side, "STOP_MARKET", quantity, new_stop)
        new_id = new_order.get("binance_order_id")
        verified = self.client.get_open_algo_orders("BTCUSDT")
        verified_new = next((row for row in verified if row.get("algoId") == new_id), None)
        if verified_new is None or not self._validate_stop(verified_new, position, quantity):
            raise ExecutionError("PROTECTION_REPLACEMENT_FAILED")
        try:
            for old in old_stops:
                if old.get("algoId") != new_id:
                    self.client.cancel_algo_order(algo_id=int(old["algoId"]))
        except Exception:
            self._mark_protection_reconciliation_required(position, "OLD_STOP_CANCELLATION_FAILED")
            raise ExecutionError("PROTECTION_RECONCILIATION_REQUIRED") from None
        final = self.client.get_open_algo_orders("BTCUSDT")
        final_stops = [row for row in final if self._order_type(row) == "STOP_MARKET"]
        if len(final_stops) != 1 or final_stops[0].get("algoId") != new_id or not self._validate_stop(final_stops[0], position, quantity):
            self._mark_protection_reconciliation_required(position, "STOP_SET_MISMATCH")
            raise ExecutionError("PROTECTION_RECONCILIATION_REQUIRED")
        new_order["role"] = "STOP"
        self._protective_orders = [row for row in self._protective_orders if row.get("role") != "STOP"] + [new_order]
        self.protection_reconciliation_required = False

    def recover_from_exchange(self) -> Dict[str, Any]:
        result = super().recover_from_exchange()
        position = result["position"]
        if float(position.get("position_amt") or 0) == 0:
            self._entry_context = {}
            self._missing_context_warning_emitted = False
            self.protection_reconciliation_required = False
            self._write_runtime_state()
        elif not self._has_verified_exchange_baseline():
            self.last_management_decision = {
                "state": "NO_CHANGE",
                "reason_codes": ["RECOVERED_POSITION_CONTEXT_UNAVAILABLE"],
                "adaptive_actions": "NONE",
            }
            if not self._missing_context_warning_emitted:
                self.execution_journal.record(
                    decision_id=None,
                    action="RECOVERED_POSITION_CONTEXT_UNAVAILABLE",
                    status="FAIL_CLOSED",
                    reason="Verified immutable exchange entry baseline is unavailable",
                    position_after=position,
                )
                self._notify(
                    "OPERATOR_WARNING",
                    {"message": "BTC TESTNET position protected; adaptive management disabled because restart context is unavailable"},
                    "RECOVERED_POSITION_CONTEXT_UNAVAILABLE:BTCUSDT",
                )
                self._missing_context_warning_emitted = True
            self._write_runtime_state(last_execution_result="RECOVERED_POSITION_CONTEXT_UNAVAILABLE")
            result["status"] = "RECOVERED_POSITION_CONTEXT_UNAVAILABLE"
        return result

    def reconcile_position(self) -> Dict[str, Any]:
        before = dict(self._known_position)
        before_size = abs(float(before.get("position_amt") or 0))
        after = super().reconcile_position()
        after_size = abs(float(after.get("position_amt") or 0))
        tolerance = max(1e-12, before_size * 1e-9)
        if before_size > after_size + tolerance and after_size > 0:
            key = f"{before_size:.12g}->{after_size:.12g}"
            if self.last_partial_reconciliation.get("key") == key:
                return after
            open_algo = self.client.get_open_algo_orders("BTCUSDT")
            open_ids = {row.get("algoId") for row in open_algo}
            completed = [row for row in self._protective_orders if row.get("binance_order_id") not in open_ids]
            known_tp = next((row for row in completed if row.get("role") == "TP1"), None)
            cause = "TP1_FILLED" if known_tp else "UNKNOWN_PARTIAL_REDUCTION"
            self._protective_orders = [row for row in self._protective_orders if row.get("binance_order_id") in open_ids]
            stops = [row for row in open_algo if self._order_type(row) == "STOP_MARKET"]
            if not stops:
                self._mark_protection_reconciliation_required(after, "STOP_MISSING_AFTER_PARTIAL_REDUCTION")
                raise ExecutionError("PROTECTION_RECONCILIATION_REQUIRED")
            if len(stops) != 1 or not self._validate_stop(stops[0], after, after_size):
                current_stop = self._trigger_price(stops[0])
                if current_stop is None:
                    self._mark_protection_reconciliation_required(after, "STOP_INVALID_AFTER_PARTIAL_REDUCTION")
                    raise ExecutionError("PROTECTION_RECONCILIATION_REQUIRED")
                self._replace_stop_safely(after, current_stop)
                open_algo = self.client.get_open_algo_orders("BTCUSDT")
            targets = [row for row in open_algo if self._order_type(row) == "TAKE_PROFIT_MARKET"]
            target_total = sum(self._order_quantity(row) or 0.0 for row in targets)
            if target_total > after_size + max(1e-12, after_size * 1e-9):
                self._mark_protection_reconciliation_required(after, "TARGET_QUANTITY_EXCEEDS_REMAINING_POSITION")
                raise ExecutionError("PROTECTION_RECONCILIATION_REQUIRED")
            if known_tp:
                self.execution_journal.record(decision_id=self._entry_context.get("entry_decision_id"), action="TP1_FILLED", status="CONFIRMED", position_before=before, position_after=after)
            self.execution_journal.record(
                decision_id=self._entry_context.get("entry_decision_id"), action="PARTIAL_POSITION_RECONCILED",
                status="CONFIRMED", reason=cause, position_before=before, position_after=after,
                details={"remaining_quantity": after_size},
            )
            self.last_partial_reconciliation = {"key": key, "cause": cause, "remaining_quantity": after_size}
            self._write_runtime_state(last_execution_result="PARTIAL_POSITION_RECONCILED")
        elif after_size == 0:
            self._entry_context = {}
            self._missing_context_warning_emitted = False
            self.protection_reconciliation_required = False
            self._write_runtime_state()
        return after

    def manage_existing_position(self, state) -> Dict[str, Any]:
        result = super().manage_existing_position(state)
        position = result.get("position") or {}
        if self.protection_reconciliation_required and float(position.get("position_amt") or 0) != 0:
            stops = [row for row in self.client.get_open_algo_orders("BTCUSDT") if self._order_type(row) == "STOP_MARKET"]
            quantity = abs(float(position.get("position_amt") or 0))
            if len(stops) == 1 and self._validate_stop(stops[0], position, quantity):
                self.protection_reconciliation_required = False
                self.execution_journal.record(
                    decision_id=self._entry_context.get("entry_decision_id"),
                    action="PROTECTION_RECONCILED", status="CONFIRMED", position_after=position,
                )
                self._write_runtime_state(last_execution_result="PROTECTION_RECONCILED")
        if float(position.get("position_amt") or 0) != 0 and not self._has_verified_exchange_baseline():
            result["status"] = "RECOVERED_POSITION_CONTEXT_UNAVAILABLE"
            result["position_intelligence"] = self.last_management_decision or {
                "state": "NO_CHANGE", "reason_codes": ["RECOVERED_POSITION_CONTEXT_UNAVAILABLE"], "adaptive_actions": "NONE",
            }
        return result

    def _replace_target_safely(self, position: Dict[str, Any], new_target: float, role: str) -> None:
        orders = self.client.get_open_algo_orders("BTCUSDT")
        stops = [order for order in orders if self._order_type(order) == "STOP_MARKET"]
        if not stops:
            raise ExecutionError("UNPROTECTED_TESTNET_POSITION")
        targets = [order for order in orders if self._order_type(order) == "TAKE_PROFIT_MARKET"]
        if not targets:
            raise ExecutionError("PROTECTION_REPLACEMENT_FAILED")
        is_long = float(position["position_amt"]) > 0
        ordered = sorted(targets, key=lambda row: self._trigger_price(row) or 0, reverse=not is_long)
        old_target = ordered[0] if role == "TP1" else ordered[-1]
        side = "SELL" if is_long else "BUY"
        quantity = self._safe_float(old_target.get("quantity") or old_target.get("requested_quantity")) or abs(float(position["position_amt"]))
        new_order = self.client.place_protective_order("BTCUSDT", side, "TAKE_PROFIT_MARKET", quantity, new_target)
        new_id = new_order.get("binance_order_id")
        if new_id not in {row.get("algoId") for row in self.client.get_open_algo_orders("BTCUSDT")}:
            raise ExecutionError("PROTECTION_REPLACEMENT_FAILED")
        self.client.cancel_algo_order(algo_id=int(old_target["algoId"]))
        final = self.client.get_open_algo_orders("BTCUSDT")
        final_ids = {row.get("algoId") for row in final}
        if new_id not in final_ids or not any(self._order_type(row) == "STOP_MARKET" for row in final):
            raise ExecutionError("PROTECTION_REPLACEMENT_FAILED")
        new_order["role"] = role
        removed_roles = {role, "TP_FINAL"} if role == "TP2" else {role}
        self._protective_orders = [row for row in self._protective_orders if row.get("role") not in removed_roles] + [new_order]

    def _replace_tp1_safely(self, position: Dict[str, Any], new_tp1: float) -> None:
        self._replace_target_safely(position, new_tp1, "TP1")

    def _replace_tp2_safely(self, position: Dict[str, Any], new_tp2: float) -> None:
        self._replace_target_safely(position, new_tp2, "TP2")

    def _take_partial_safely(self, position: Dict[str, Any], quantity: float) -> Dict[str, Any]:
        """Execute a reduce-only partial close while an exchange stop remains live."""
        before_size = abs(float(position.get("position_amt") or 0))
        if quantity <= 0 or quantity >= before_size:
            raise ExecutionError("INVALID_REDUCE_ONLY_QUANTITY")
        orders = self.client.get_open_algo_orders("BTCUSDT")
        if not any(self._order_type(row) == "STOP_MARKET" for row in orders):
            raise ExecutionError("UNPROTECTED_TESTNET_POSITION")
        order = self.client.reduce_position_market("BTCUSDT", quantity)
        after = self.client.get_position("BTCUSDT")
        after_size = abs(float(after.get("position_amt") or 0))
        if after_size >= before_size or after_size > before_size - quantity + max(1e-9, before_size * 1e-6):
            raise ExecutionError("PARTIAL_EXIT_RECONCILIATION_FAILED")
        if not any(self._order_type(row) == "STOP_MARKET" for row in self.client.get_open_algo_orders("BTCUSDT")):
            raise ExecutionError("UNPROTECTED_TESTNET_POSITION")
        return {"order": order, "position": after}

    def _early_exit_and_reconcile(self, position: Dict[str, Any], state, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Close, clean and prove a flat exchange state before confirmation."""
        try:
            self.client.close_position_market("BTCUSDT")
            after_close = self.client.get_position("BTCUSDT")
            self._known_position = after_close
            if float(after_close.get("position_amt") or 0) != 0:
                raise ExecutionError("EARLY_EXIT_POSITION_NOT_FLAT")
            cleanup = self.cleanup_flat_reduce_only_orders()
            final_position = self.client.get_position("BTCUSDT")
            remaining = self.client.get_open_orders("BTCUSDT") + self.client.get_open_algo_orders("BTCUSDT")
            stale_protection = [row for row in remaining if self._is_reduce_only(row)]
            if float(final_position.get("position_amt") or 0) != 0 or stale_protection:
                raise ExecutionError("EARLY_EXIT_RECONCILIATION_FAILED")
            self._known_position = final_position
            self._protective_orders = []
            self._entry_context = {}
            self.protection_reconciliation_required = False
            self.execution_journal.record(
                decision_id=None, action="EARLY_EXIT", status="CONFIRMED", details=payload,
                position_before=position, position_after=final_position,
            )
            return {"position": final_position, "stale_orders_cancelled": cleanup["cancelled"]}
        except Exception as exc:
            verified_stop = False
            current = None
            try:
                current = self.client.get_position("BTCUSDT")
                if float(current.get("position_amt") or 0) != 0:
                    orders = self.client.get_open_algo_orders("BTCUSDT")
                    expected = abs(float(current.get("position_amt") or 0))
                    verified_stop = any(self._validate_stop(row, current, expected) for row in orders)
            except Exception:
                verified_stop = False
            if current is None or (float(current.get("position_amt") or 0) != 0 and not verified_stop):
                state.activate_emergency_latch("EARLY_EXIT_RECONCILIATION_FAILURE")
            self.execution_journal.record(
                decision_id=None, action="EARLY_EXIT_RECONCILIATION_FAILURE",
                status="EXISTING_STOP_PRESERVED" if verified_stop else "KILL_SWITCH",
                reason=getattr(exc, "category", type(exc).__name__), details=payload,
                position_before=position, position_after=current,
            )
            self._write_runtime_state(last_execution_result="EARLY_EXIT_RECONCILIATION_FAILURE")
            raise ExecutionError("EARLY_EXIT_RECONCILIATION_FAILED") from None

    def manage_adaptive_position(self, snapshot: Dict[str, Any], state, position: Dict[str, Any]) -> Dict[str, Any]:
        if not getattr(self.settings, "ADAPTIVE_MANAGEMENT_ENABLED", True):
            return {"status": "POSITION_MANAGEMENT", "position": position}
        if self.protection_reconciliation_required:
            return {
                "status": "PROTECTION_RECONCILIATION_REQUIRED", "position": position,
                "position_intelligence": {"state": "NO_CHANGE", "reason_codes": ["PROTECTION_RECONCILIATION_REQUIRED"], "adaptive_actions": "NONE"},
            }
        if not self._has_verified_exchange_baseline():
            payload = {"state": "NO_CHANGE", "reason_codes": ["RECOVERED_POSITION_CONTEXT_UNAVAILABLE"], "adaptive_actions": "NONE"}
            self.last_management_decision = payload
            if not self._missing_context_warning_emitted:
                self.execution_journal.record(
                    decision_id=None, action="RECOVERED_POSITION_CONTEXT_UNAVAILABLE", status="FAIL_CLOSED",
                    reason="Verified immutable exchange entry baseline is unavailable", position_after=position,
                )
                self._notify("OPERATOR_WARNING", {"message": "Adaptive management disabled: verified entry context unavailable"}, "RECOVERED_POSITION_CONTEXT_UNAVAILABLE:BTCUSDT")
                self._missing_context_warning_emitted = True
            self._write_runtime_state(last_execution_result="RECOVERED_POSITION_CONTEXT_UNAVAILABLE")
            return {"status": "RECOVERED_POSITION_CONTEXT_UNAVAILABLE", "position": position, "position_intelligence": payload}
        candles = snapshot.get("candles", {}).get("5m") or []
        if not candles:
            return {"status": "MANAGEMENT_NO_CHANGE", "position": position}
        candle_ts = int(candles[-1]["time"] * 1000)
        if candle_ts == self.last_management_closed_5m_timestamp:
            return {"status": "DUPLICATE_MANAGEMENT_CANDLE", "position": position, "position_intelligence": self.last_management_decision}
        self.last_management_closed_5m_timestamp = candle_ts
        orders = self.client.get_open_algo_orders("BTCUSDT")
        stops = [o for o in orders if self._order_type(o) == "STOP_MARKET"]
        targets = [o for o in orders if self._order_type(o) == "TAKE_PROFIT_MARKET"]
        current_stop = self._trigger_price(stops[0]) if stops else None
        is_long = float(position.get("position_amt") or 0) > 0
        prices = [self._trigger_price(o) for o in targets if self._trigger_price(o) is not None]
        current_tp2 = (max(prices) if is_long else min(prices)) if prices else None
        entry = float(self._entry_context["actual_entry_price"])
        initial_stop = float(self._entry_context["actual_initial_stop"])
        initial_size = float(self._entry_context["actual_initial_position_size"])
        if initial_stop is None or entry <= 0:
            return {"status": "MANAGEMENT_NO_CHANGE", "position": position}
        mark = float(position.get("mark_price") or snapshot.get("market", {}).get("mark_price") or snapshot.get("market", {}).get("price") or 0)
        risk = abs(entry - float(initial_stop))
        observed_r = ((mark - entry) if is_long else (entry - mark)) / risk if risk > 0 and mark > 0 else 0.0
        if mark > 0:
            self.management_mfe_r = max(self.management_mfe_r, observed_r)
            self.management_mae_r = min(self.management_mae_r, observed_r)
        frame = snapshot.get("chart_intelligence", {}).get("timeframes", {}).get("5m", {})
        direction = TradeDirection.LONG if is_long else TradeDirection.SHORT
        momentum_support, momentum_opposing, momentum_available = self.position_manager.normalize_momentum(
            direction, frame.get("trend")
        )
        volume_support, volume_available = self.position_manager.normalize_volume(frame.get("volume_state"))
        frame_available = (
            str(frame.get("status") or "").upper() == "AVAILABLE"
            and int(frame.get("closed_candles") or 0) > 0
        )
        zones = snapshot.get("zones") or []
        levels = [float(z.get("center") or 0) for z in zones]
        structural = sorted([x for x in levels if x > (current_tp2 or entry)]) if is_long else sorted([x for x in levels if 0 < x < (current_tp2 or entry)], reverse=True)
        source = snapshot.get("sources", {}).get("binance", {})
        decision = self.position_manager.evaluate(
            direction=direction, entry=entry, initial_stop=float(initial_stop), current_stop=float(current_stop or initial_stop),
            mark=mark,
            initial_size=initial_size, current_size=abs(float(position.get("position_amt") or 0)),
            structure=StructureType(str(frame.get("structure") or StructureType.MIXED.value)), last_bos=frame.get("bos"), last_choch=frame.get("choch"),
            regime=MarketRegime(str(snapshot.get("decision", {}).get("regime") or MarketRegime.RANGE.value)),
            volatility=VolatilityLevel(str(snapshot.get("decision", {}).get("volatility") or VolatilityLevel.NORMAL.value)),
            momentum_support=momentum_support, momentum_opposing=momentum_opposing,
            momentum_available=momentum_available, volume_support=volume_support,
            volume_available=volume_available,
            data_healthy=mark > 0 and frame_available and source.get("status") == "HEALTHY" and source.get("market_data_trading_safe", True) is not False,
            candle_closed=True, candle_timestamp=candle_ts, current_tp2=current_tp2, candidate_tp2=structural[0] if structural else None,
            target_replan_count=self.target_replan_count, last_target_replan_at=self.last_target_replan_at,
            mfe_r=self.management_mfe_r, mae_r=self.management_mae_r,
            management_profile=ManagementProfile(str(self._entry_context.get("management_profile") or ManagementProfile.BALANCED.value)),
        )
        tp1 = (min(prices) if is_long else max(prices)) if prices else None
        new_stop = decision.stop_action.get("new_stop")
        new_tp2 = decision.target_action.get("new_tp2")
        payload = decision.model_dump(mode="json")
        payload.update({
            "position_side": direction.value,
            "entry": entry,
            "mark": position.get("mark_price"),
            "unrealized_pnl": position.get("unrealized_pnl"),
            "old_stop": current_stop,
            "new_stop": new_stop if new_stop is not None else current_stop,
            "initial_stop": initial_stop,
            "old_tp1": tp1,
            "new_tp1": tp1,
            "old_tp2": current_tp2,
            "new_tp2": new_tp2 if new_tp2 is not None else current_tp2,
            "regime": snapshot.get("decision", {}).get("regime"),
            "volatility": snapshot.get("decision", {}).get("volatility"),
            "structure": frame.get("structure"),
            "timestamp": candle_ts,
        })
        self.last_management_decision = payload
        try:
            if decision.state == PositionManagementState.EXIT_EARLY:
                self.execution_journal.record(decision_id=None, action="THESIS_INVALIDATED", status="CONFIRMED", details=payload)
                reconciled = self._early_exit_and_reconcile(position, state, payload)
                position = reconciled["position"]
                event = "EARLY_EXIT"
            elif decision.stop_action.get("action") == "TIGHTEN_STOP":
                self._replace_stop_safely(position, float(decision.stop_action["new_stop"]))
                event = "STOP_TIGHTENED"
            elif decision.target_action.get("action") == "REPLACE_TP2":
                self._replace_tp2_safely(position, float(decision.target_action["new_tp2"]))
                self.target_replan_count = decision.target_replan_count
                self.last_target_replan_at = decision.last_target_replan_at
                event = "TP2_REPLANNED"
            else:
                event = "RECOVERY_WAIT" if decision.state == PositionManagementState.RECOVERY_WAIT else "POSITION_HOLD" if decision.state == PositionManagementState.HOLD else "MANAGEMENT_NO_CHANGE"
        except Exception as exc:
            if decision.state == PositionManagementState.EXIT_EARLY:
                raise
            if self.protection_reconciliation_required:
                raise ExecutionError("PROTECTION_RECONCILIATION_REQUIRED") from None
            remaining = self.client.get_open_algo_orders("BTCUSDT")
            stop_active = any(self._order_type(row) == "STOP_MARKET" for row in remaining)
            if not stop_active:
                state.activate_emergency_latch("PROTECTION_REPLACEMENT_FAILURE")
            self.execution_journal.record(
                decision_id=None,
                action="PROTECTION_REPLACEMENT_FAILURE",
                status="EXISTING_STOP_PRESERVED" if stop_active else "KILL_SWITCH",
                reason=getattr(exc, "category", type(exc).__name__),
                details=payload,
            )
            self._write_runtime_state(last_execution_result="PROTECTION_REPLACEMENT_FAILURE")
            raise ExecutionError("PROTECTION_REPLACEMENT_FAILED") from None
        if event != "EARLY_EXIT":
            self.execution_journal.record(decision_id=None, action=event, status="CONFIRMED", details=payload)
        self._write_runtime_state(last_execution_result=decision.state.value)
        return {"status": decision.state.value, "position": position, "position_intelligence": payload}
