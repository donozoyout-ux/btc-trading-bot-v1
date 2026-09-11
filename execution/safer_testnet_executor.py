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
from engines.restart_baseline_recovery import RestartBaselineRecovery
from execution.testnet_executor import TestnetExecutor


class SaferTestnetExecutor(TestnetExecutor):
    """TestnetExecutor with split profit targets and contextual journaling."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        persisted = self.execution_journal.read_state()
        self._entry_context: Dict[str, Any] = dict(persisted.get("entry_context") or {})
        if self._entry_context.get("exchange_baseline_verified") is True:
            previous_source = self._entry_context.get("initial_stop_source")
            if previous_source:
                self._entry_context["initial_stop_provenance"] = previous_source
            self._entry_context["initial_stop_source"] = "PERSISTED_ENTRY_CONTEXT"
        self.last_management_closed_5m_timestamp = persisted.get("last_management_closed_5m_timestamp")
        self.target_replan_count = int(persisted.get("target_replan_count") or 0)
        self.last_target_replan_at = persisted.get("last_target_replan_at")
        self.management_mfe_r = float(persisted.get("management_mfe_r") or 0.0)
        self.management_mae_r = float(persisted.get("management_mae_r") or 0.0)
        self.last_management_decision = persisted.get("position_intelligence") or {}
        self.protection_reconciliation_required = bool(persisted.get("protection_reconciliation_required", False))
        self.protection_reconciliation_reason = persisted.get("protection_reconciliation_reason")
        self.protection_reconciliation_expected_target_ids = list(persisted.get("protection_reconciliation_expected_target_ids") or [])
        self.last_partial_reconciliation = persisted.get("last_partial_reconciliation") or {}
        self._missing_context_warning_emitted = bool(persisted.get("missing_context_warning_emitted", False))
        self.profit_fade_partial_taken = bool(persisted.get("profit_fade_partial_taken", False))
        self.last_alert_type = persisted.get("last_alert_type")
        self.last_alert_reason = persisted.get("last_alert_reason")
        self.last_alert_sent_at = persisted.get("last_alert_sent_at")
        self.active_alert_state = persisted.get("active_alert_state") or "HEALTHY"
        self.last_management_alert_key = persisted.get("last_management_alert_key")
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
            profit_protection_enabled=getattr(self.settings, "PROFIT_PROTECTION_ENABLED", True),
            profit_arm_r=getattr(self.settings, "PROFIT_ARM_R", .60),
            breakeven_trigger_r=getattr(self.settings, "BREAKEVEN_TRIGGER_R", .75),
            profit_lock_1_trigger_r=getattr(self.settings, "PROFIT_LOCK_1_TRIGGER_R", 1.0),
            profit_lock_1_r=getattr(self.settings, "PROFIT_LOCK_1_R", .20),
            profit_lock_2_trigger_r=getattr(self.settings, "PROFIT_LOCK_2_TRIGGER_R", 1.5),
            profit_lock_2_r=getattr(self.settings, "PROFIT_LOCK_2_R", .60),
            profit_lock_3_trigger_r=getattr(self.settings, "PROFIT_LOCK_3_TRIGGER_R", 2.0),
            profit_lock_3_r=getattr(self.settings, "PROFIT_LOCK_3_R", 1.10),
            mfe_giveback_enabled=getattr(self.settings, "MFE_GIVEBACK_ENABLED", True),
            mfe_giveback_arm_r=getattr(self.settings, "MFE_GIVEBACK_ARM_R", .80),
            mfe_giveback_warn_r=getattr(self.settings, "MFE_GIVEBACK_WARN_R", .35),
            mfe_giveback_exit_r=getattr(self.settings, "MFE_GIVEBACK_EXIT_R", .55),
            profit_fade_partial_min_r=getattr(self.settings, "PROFIT_FADE_PARTIAL_MIN_R", .80),
            profit_fade_partial_fraction=getattr(self.settings, "PROFIT_FADE_PARTIAL_FRACTION", .35),
        )
        self.baseline_recovery = RestartBaselineRecovery("BTCUSDT")

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
        required = ("actual_entry_price", "actual_initial_position_size", "actual_initial_stop", "entry_opened_at")
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
        if self.protection_reconciliation_required:
            self._write_runtime_state(last_execution_result="PROTECTION_RECONCILIATION_REQUIRED")
            return {"status": "PROTECTION_RECONCILIATION_REQUIRED"}
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
            self.protection_reconciliation_reason = None
            self.protection_reconciliation_expected_target_ids = []
            self.last_management_closed_5m_timestamp = None
            self.target_replan_count = 0
            self.last_target_replan_at = None
            self.management_mfe_r = 0.0
            self.management_mae_r = 0.0
            self.last_management_decision = {}
            self.profit_fade_partial_taken = False
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
            "protection_reconciliation_reason": getattr(self, "protection_reconciliation_reason", None),
            "protection_reconciliation_expected_target_ids": getattr(self, "protection_reconciliation_expected_target_ids", []),
            "last_partial_reconciliation": getattr(self, "last_partial_reconciliation", {}),
            "missing_context_warning_emitted": getattr(self, "_missing_context_warning_emitted", False),
            "profit_fade_partial_taken": getattr(self, "profit_fade_partial_taken", False),
            "last_alert_type": getattr(self, "last_alert_type", None),
            "last_alert_reason": getattr(self, "last_alert_reason", None),
            "last_alert_sent_at": getattr(self, "last_alert_sent_at", None),
            "active_alert_state": getattr(self, "active_alert_state", "HEALTHY"),
            "last_management_alert_key": getattr(self, "last_management_alert_key", None),
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
        trigger = self._trigger_price(order)
        mark = self._safe_float(position.get("mark_price"))
        directionally_valid = bool(
            trigger is not None and mark is not None and mark > 0
            and (trigger < mark if close_side == "SELL" else trigger > mark)
        )
        tolerance = max(1e-12, expected_quantity * 1e-9)
        return all([
            self._order_type(order) == "STOP_MARKET",
            trigger is not None and trigger > 0,
            directionally_valid,
            str(order.get("side") or "").upper() == close_side,
            self._is_reduce_only(order),
            quantity is not None and abs(quantity - expected_quantity) <= tolerance,
        ])

    def _price_tick_gap(self, symbol: str, mark: float) -> float:
        getter = getattr(self.client, "price_tick_size", None)
        if callable(getter):
            return float(getter(symbol))
        return max(abs(mark) * 1e-9, 1e-8)

    def _validate_target(self, order: Dict[str, Any], position: Dict[str, Any]) -> bool:
        close_side = "SELL" if float(position.get("position_amt") or 0) > 0 else "BUY"
        quantity = self._order_quantity(order)
        trigger = self._trigger_price(order)
        return all([
            self._order_type(order) == "TAKE_PROFIT_MARKET",
            str(order.get("side") or "").upper() == close_side,
            self._is_reduce_only(order),
            quantity is not None and quantity > 0,
            trigger is not None and trigger > 0,
        ])

    def _protection_invariants_hold(self, position: Dict[str, Any], orders: list[Dict[str, Any]]) -> bool:
        quantity = abs(float(position.get("position_amt") or 0))
        tolerance = max(1e-12, quantity * 1e-9)
        stops = [row for row in orders if self._order_type(row) == "STOP_MARKET"]
        targets = [row for row in orders if self._order_type(row) == "TAKE_PROFIT_MARKET"]
        return all([
            len(stops) == 1,
            self._validate_stop(stops[0], position, quantity) if len(stops) == 1 else False,
            bool(targets),
            all(self._validate_target(row, position) for row in targets),
            sum(self._order_quantity(row) or 0.0 for row in targets) <= quantity + tolerance,
        ])

    def _mark_protection_reconciliation_required(self, position: Dict[str, Any], reason: str) -> None:
        should_record = not self.protection_reconciliation_required or self.protection_reconciliation_reason != reason
        self.protection_reconciliation_required = True
        self.protection_reconciliation_reason = reason
        if should_record:
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
        is_long = float(position["position_amt"]) > 0
        side = "SELL" if is_long else "BUY"
        quantity = abs(float(position["position_amt"]))
        mark_getter = getattr(self.client, "get_mark_price", None)
        mark = self._safe_float(mark_getter("BTCUSDT")) if callable(mark_getter) else self._safe_float(position.get("mark_price"))
        if mark is None or mark <= 0:
            raise ExecutionError("MARK_PRICE_UNAVAILABLE")
        normalizer = getattr(self.client, "normalize_price", None)
        normalized_stop = float(normalizer("BTCUSDT", new_stop)) if callable(normalizer) else float(new_stop)
        gap = self._price_tick_gap("BTCUSDT", mark)
        if (is_long and normalized_stop > mark - gap) or (not is_long and normalized_stop < mark + gap):
            raise ExecutionError("STOP_TRIGGER_WOULD_IMMEDIATELY_FIRE")
        old_trigger = self._trigger_price(old_stops[0])
        if old_trigger is not None and ((is_long and normalized_stop < old_trigger) or (not is_long and normalized_stop > old_trigger)):
            raise ExecutionError("STOP_WIDENING_BLOCKED")
        validation_position = dict(position, mark_price=mark)
        new_order = self.client.place_protective_order("BTCUSDT", side, "STOP_MARKET", quantity, normalized_stop)
        new_id = new_order.get("binance_order_id")
        verified = self.client.get_open_algo_orders("BTCUSDT")
        verified_new = next((row for row in verified if row.get("algoId") == new_id), None)
        if verified_new is None or not self._validate_stop(verified_new, validation_position, quantity):
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
        if len(final_stops) != 1 or final_stops[0].get("algoId") != new_id or not self._validate_stop(final_stops[0], validation_position, quantity):
            self._mark_protection_reconciliation_required(position, "STOP_SET_MISMATCH")
            raise ExecutionError("PROTECTION_RECONCILIATION_REQUIRED")
        new_order["role"] = "STOP"
        self._protective_orders = [row for row in self._protective_orders if row.get("role") != "STOP"] + [new_order]

    def _restore_protective_roles(self, position: Dict[str, Any], orders: list[Dict[str, Any]]) -> None:
        """Restore only roles implied unambiguously by exchange order shape/price."""
        targets = [row for row in orders if self._order_type(row) == "TAKE_PROFIT_MARKET"]
        is_long = float(position.get("position_amt") or 0) > 0
        role_by_id: Dict[Any, str] = {}
        if len(targets) == 2:
            prices = [self._trigger_price(row) for row in targets]
            if all(price is not None for price in prices) and prices[0] != prices[1]:
                ordered = sorted(targets, key=lambda row: self._trigger_price(row) or 0, reverse=not is_long)
                role_by_id[ordered[0].get("algoId")] = "TP1"
                role_by_id[ordered[1].get("algoId")] = "TP2"
        restored = []
        for row in orders:
            order_type = self._order_type(row)
            role = "STOP" if order_type == "STOP_MARKET" else role_by_id.get(row.get("algoId"), "UNKNOWN_TARGET")
            restored.append({
                "binance_order_id": row.get("algoId"),
                "client_order_id": row.get("clientAlgoId"),
                "type": order_type,
                "side": row.get("side"),
                "trigger_price": self._trigger_price(row),
                "requested_quantity": self._order_quantity(row),
                "reduce_only": self._is_reduce_only(row),
                "status": row.get("algoStatus"),
                "role": role,
            })
        self._protective_orders = restored

    def _reconcile_active_protection(self, position: Dict[str, Any], *, restart: bool = False) -> Dict[str, Any]:
        """Validate protection against current exchange size without strategy changes."""
        quantity = abs(float(position.get("position_amt") or 0))
        if quantity <= 0:
            return {"status": "FLAT", "stop_resized": False}
        orders = self.client.get_open_algo_orders("BTCUSDT")
        stops = [row for row in orders if self._order_type(row) == "STOP_MARKET"]
        stop_resized = False
        if len(stops) != 1:
            self._restore_protective_roles(position, orders)
            self._mark_protection_reconciliation_required(position, "AUTHORITATIVE_STOP_SET_AMBIGUOUS")
            return {"status": "PROTECTION_RECONCILIATION_REQUIRED", "stop_resized": False}
        stop = stops[0]
        trigger = self._trigger_price(stop)
        if trigger is None or trigger <= 0:
            self._restore_protective_roles(position, orders)
            self._mark_protection_reconciliation_required(position, "STOP_TRIGGER_INVALID")
            return {"status": "PROTECTION_RECONCILIATION_REQUIRED", "stop_resized": False}
        if not self._validate_stop(stop, position, quantity):
            old_trigger = trigger
            self._replace_stop_safely(position, old_trigger)
            orders = self.client.get_open_algo_orders("BTCUSDT")
            final_stop = next((row for row in orders if self._order_type(row) == "STOP_MARKET"), None)
            if final_stop is None or self._trigger_price(final_stop) != old_trigger:
                self._mark_protection_reconciliation_required(position, "STOP_PRICE_CHANGED_DURING_QUANTITY_RECONCILIATION")
                raise ExecutionError("PROTECTION_RECONCILIATION_REQUIRED")
            stop_resized = True
            self.execution_journal.record(
                decision_id=self._entry_context.get("entry_decision_id"),
                action="RESTART_PROTECTION_QUANTITY_RECONCILED" if restart else "PROTECTION_QUANTITY_RECONCILED",
                status="CONFIRMED", reason="CURRENT_EXCHANGE_POSITION_QUANTITY",
                position_after=position,
                details={"stop_trigger_price": old_trigger, "remaining_quantity": quantity},
            )
        orders = self.client.get_open_algo_orders("BTCUSDT")
        targets = [row for row in orders if self._order_type(row) == "TAKE_PROFIT_MARKET"]
        if not targets:
            if self.protection_reconciliation_reason != "TARGET_PROTECTION_MISSING":
                self.execution_journal.record(
                    decision_id=self._entry_context.get("entry_decision_id"),
                    action="TARGET_PROTECTION_MISSING", status="DEGRADED",
                    reason="Valid exchange stop retained during reconciliation", position_after=position,
                )
            self.protection_reconciliation_required = True
            self.protection_reconciliation_reason = "TARGET_PROTECTION_MISSING"
            try:
                repaired = self._repair_missing_target(position, orders)
            except Exception:
                repaired = False
            if not repaired:
                self.execution_journal.record(
                    decision_id=self._entry_context.get("entry_decision_id"),
                    action="TARGET_PROTECTION_REPAIR_FAILED", status="FAIL_CLOSED",
                    reason="Restart/reconciliation target repair could not be verified", position_after=position,
                )
                self._write_runtime_state(last_execution_result="PROTECTION_DEGRADED_TARGET_MISSING")
                return {"status": "PROTECTION_DEGRADED_TARGET_MISSING", "stop_resized": stop_resized}
            orders = self.client.get_open_algo_orders("BTCUSDT")
        expected_target_ids = set(self.protection_reconciliation_expected_target_ids)
        if expected_target_ids:
            extra_targets = [
                row for row in orders
                if self._order_type(row) == "TAKE_PROFIT_MARKET"
                and row.get("algoId") is not None
                and row.get("algoId") not in expected_target_ids
            ]
            if extra_targets:
                try:
                    for target in extra_targets:
                        self.client.cancel_algo_order(algo_id=int(target["algoId"]))
                    orders = self.client.get_open_algo_orders("BTCUSDT")
                    remaining_ids = {row.get("algoId") for row in orders}
                    if any(target.get("algoId") in remaining_ids for target in extra_targets):
                        raise ExecutionError("STALE_TARGET_CLEANUP_UNPROVEN")
                except Exception:
                    self._mark_protection_reconciliation_required(position, "STALE_TARGET_CLEANUP_FAILED")
                    return {"status": "PROTECTION_RECONCILIATION_REQUIRED", "stop_resized": stop_resized}
                self.execution_journal.record(
                    decision_id=self._entry_context.get("entry_decision_id"),
                    action="STALE_TARGET_RECONCILED", status="CONFIRMED", position_after=position,
                    details={"cancelled_target_ids": [row.get("algoId") for row in extra_targets]},
                )
        self._restore_protective_roles(position, orders)
        if not self._protection_invariants_hold(position, orders):
            self._mark_protection_reconciliation_required(position, "TARGET_OR_STOP_QUANTITY_INVARIANT_FAILED")
            return {"status": "PROTECTION_RECONCILIATION_REQUIRED", "stop_resized": stop_resized}
        current_target_ids = sorted(
            row.get("algoId") for row in orders
            if self._order_type(row) == "TAKE_PROFIT_MARKET" and row.get("algoId") is not None
        )
        expected_target_ids = sorted(self.protection_reconciliation_expected_target_ids)
        target_identity_reconciled = not expected_target_ids or current_target_ids == expected_target_ids
        if self.protection_reconciliation_required and target_identity_reconciled:
            self.protection_reconciliation_required = False
            self.protection_reconciliation_reason = None
            self.protection_reconciliation_expected_target_ids = []
            self.execution_journal.record(
                decision_id=self._entry_context.get("entry_decision_id"),
                action="PROTECTION_RECONCILED", status="CONFIRMED", position_after=position,
            )
        self._write_runtime_state()
        return {
            "status": "PROTECTION_RECONCILIATION_REQUIRED" if self.protection_reconciliation_required else "PROTECTION_RECONCILED",
            "stop_resized": stop_resized,
        }

    def recover_from_exchange(self) -> Dict[str, Any]:
        result = super().recover_from_exchange()
        position = result["position"]
        if float(position.get("position_amt") or 0) == 0:
            self._entry_context = {}
            self._missing_context_warning_emitted = False
            self.protection_reconciliation_required = False
            self.protection_reconciliation_reason = None
            self.protection_reconciliation_expected_target_ids = []
            self._write_runtime_state()
        else:
            if not self._has_verified_exchange_baseline():
                recovered_baseline = self._recover_historical_baseline(position)
                result["baseline_recovery"] = recovered_baseline
            protection = self._reconcile_active_protection(position, restart=True)
            result["protection_reconciliation"] = protection
        if float(position.get("position_amt") or 0) != 0 and not self._has_verified_exchange_baseline():
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
        elif float(position.get("position_amt") or 0) != 0 and (result.get("baseline_recovery") or {}).get("verified"):
            result["status"] = "RECONSTRUCTED_VERIFIED"
        return result

    def _recover_historical_baseline(self, position: Dict[str, Any]) -> Dict[str, Any]:
        """Read history only; never mutate current exchange orders."""
        get_trades = getattr(self.client, "get_user_trades", None)
        if not callable(get_trades):
            return {"verified": False, "reason": "SIGNED_USER_TRADE_HISTORY_UNAVAILABLE"}
        try:
            trades = get_trades("BTCUSDT")
            entry = self.baseline_recovery.recover_entry(position, trades)
            if not entry.get("verified"):
                return entry
            start = int(entry["entry_opened_at"])
            normal_reader = getattr(self.client, "get_order_history", None)
            algo_reader = getattr(self.client, "get_algo_order_history", None)
            try:
                normal = normal_reader("BTCUSDT", start_time=start) if callable(normal_reader) else []
            except Exception:
                normal = []
            try:
                algo = algo_reader("BTCUSDT", start_time=start) if callable(algo_reader) else []
            except Exception:
                # Some Binance TESTNET deployments may not expose historical
                # algo history; normal historical STOP orders remain eligible.
                algo = []
            recovered = self.baseline_recovery.recover(position, trades, normal, algo)
            if not recovered.get("verified"):
                return recovered
            self._entry_context.update({
                "exchange_baseline_verified": True,
                "actual_entry_price": recovered["actual_entry_price"],
                "actual_initial_position_size": recovered["actual_initial_position_size"],
                "actual_initial_stop": recovered["actual_initial_stop"],
                "entry_opened_at": recovered["entry_opened_at"],
                "direction": recovered["direction"],
                "context_status": "RECONSTRUCTED_VERIFIED",
                "initial_stop_source": "BINANCE_HISTORICAL_ORDER",
                "historical_stop_order_id": recovered.get("historical_stop_order_id"),
                "historical_stop_created_at": recovered.get("historical_stop_created_at"),
            })
            candle_reader = getattr(self.client, "get_closed_klines_since", None)
            if callable(candle_reader):
                try:
                    candles = candle_reader("BTCUSDT", "5m", int(recovered["entry_opened_at"]))
                    excursions = self.baseline_recovery.rebuild_excursions(
                        recovered["direction"], recovered["actual_entry_price"],
                        recovered["actual_initial_stop"], recovered["entry_opened_at"], candles,
                    )
                    self.management_mfe_r = excursions["mfe_r"]
                    self.management_mae_r = excursions["mae_r"]
                except Exception:
                    # The immutable baseline remains proven even if optional
                    # public candle reconstruction is temporarily unavailable.
                    pass
            self._missing_context_warning_emitted = False
            self.execution_journal.record(
                decision_id=None, action="RECONSTRUCTED_VERIFIED", status="CONFIRMED",
                reason="Immutable baseline proven from signed Binance history",
                position_after=position,
                details={
                    "entry_order_id": recovered.get("entry_order_id"),
                    "historical_stop_order_id": recovered.get("historical_stop_order_id"),
                    "initial_stop_source": "BINANCE_HISTORICAL_ORDER",
                },
            )
            self._write_runtime_state(last_execution_result="RECONSTRUCTED_VERIFIED")
            return recovered
        except Exception:
            return {"verified": False, "reason": "SIGNED_HISTORICAL_RECOVERY_UNAVAILABLE"}

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
            self.protection_reconciliation_reason = None
            self.protection_reconciliation_expected_target_ids = []
            self.profit_fade_partial_taken = False
            self._write_runtime_state()
        return after

    def _transition_protection_alert(self, state, reason: Optional[str]) -> None:
        """Persist protection alert transitions so restarts cannot repeat Telegram spam."""
        if reason:
            changed = self.active_alert_state != "UNPROTECTED" or self.last_alert_reason != reason
            if changed:
                state.activate_emergency_latch(reason)
                self._notify(
                    "KILL_SWITCH",
                    {"reason": f"{reason}; active TESTNET position has no verified exchange stop"},
                    f"KILL_SWITCH:{reason}:{int(time.time())}",
                )
                self.last_alert_type = "KILL_SWITCH"
                self.last_alert_reason = reason
                self.last_alert_sent_at = int(time.time())
                self.active_alert_state = "UNPROTECTED"
            self._write_runtime_state(last_execution_result=reason)
            return
        if self.active_alert_state == "UNPROTECTED":
            self._notify(
                "PROTECTION_RECOVERED",
                {"message": "✅ POZİSYON KORUMASI DÜZELDİ"},
                f"PROTECTION_RECOVERED:{self.last_alert_reason}:{int(time.time())}",
            )
            self.execution_journal.record(
                decision_id=self._entry_context.get("entry_decision_id"),
                action="PROTECTION_RECOVERED", status="CONFIRMED",
                reason=self.last_alert_reason,
            )
            # Clear only the operational latch owned by this protection fault.
            if getattr(state, "emergency_latch_active", False) and getattr(state, "kill_switch_reason", "") == self.last_alert_reason:
                state.emergency_latch_active = False
                state.kill_switch_activated = bool(state.daily_loss_guard_active or state.consecutive_loss_cooldown_active)
                if not state.kill_switch_activated:
                    state.kill_switch_reason = ""
            self.last_alert_type = "PROTECTION_RECOVERED"
            self.last_alert_sent_at = int(time.time())
            self.active_alert_state = "HEALTHY"
            self._write_runtime_state(last_execution_result="PROTECTION_RECOVERED")

    def _repair_missing_target(self, position: Dict[str, Any], orders: list[Dict[str, Any]]) -> bool:
        quantity = abs(float(position.get("position_amt") or 0))
        is_long = float(position.get("position_amt") or 0) > 0
        entry = self._safe_float(self._entry_context.get("actual_entry_price"))
        mark = self._safe_float(position.get("mark_price"))
        candidates = [
            self._safe_float((self.last_management_decision or {}).get("new_tp2")),
            self._safe_float((self.last_management_decision or {}).get("old_tp2")),
            self._safe_float(self._entry_context.get("tp2")),
        ]
        reference_values = [value for value in (entry, mark) if value is not None]
        if not reference_values:
            return False
        boundary = max(reference_values) if is_long else min(reference_values)
        target = next((value for value in candidates if value is not None and (value > boundary if is_long else 0 < value < boundary)), None)
        if quantity <= 0 or target is None:
            return False
        side = "SELL" if is_long else "BUY"
        new_order = self.client.place_protective_order("BTCUSDT", side, "TAKE_PROFIT_MARKET", quantity, target)
        new_id = new_order.get("binance_order_id")
        verified = self.client.get_open_algo_orders("BTCUSDT")
        verified_target = next((row for row in verified if row.get("algoId") == new_id), None)
        target_quantity = self._order_quantity(verified_target or {})
        tolerance = max(1e-12, quantity * 1e-9)
        stop_still_valid = any(self._validate_stop(row, position, quantity) for row in verified)
        if (verified_target is None or not self._validate_target(verified_target, position)
                or target_quantity is None or target_quantity > quantity + tolerance or not stop_still_valid):
            try:
                if new_id is not None:
                    self.client.cancel_algo_order(algo_id=int(new_id))
            except Exception:
                pass
            return False
        new_order["role"] = "TP_FINAL"
        self._restore_protective_roles(position, verified)
        self.protection_reconciliation_required = False
        self.protection_reconciliation_reason = None
        self.protection_reconciliation_expected_target_ids = []
        self.execution_journal.record(
            decision_id=self._entry_context.get("entry_decision_id"),
            action="TARGET_PROTECTION_REPAIRED", status="CONFIRMED",
            position_after=position,
            details={"target": target, "quantity": quantity, "target_order_id": new_id},
        )
        self._write_runtime_state(last_execution_result="TARGET_PROTECTION_REPAIRED")
        return True

    def _assess_and_repair_protection(self, position: Dict[str, Any], state) -> Dict[str, Any]:
        orders = self.client.get_open_algo_orders("BTCUSDT")
        quantity = abs(float(position.get("position_amt") or 0))
        stops = [row for row in orders if self._order_type(row) == "STOP_MARKET"]
        targets = [row for row in orders if self._order_type(row) == "TAKE_PROFIT_MARKET"]
        valid_stop = len(stops) == 1 and self._validate_stop(stops[0], position, quantity)
        valid_targets = [row for row in targets if self._validate_target(row, position)]
        target_total = sum(self._order_quantity(row) or 0.0 for row in valid_targets)
        valid_target_set = bool(valid_targets) and target_total <= quantity + max(1e-12, quantity * 1e-9)
        if not valid_stop:
            reason = "UNPROTECTED_TESTNET_POSITION"
            should_record = self.active_alert_state != "UNPROTECTED" or self.last_alert_reason != reason
            if should_record:
                self.execution_journal.record(
                    decision_id=self._entry_context.get("entry_decision_id"), action="UNPROTECTED_POSITION",
                    status="KILL_SWITCH", reason="Missing or invalid exchange STOP_MARKET", position_after=position,
                )
            self.protection_reconciliation_required = True
            self.protection_reconciliation_reason = "STOP_MISSING_OR_INVALID"
            self._transition_protection_alert(state, reason)
            return {"status": "UNPROTECTED_POSITION", "position": position, "open_orders": orders}
        if targets and not valid_target_set:
            self._mark_protection_reconciliation_required(position, "TARGET_SET_INVALID")
            return {"status": "PROTECTION_RECONCILIATION_REQUIRED", "position": position, "open_orders": orders}
        if not valid_target_set:
            first_detection = self.protection_reconciliation_reason != "TARGET_PROTECTION_MISSING"
            self.protection_reconciliation_required = True
            self.protection_reconciliation_reason = "TARGET_PROTECTION_MISSING"
            if first_detection:
                self.execution_journal.record(
                    decision_id=self._entry_context.get("entry_decision_id"), action="TARGET_PROTECTION_MISSING",
                    status="DEGRADED", reason="Valid exchange stop retained; target repair required", position_after=position,
                )
            try:
                repaired = self._repair_missing_target(position, orders)
            except Exception:
                repaired = False
            if not repaired:
                self.execution_journal.record(
                    decision_id=self._entry_context.get("entry_decision_id"), action="TARGET_PROTECTION_REPAIR_FAILED",
                    status="FAIL_CLOSED", reason="Safe deterministic target could not be verified", position_after=position,
                )
                self._write_runtime_state(last_execution_result="PROTECTION_DEGRADED_TARGET_MISSING")
                return {"status": "PROTECTION_DEGRADED_TARGET_MISSING", "position": position, "open_orders": orders}
            orders = self.client.get_open_algo_orders("BTCUSDT")
        self._transition_protection_alert(state, None)
        self._restore_protective_roles(position, orders)
        self._write_runtime_state(last_execution_result="POSITION_MANAGEMENT")
        return {"status": "POSITION_MANAGEMENT", "position": position, "open_orders": orders}

    def manage_existing_position(self, state) -> Dict[str, Any]:
        try:
            position = self.reconcile_position()
        except Exception:
            self._transition_protection_alert(state, "EXCHANGE_RECONCILIATION_FAILURE")
            raise
        if float(position.get("position_amt") or 0) == 0:
            cleanup = self.cleanup_flat_reduce_only_orders()
            if cleanup["remaining"]:
                self._transition_protection_alert(state, "UNEXPECTED_OPEN_ORDERS")
                return {"status": "OPEN_ORDERS_PRESENT", "position": position, "open_orders": cleanup["remaining"]}
            self._transition_protection_alert(state, None)
            return {"status": "FLAT", "position": position, "stale_orders_cancelled": cleanup["cancelled"]}
        result = self._assess_and_repair_protection(position, state)
        if result["status"] == "POSITION_MANAGEMENT":
            result["protection_reconciliation"] = self._reconcile_active_protection(position)
        if float(position.get("position_amt") or 0) != 0 and not self._has_verified_exchange_baseline():
            result["status"] = "RECOVERED_POSITION_CONTEXT_UNAVAILABLE"
            result["position_intelligence"] = self.last_management_decision or {
                "state": "NO_CHANGE", "reason_codes": ["RECOVERED_POSITION_CONTEXT_UNAVAILABLE"], "adaptive_actions": "NONE",
            }
        return result

    def _replace_target_safely(self, position: Dict[str, Any], new_target: float, role: str) -> None:
        orders = self.client.get_open_algo_orders("BTCUSDT")
        stops = [order for order in orders if self._order_type(order) == "STOP_MARKET"]
        position_quantity = abs(float(position.get("position_amt") or 0))
        if len(stops) != 1 or not self._validate_stop(stops[0], position, position_quantity):
            raise ExecutionError("UNPROTECTED_TESTNET_POSITION")
        targets = [order for order in orders if self._order_type(order) == "TAKE_PROFIT_MARKET"]
        if not targets:
            raise ExecutionError("PROTECTION_REPLACEMENT_FAILED")
        is_long = float(position["position_amt"]) > 0
        ordered = sorted(targets, key=lambda row: self._trigger_price(row) or 0, reverse=not is_long)
        old_target = ordered[0] if role == "TP1" else ordered[-1]
        side = "SELL" if is_long else "BUY"
        quantity = self._order_quantity(old_target) or position_quantity
        new_order = self.client.place_protective_order("BTCUSDT", side, "TAKE_PROFIT_MARKET", quantity, new_target)
        new_id = new_order.get("binance_order_id")
        verified = self.client.get_open_algo_orders("BTCUSDT")
        verified_new = next((row for row in verified if row.get("algoId") == new_id), None)
        quantity_tolerance = max(1e-12, quantity * 1e-9)
        verified_quantity = self._order_quantity(verified_new or {})
        if verified_new is None or not self._validate_target(verified_new, position) or verified_quantity is None or abs(verified_quantity - quantity) > quantity_tolerance:
            try:
                if new_id is not None:
                    self.client.cancel_algo_order(algo_id=int(new_id))
            except Exception:
                self._mark_protection_reconciliation_required(position, "NEW_TARGET_VERIFICATION_ROLLBACK_FAILED")
                raise ExecutionError("PROTECTION_RECONCILIATION_REQUIRED") from None
            raise ExecutionError("PROTECTION_REPLACEMENT_FAILED")
        try:
            self.client.cancel_algo_order(algo_id=int(old_target["algoId"]))
        except Exception:
            try:
                self.client.cancel_algo_order(algo_id=int(new_id))
                rollback = self.client.get_open_algo_orders("BTCUSDT")
                rollback_ids = {row.get("algoId") for row in rollback}
                stop_valid = self._protection_invariants_hold(position, rollback)
                if old_target.get("algoId") not in rollback_ids or new_id in rollback_ids or not stop_valid:
                    raise ExecutionError("TARGET_ROLLBACK_UNPROVEN")
            except Exception:
                self.protection_reconciliation_expected_target_ids = [
                    row.get("algoId") for row in targets if row.get("algoId") is not None
                ]
                self._mark_protection_reconciliation_required(position, "TARGET_REPLACEMENT_ROLLBACK_FAILED")
                raise ExecutionError("PROTECTION_RECONCILIATION_REQUIRED") from None
            self.execution_journal.record(
                decision_id=self._entry_context.get("entry_decision_id"),
                action="TARGET_REPLACEMENT_ROLLED_BACK", status="CONFIRMED",
                reason="OLD_TARGET_CANCELLATION_FAILED", position_after=position,
                details={"role": role, "old_target_id": old_target.get("algoId")},
            )
            raise ExecutionError("PROTECTION_REPLACEMENT_FAILED") from None
        final = self.client.get_open_algo_orders("BTCUSDT")
        final_ids = {row.get("algoId") for row in final}
        final_new = next((row for row in final if row.get("algoId") == new_id), None)
        final_quantity = self._order_quantity(final_new or {})
        if old_target.get("algoId") in final_ids or final_new is None or not self._validate_target(final_new, position) or final_quantity is None or abs(final_quantity - quantity) > quantity_tolerance or not self._protection_invariants_hold(position, final):
            self._mark_protection_reconciliation_required(position, "TARGET_REPLACEMENT_FINAL_SET_MISMATCH")
            raise ExecutionError("PROTECTION_RECONCILIATION_REQUIRED")
        new_order["role"] = role
        self.protection_reconciliation_expected_target_ids = []
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

    def _reconcile_profit_partial_protection(self, position: Dict[str, Any]) -> None:
        """Resize protection after a discretionary partial while retaining STOP authority."""
        quantity = abs(float(position.get("position_amt") or 0))
        orders = self.client.get_open_algo_orders("BTCUSDT")
        stops = [row for row in orders if self._order_type(row) == "STOP_MARKET"]
        if not stops or self._trigger_price(stops[0]) is None:
            raise ExecutionError("UNPROTECTED_TESTNET_POSITION")
        if len(stops) != 1 or not self._validate_stop(stops[0], position, quantity):
            self._replace_stop_safely(position, float(self._trigger_price(stops[0])))
        orders = self.client.get_open_algo_orders("BTCUSDT")
        targets = [row for row in orders if self._order_type(row) == "TAKE_PROFIT_MARKET"]
        target_total = sum(self._order_quantity(row) or 0.0 for row in targets)
        if target_total > quantity + max(1e-12, quantity * 1e-9):
            try:
                for target in targets:
                    self.client.cancel_algo_order(algo_id=int(target["algoId"]))
            except Exception:
                self._mark_protection_reconciliation_required(position, "TARGET_CANCEL_AFTER_PROFIT_PARTIAL_FAILED")
                raise ExecutionError("PROTECTION_RECONCILIATION_REQUIRED") from None
            if not self._repair_missing_target(position, self.client.get_open_algo_orders("BTCUSDT")):
                self._mark_protection_reconciliation_required(position, "TARGET_REPAIR_AFTER_PROFIT_PARTIAL_FAILED")
                raise ExecutionError("PROTECTION_RECONCILIATION_REQUIRED")
        final = self.client.get_open_algo_orders("BTCUSDT")
        if not self._protection_invariants_hold(position, final):
            self._mark_protection_reconciliation_required(position, "PROFIT_PARTIAL_PROTECTION_INVARIANT_FAILED")
            raise ExecutionError("PROTECTION_RECONCILIATION_REQUIRED")

    def _notify_management_transition(self, event: str, payload: Dict[str, Any]) -> None:
        # State/action identity deliberately excludes candle time and decision id.
        # Repeated closed-5M evaluations in the same state must stay silent.
        action = (payload.get("target_action") or {}).get("action") or (payload.get("stop_action") or {}).get("action") or "NONE"
        key = f"{event}:{payload.get('state')}:{action}:{','.join(payload.get('reason_codes') or [])}"
        if key == self.last_management_alert_key:
            return
        self._notify(event, payload, key)
        self.last_management_alert_key = key

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
            self.protection_reconciliation_reason = None
            self.protection_reconciliation_expected_target_ids = []
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
        closed_candle = candles[-1]
        if closed_candle.get("is_closed") is False:
            return {"status": "MANAGEMENT_NO_CHANGE", "position": position, "position_intelligence": self.last_management_decision}
        candle_ts = int(closed_candle["time"] * 1000)
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
        closed_high = self._safe_float(closed_candle.get("high"))
        closed_low = self._safe_float(closed_candle.get("low"))
        favorable_price = closed_high if is_long else closed_low
        adverse_price = closed_low if is_long else closed_high
        closed_candle_mfe_r = (
            ((favorable_price - entry) if is_long else (entry - favorable_price)) / risk
            if risk > 0 and favorable_price is not None and favorable_price > 0 else None
        )
        closed_candle_mae_r = (
            ((adverse_price - entry) if is_long else (entry - adverse_price)) / risk
            if risk > 0 and adverse_price is not None and adverse_price > 0 else None
        )
        if closed_candle_mfe_r is not None:
            self.management_mfe_r = max(self.management_mfe_r, closed_candle_mfe_r)
        if closed_candle_mae_r is not None:
            self.management_mae_r = min(self.management_mae_r, closed_candle_mae_r)
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
        atr = self._safe_float(frame.get("atr")) or 0.0
        swing_key = "swing_lows" if is_long else "swing_highs"
        confirmed_swings = [
            row for row in (frame.get(swing_key) or [])
            if self._safe_float(row.get("price")) is not None
            and int(row.get("confirmed_at") or 0) <= candle_ts
        ]
        confirmed_swing_stop = None
        if confirmed_swings and atr > 0:
            raw_swing = float(confirmed_swings[-1]["price"]) + (-.20 * atr if is_long else .20 * atr)
            if (is_long and raw_swing < mark) or (not is_long and raw_swing > mark):
                confirmed_swing_stop = raw_swing
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
            profit_fade_partial_taken=self.profit_fade_partial_taken,
            confirmed_swing_stop=confirmed_swing_stop,
            stop_min_gap=self._price_tick_gap("BTCUSDT", mark),
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
            "closed_candle_mfe_r": closed_candle_mfe_r,
            "closed_candle_mae_r": closed_candle_mae_r,
        })
        self.last_management_decision = payload
        try:
            if decision.state in {PositionManagementState.EXIT_EARLY, PositionManagementState.EXIT_PROFIT_FADE}:
                self.execution_journal.record(decision_id=None, action="THESIS_INVALIDATED", status="CONFIRMED", details=payload)
                reconciled = self._early_exit_and_reconcile(position, state, payload)
                position = reconciled["position"]
                event = "PROFIT_FADE_EXIT" if decision.state == PositionManagementState.EXIT_PROFIT_FADE else "EARLY_EXIT"
            elif decision.target_action.get("action") == "CLOSE_PARTIAL":
                before_size = abs(float(position.get("position_amt") or 0))
                raw_quantity = before_size * float(decision.target_action.get("fraction") or 0)
                quantity = self.client.normalize_quantity("BTCUSDT", raw_quantity, market=True, price=mark)
                if quantity <= 0 or quantity >= before_size:
                    raise ExecutionError("INVALID_REDUCE_ONLY_QUANTITY")
                partial = self._take_partial_safely(position, quantity)
                position = partial["position"]
                self._known_position = position
                self._reconcile_profit_partial_protection(position)
                self.profit_fade_partial_taken = True
                partial_order = partial.get("order") or {}
                fill_price = self._safe_float(partial_order.get("average_fill_price"))
                realized_pnl = partial_order.get("realized_pnl")
                if realized_pnl is None and fill_price is not None:
                    realized_pnl = ((fill_price - entry) if is_long else (entry - fill_price)) * quantity
                payload.update({
                    "closed_quantity": quantity,
                    "remaining_quantity": abs(float(position.get("position_amt") or 0)),
                    "realized_pnl": realized_pnl,
                })
                event = "PROFIT_PARTIAL_TAKEN"
                if decision.stop_action.get("action") == "TIGHTEN_STOP":
                    self._replace_stop_safely(position, float(decision.stop_action["new_stop"]))
            elif decision.stop_action.get("action") == "TIGHTEN_STOP":
                self._replace_stop_safely(position, float(decision.stop_action["new_stop"]))
                event = "PROFIT_PROTECTION" if decision.state in {PositionManagementState.PROFIT_PROTECT, PositionManagementState.PROFIT_TRAIL} else "STOP_TIGHTENED"
            elif decision.target_action.get("action") == "REPLACE_TP2":
                self._replace_tp2_safely(position, float(decision.target_action["new_tp2"]))
                self.target_replan_count = decision.target_replan_count
                self.last_target_replan_at = decision.last_target_replan_at
                event = "TP2_REPLANNED"
            else:
                event = "RECOVERY_WAIT" if decision.state == PositionManagementState.RECOVERY_WAIT else "POSITION_HOLD" if decision.state == PositionManagementState.HOLD else "MANAGEMENT_NO_CHANGE"
        except Exception as exc:
            if decision.state in {PositionManagementState.EXIT_EARLY, PositionManagementState.EXIT_PROFIT_FADE}:
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
        telegram_event = {
            "PROFIT_PROTECTION": "PROFIT_PROTECTION",
            "PROFIT_PARTIAL_TAKEN": "PROFIT_PARTIAL_TAKEN",
            "PROFIT_FADE_EXIT": "PROFIT_FADE",
        }.get(event)
        if telegram_event:
            self._notify_management_transition(telegram_event, payload)
        elif decision.state == PositionManagementState.PROFIT_TRAIL:
            self._notify_management_transition("PROFIT_RUNNER", payload)
        else:
            self.last_management_alert_key = f"STATE:{decision.state.value}"
        self._write_runtime_state(last_execution_result=decision.state.value)
        return {"status": decision.state.value, "position": position, "position_intelligence": payload}
