"""Exchange-safe fallback profit management for partial restart context."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

from data.binance_execution_client import ExecutionError
from engines.restart_profit_fallback import RestartProfitFallback
from storage.state_repository import create_state_repository


class RestartProfitFallbackManager:
    """Protect profit without fabricating the missing immutable initial stop.

    This path is used only while the normal R-based manager is fail-closed due
    to an unverified restart baseline. It requires a valid exchange STOP before
    taking any action and never opens or increases a position.
    """

    KEY = "restart_profit_fallback"

    def __init__(self, executor, settings, journal_dir: str = "journal_logs", state_repository=None) -> None:
        self.executor = executor
        self.client = executor.client
        self.settings = settings
        self.repository = state_repository or create_state_repository(Path(journal_dir) / "restart_profit_fallback.json")
        persisted = self._load()
        self.peak_pnl_usdt = float(persisted.get("peak_pnl_usdt") or 0.0)
        self.partial_taken = bool(persisted.get("partial_taken", False))
        self.armed = bool(persisted.get("armed", False))
        self.last_action = persisted.get("last_action")
        self.engine = RestartProfitFallback(
            enabled=getattr(settings, "RESTART_PROFIT_FALLBACK_ENABLED", True),
            arm_usdt=getattr(settings, "RESTART_PROFIT_FALLBACK_ARM_USDT", 5.0),
            partial_trigger_usdt=getattr(settings, "RESTART_PROFIT_FALLBACK_PARTIAL_TRIGGER_USDT", 10.0),
            partial_fraction=getattr(settings, "RESTART_PROFIT_FALLBACK_PARTIAL_FRACTION", 0.30),
            lock_1_trigger_usdt=getattr(settings, "RESTART_PROFIT_FALLBACK_LOCK1_TRIGGER_USDT", 10.0),
            lock_1_usdt=getattr(settings, "RESTART_PROFIT_FALLBACK_LOCK1_USDT", 4.0),
            lock_2_trigger_usdt=getattr(settings, "RESTART_PROFIT_FALLBACK_LOCK2_TRIGGER_USDT", 15.0),
            lock_2_usdt=getattr(settings, "RESTART_PROFIT_FALLBACK_LOCK2_USDT", 8.0),
            lock_3_trigger_usdt=getattr(settings, "RESTART_PROFIT_FALLBACK_LOCK3_TRIGGER_USDT", 25.0),
            lock_3_usdt=getattr(settings, "RESTART_PROFIT_FALLBACK_LOCK3_USDT", 15.0),
            giveback_exit_arm_usdt=getattr(settings, "RESTART_PROFIT_FALLBACK_GIVEBACK_ARM_USDT", 12.0),
            giveback_exit_pct=getattr(settings, "RESTART_PROFIT_FALLBACK_GIVEBACK_PCT", 0.40),
            giveback_exit_min_usdt=getattr(settings, "RESTART_PROFIT_FALLBACK_GIVEBACK_MIN_USDT", 5.0),
            giveback_exit_min_current_usdt=getattr(settings, "RESTART_PROFIT_FALLBACK_EXIT_MIN_CURRENT_USDT", 2.0),
            min_lock_improvement_usdt=getattr(settings, "RESTART_PROFIT_FALLBACK_MIN_LOCK_IMPROVEMENT_USDT", 1.0),
        )

    def _load(self) -> Dict[str, Any]:
        try:
            return self.repository.load(self.KEY) or {}
        except Exception:
            return {}

    def _save(self) -> None:
        try:
            self.repository.save(self.KEY, {
                "peak_pnl_usdt": self.peak_pnl_usdt,
                "partial_taken": self.partial_taken,
                "armed": self.armed,
                "last_action": self.last_action,
            })
        except Exception:
            pass

    def reset(self) -> None:
        self.peak_pnl_usdt = 0.0
        self.partial_taken = False
        self.armed = False
        self.last_action = None
        self._save()

    @staticmethod
    def _pnl_from_price(is_long: bool, entry: float, price: float, quantity: float) -> float:
        return ((price - entry) if is_long else (entry - price)) * quantity

    @staticmethod
    def _stop_for_lock(is_long: bool, entry: float, quantity: float, lock_usdt: float) -> float:
        offset = float(lock_usdt) / float(quantity)
        return entry + offset if is_long else entry - offset

    def _validated_stop(self, position: Dict[str, Any], quantity: float) -> Optional[Dict[str, Any]]:
        orders = self.client.get_open_algo_orders("BTCUSDT")
        stops = [row for row in orders if self.executor._order_type(row) == "STOP_MARKET"]
        if len(stops) != 1:
            return None
        return stops[0] if self.executor._validate_stop(stops[0], position, quantity) else None

    def manage(self, position: Dict[str, Any], state) -> Optional[Dict[str, Any]]:
        quantity = abs(float(position.get("position_amt") or 0))
        if quantity <= 0:
            self.reset()
            return None
        if self.executor._has_verified_exchange_baseline():
            self.reset()
            return None
        if self.executor.protection_reconciliation_required:
            return None

        entry = float(position.get("entry_price") or 0)
        mark = float(position.get("mark_price") or 0)
        if entry <= 0:
            return None
        if mark <= 0:
            mark = float(self.client.get_mark_price("BTCUSDT"))
        is_long = float(position.get("position_amt") or 0) > 0
        validation_position = dict(position, mark_price=mark)
        stop = self._validated_stop(validation_position, quantity)
        if stop is None:
            return None
        stop_price = self.executor._trigger_price(stop)
        if stop_price is None:
            return None

        exchange_pnl = self.executor._safe_float(position.get("unrealized_pnl"))
        current_pnl = exchange_pnl if exchange_pnl is not None else self._pnl_from_price(is_long, entry, mark, quantity)
        locked_pnl = self._pnl_from_price(is_long, entry, float(stop_price), quantity)
        decision = self.engine.evaluate(
            current_pnl_usdt=current_pnl,
            previous_peak_pnl_usdt=self.peak_pnl_usdt,
            currently_locked_usdt=locked_pnl,
            partial_taken=self.partial_taken,
        )
        changed = decision.peak_pnl_usdt > self.peak_pnl_usdt + 0.01 or decision.armed != self.armed
        self.peak_pnl_usdt = decision.peak_pnl_usdt
        self.armed = decision.armed

        payload = {
            "state": decision.state,
            "reason_codes": [decision.reason],
            "restart_profit_fallback": True,
            "baseline_status": "UNVERIFIED",
            "current_pnl_usdt": decision.current_pnl_usdt,
            "peak_pnl_usdt": decision.peak_pnl_usdt,
            "giveback_usdt": decision.giveback_usdt,
            "giveback_pct": decision.giveback_pct,
            "current_stop": stop_price,
            "currently_locked_usdt": locked_pnl,
            "desired_lock_usdt": decision.desired_lock_usdt,
        }

        if decision.action == "NONE":
            if changed:
                self._save()
            return None

        if decision.action == "CLOSE_FULL":
            result = self.executor._fast_profit_exit_and_reconcile(position, state, payload)
            self.last_action = "CLOSE_FULL"
            self.reset()
            self.executor._notify(
                "PROFIT_FADE",
                {"message": "Restart fallback peak kâr geri verildi; TESTNET pozisyon kapatıldı", **payload},
                f"RESTART_FALLBACK_EXIT:{int(decision.peak_pnl_usdt * 100)}",
            )
            return {"status": "RESTART_PROFIT_FALLBACK_EXIT", "position": result["position"], "position_intelligence": payload}

        if decision.action == "CLOSE_PARTIAL":
            raw_quantity = quantity * float(decision.partial_fraction or 0)
            close_qty = self.client.normalize_quantity("BTCUSDT", raw_quantity, market=True, price=mark)
            if close_qty <= 0 or close_qty >= quantity:
                raise ExecutionError("INVALID_REDUCE_ONLY_QUANTITY")
            partial = self.executor._take_partial_safely(position, close_qty)
            updated = partial["position"]
            self.executor._known_position = updated
            self.executor._reconcile_profit_partial_protection(updated)
            self.partial_taken = True
            self.last_action = "CLOSE_PARTIAL"
            remaining = abs(float(updated.get("position_amt") or 0))
            # Rebase peak PnL to the remaining runner size. Otherwise the
            # intentional 30% size reduction looks like an immediate giveback
            # and can trigger a false full-exit on the next poll.
            remaining_pnl = self._pnl_from_price(is_long, entry, mark, remaining) if remaining > 0 else 0.0
            self.peak_pnl_usdt = max(0.0, remaining_pnl)
            payload.update({
                "closed_quantity": close_qty,
                "remaining_quantity": remaining,
                "post_partial_peak_pnl_usdt": self.peak_pnl_usdt,
            })
            # Persist the confirmed partial before any optional stop tightening.
            # If tightening fails, the next poll must never repeat the partial.
            self._save()
            if decision.desired_lock_usdt is not None and remaining > 0:
                new_stop = self._stop_for_lock(is_long, entry, remaining, decision.desired_lock_usdt)
                self.executor._replace_stop_safely(updated, new_stop)
                payload["new_stop"] = new_stop
            self.executor.execution_journal.record(
                decision_id=None,
                action="RESTART_PROFIT_FALLBACK_PARTIAL",
                status="CONFIRMED",
                reason=decision.reason,
                position_before=position,
                position_after=updated,
                details=payload,
            )
            self._save()
            self.executor._notify(
                "PROFIT_PARTIAL_TAKEN",
                {"message": "Restart fallback TESTNET kârının %30'unu realize etti", **payload},
                "RESTART_FALLBACK_PARTIAL",
            )
            return {"status": "RESTART_PROFIT_FALLBACK_PARTIAL", "position": updated, "position_intelligence": payload}

        if decision.action == "TIGHTEN_STOP" and decision.desired_lock_usdt is not None:
            new_stop = self._stop_for_lock(is_long, entry, quantity, decision.desired_lock_usdt)
            self.executor._replace_stop_safely(position, new_stop)
            self.last_action = "TIGHTEN_STOP"
            payload["new_stop"] = new_stop
            self.executor.execution_journal.record(
                decision_id=None,
                action="RESTART_PROFIT_FALLBACK_STOP_TIGHTENED",
                status="CONFIRMED",
                reason=decision.reason,
                position_after=position,
                details=payload,
            )
            self._save()
            self.executor._notify(
                "PROFIT_PROTECTION",
                {"message": "Restart fallback TESTNET kârını exchange STOP ile kilitledi", **payload},
                f"RESTART_FALLBACK_LOCK:{decision.desired_lock_usdt:.2f}",
            )
            return {"status": "RESTART_PROFIT_FALLBACK_STOP_TIGHTENED", "position": position, "position_intelligence": payload}

        return None
