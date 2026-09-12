"""Fast mark-price profit capture decisions for active TESTNET positions.

This engine is pure: it never submits, cancels, or replaces an exchange order.
The executor applies a decision only after the immutable entry baseline and the
current exchange protection have both been verified.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class FastProfitDecision:
    state: str
    action: str
    current_r: float
    mfe_r: float
    giveback_r: float
    protected_r: float
    armed: bool
    desired_lock_r: Optional[float] = None
    new_stop: Optional[float] = None
    partial_fraction: Optional[float] = None
    reason: str = ""


class FastProfitGuard:
    """Deterministic high-frequency profit guard driven by authoritative mark."""

    def __init__(
        self,
        *,
        enabled: bool = True,
        arm_r: float = 0.40,
        breakeven_trigger_r: float = 0.60,
        breakeven_lock_r: float = 0.00,
        lock_075_trigger_r: float = 0.75,
        lock_075_r: float = 0.10,
        partial_trigger_r: float = 1.00,
        partial_fraction: float = 0.30,
        lock_1_trigger_r: float = 1.00,
        lock_1_r: float = 0.30,
        lock_2_trigger_r: float = 1.25,
        lock_2_r: float = 0.60,
        lock_3_trigger_r: float = 1.50,
        lock_3_r: float = 0.90,
        lock_4_trigger_r: float = 2.00,
        lock_4_r: float = 1.30,
        trail_arm_r: float = 1.25,
        trail_gap_r: float = 0.70,
        fade_exit_arm_r: float = 1.25,
        fade_exit_giveback_r: float = 0.60,
        fade_exit_min_current_r: float = 0.10,
        min_stop_improvement_r: float = 0.05,
    ) -> None:
        self.enabled = enabled
        self.arm_r = arm_r
        self.breakeven_trigger_r = breakeven_trigger_r
        self.breakeven_lock_r = breakeven_lock_r
        self.lock_075_trigger_r = lock_075_trigger_r
        self.lock_075_r = lock_075_r
        self.partial_trigger_r = partial_trigger_r
        self.partial_fraction = partial_fraction
        self.profit_locks = (
            (lock_4_trigger_r, lock_4_r),
            (lock_3_trigger_r, lock_3_r),
            (lock_2_trigger_r, lock_2_r),
            (lock_1_trigger_r, lock_1_r),
            (lock_075_trigger_r, lock_075_r),
            (breakeven_trigger_r, breakeven_lock_r),
        )
        self.trail_arm_r = trail_arm_r
        self.trail_gap_r = trail_gap_r
        self.fade_exit_arm_r = fade_exit_arm_r
        self.fade_exit_giveback_r = fade_exit_giveback_r
        self.fade_exit_min_current_r = fade_exit_min_current_r
        self.min_stop_improvement_r = min_stop_improvement_r

    @staticmethod
    def _r(direction: str, entry: float, price: float, risk: float) -> float:
        return ((price - entry) if direction == "LONG" else (entry - price)) / risk

    @staticmethod
    def _price_from_r(direction: str, entry: float, risk: float, r_value: float) -> float:
        return entry + risk * r_value * (1 if direction == "LONG" else -1)

    def evaluate(
        self,
        *,
        direction: str,
        entry: float,
        initial_stop: float,
        current_stop: float,
        mark: float,
        initial_size: float,
        current_size: float,
        previous_mfe_r: float = 0.0,
        partial_taken: bool = False,
        stop_min_gap: float = 0.0,
    ) -> FastProfitDecision:
        normalized_direction = str(direction or "").upper()
        risk = abs(float(entry) - float(initial_stop))
        if (
            not self.enabled
            or normalized_direction not in {"LONG", "SHORT"}
            or risk <= 0
            or mark <= 0
            or initial_size <= 0
            or current_size <= 0
        ):
            return FastProfitDecision(
                state="FAST_PROFIT_DISABLED",
                action="NONE",
                current_r=0.0,
                mfe_r=max(0.0, float(previous_mfe_r or 0.0)),
                giveback_r=0.0,
                protected_r=0.0,
                armed=False,
                reason="FAST_PROFIT_GUARD_UNAVAILABLE",
            )

        current_r = self._r(normalized_direction, entry, mark, risk)
        mfe_r = max(float(previous_mfe_r or 0.0), current_r)
        giveback_r = max(0.0, mfe_r - current_r)
        protected_r = self._r(normalized_direction, entry, current_stop, risk)
        armed = mfe_r >= self.arm_r

        if not armed:
            return FastProfitDecision(
                state="FAST_PROFIT_WAIT",
                action="NONE",
                current_r=current_r,
                mfe_r=mfe_r,
                giveback_r=giveback_r,
                protected_r=protected_r,
                armed=False,
                reason="FAST_PROFIT_NOT_ARMED",
            )

        if (
            mfe_r >= self.fade_exit_arm_r
            and giveback_r + 1e-9 >= self.fade_exit_giveback_r
            and current_r >= self.fade_exit_min_current_r
        ):
            return FastProfitDecision(
                state="FAST_PROFIT_EXIT",
                action="CLOSE_FULL",
                current_r=current_r,
                mfe_r=mfe_r,
                giveback_r=giveback_r,
                protected_r=protected_r,
                armed=True,
                reason="FAST_MFE_GIVEBACK_EXIT",
            )

        lock_r: Optional[float] = None
        for trigger_r, candidate_lock in self.profit_locks:
            if mfe_r >= trigger_r:
                lock_r = candidate_lock
                break
        if mfe_r >= self.trail_arm_r:
            trailing_lock = mfe_r - self.trail_gap_r
            lock_r = max(lock_r if lock_r is not None else trailing_lock, trailing_lock)

        desired_stop: Optional[float] = None
        if lock_r is not None and lock_r > protected_r + self.min_stop_improvement_r - 1e-12:
            candidate = self._price_from_r(normalized_direction, entry, risk, lock_r)
            valid = (
                candidate < mark - stop_min_gap
                if normalized_direction == "LONG"
                else candidate > mark + stop_min_gap
            )
            if valid:
                desired_stop = candidate

        already_reduced = current_size < initial_size * (1 - 1e-6)
        if (
            current_r >= self.partial_trigger_r
            and not partial_taken
            and not already_reduced
            and 0 < self.partial_fraction < 1
        ):
            return FastProfitDecision(
                state="FAST_PROFIT_PARTIAL",
                action="CLOSE_PARTIAL",
                current_r=current_r,
                mfe_r=mfe_r,
                giveback_r=giveback_r,
                protected_r=protected_r,
                armed=True,
                desired_lock_r=lock_r,
                new_stop=desired_stop,
                partial_fraction=self.partial_fraction,
                reason="FAST_1R_PARTIAL_REALIZATION",
            )

        if desired_stop is not None:
            return FastProfitDecision(
                state="FAST_PROFIT_TIGHTEN",
                action="TIGHTEN_STOP",
                current_r=current_r,
                mfe_r=mfe_r,
                giveback_r=giveback_r,
                protected_r=protected_r,
                armed=True,
                desired_lock_r=lock_r,
                new_stop=desired_stop,
                reason="FAST_PROFIT_LOCK_LADDER",
            )

        return FastProfitDecision(
            state="FAST_PROFIT_HOLD",
            action="NONE",
            current_r=current_r,
            mfe_r=mfe_r,
            giveback_r=giveback_r,
            protected_r=protected_r,
            armed=True,
            desired_lock_r=lock_r,
            reason="FAST_PROFIT_ARMED_NO_SAFE_CHANGE",
        )
