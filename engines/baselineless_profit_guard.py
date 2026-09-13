"""Baseline-less profit fallback for recovered TESTNET positions.

This guard is used only when the immutable initial stop cannot be proven.
It never invents R. Instead it works with entry-relative favorable price
movement, peak favorable movement and the currently verified exchange STOP.

The guard itself is pure. Exchange mutation is performed by the executor.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class BaselineLessProfitDecision:
    state: str
    action: str
    current_profit_pct: float
    peak_profit_pct: float
    giveback_pct: float
    giveback_fraction: float
    protected_profit_pct: float
    armed: bool
    desired_lock_pct: Optional[float] = None
    new_stop: Optional[float] = None
    reason: str = ""


class BaselineLessProfitGuard:
    """Protect profitable recovered positions without reconstructing fake R."""

    def __init__(
        self,
        *,
        enabled: bool = True,
        arm_pct: float = 0.0015,
        lock_1_trigger_pct: float = 0.0020,
        lock_1_pct: float = 0.0010,
        lock_2_trigger_pct: float = 0.0030,
        lock_2_pct: float = 0.0015,
        lock_3_trigger_pct: float = 0.0040,
        lock_3_pct: float = 0.0022,
        lock_4_trigger_pct: float = 0.0060,
        lock_4_pct: float = 0.0035,
        lock_5_trigger_pct: float = 0.0080,
        lock_5_pct: float = 0.0050,
        trail_arm_pct: float = 0.0040,
        trail_gap_pct: float = 0.0025,
        exit_arm_pct: float = 0.0035,
        exit_giveback_fraction: float = 0.40,
        exit_min_profit_pct: float = 0.0005,
        min_stop_improvement_pct: float = 0.0002,
    ) -> None:
        self.enabled = enabled
        self.arm_pct = arm_pct
        self.locks = (
            (lock_5_trigger_pct, lock_5_pct),
            (lock_4_trigger_pct, lock_4_pct),
            (lock_3_trigger_pct, lock_3_pct),
            (lock_2_trigger_pct, lock_2_pct),
            (lock_1_trigger_pct, lock_1_pct),
        )
        self.trail_arm_pct = trail_arm_pct
        self.trail_gap_pct = trail_gap_pct
        self.exit_arm_pct = exit_arm_pct
        self.exit_giveback_fraction = exit_giveback_fraction
        self.exit_min_profit_pct = exit_min_profit_pct
        self.min_stop_improvement_pct = min_stop_improvement_pct

    @staticmethod
    def _profit_pct(direction: str, entry: float, price: float) -> float:
        return ((price - entry) if direction == "LONG" else (entry - price)) / entry

    @staticmethod
    def _price_from_profit_pct(direction: str, entry: float, value: float) -> float:
        return entry * (1 + value if direction == "LONG" else 1 - value)

    def evaluate(
        self,
        *,
        direction: str,
        entry: float,
        mark: float,
        current_stop: float,
        previous_peak_profit_pct: float = 0.0,
        stop_min_gap: float = 0.0,
    ) -> BaselineLessProfitDecision:
        direction = str(direction or "").upper()
        if (
            not self.enabled
            or direction not in {"LONG", "SHORT"}
            or entry <= 0
            or mark <= 0
            or current_stop <= 0
        ):
            return BaselineLessProfitDecision(
                state="BASELINELESS_DISABLED",
                action="NONE",
                current_profit_pct=0.0,
                peak_profit_pct=max(0.0, float(previous_peak_profit_pct or 0.0)),
                giveback_pct=0.0,
                giveback_fraction=0.0,
                protected_profit_pct=0.0,
                armed=False,
                reason="BASELINELESS_GUARD_UNAVAILABLE",
            )

        current_profit_pct = self._profit_pct(direction, entry, mark)
        peak_profit_pct = max(float(previous_peak_profit_pct or 0.0), current_profit_pct)
        giveback_pct = max(0.0, peak_profit_pct - current_profit_pct)
        giveback_fraction = giveback_pct / peak_profit_pct if peak_profit_pct > 0 else 0.0
        protected_profit_pct = self._profit_pct(direction, entry, current_stop)
        armed = peak_profit_pct >= self.arm_pct

        if not armed:
            return BaselineLessProfitDecision(
                state="BASELINELESS_WAIT",
                action="NONE",
                current_profit_pct=current_profit_pct,
                peak_profit_pct=peak_profit_pct,
                giveback_pct=giveback_pct,
                giveback_fraction=giveback_fraction,
                protected_profit_pct=protected_profit_pct,
                armed=False,
                reason="BASELINELESS_NOT_ARMED",
            )

        if (
            peak_profit_pct + 1e-12 >= self.exit_arm_pct
            and giveback_fraction + 1e-12 >= self.exit_giveback_fraction
            and current_profit_pct + 1e-12 >= self.exit_min_profit_pct
        ):
            return BaselineLessProfitDecision(
                state="BASELINELESS_GIVEBACK_EXIT",
                action="CLOSE_FULL",
                current_profit_pct=current_profit_pct,
                peak_profit_pct=peak_profit_pct,
                giveback_pct=giveback_pct,
                giveback_fraction=giveback_fraction,
                protected_profit_pct=protected_profit_pct,
                armed=True,
                reason="BASELINELESS_PEAK_GIVEBACK_EXIT",
            )

        desired_lock_pct: Optional[float] = None
        for trigger, lock in self.locks:
            if peak_profit_pct + 1e-12 >= trigger:
                desired_lock_pct = lock
                break

        if peak_profit_pct + 1e-12 >= self.trail_arm_pct:
            trailing_lock = max(0.0, peak_profit_pct - self.trail_gap_pct)
            desired_lock_pct = max(
                desired_lock_pct if desired_lock_pct is not None else trailing_lock,
                trailing_lock,
            )

        new_stop: Optional[float] = None
        if (
            desired_lock_pct is not None
            and desired_lock_pct > protected_profit_pct + self.min_stop_improvement_pct - 1e-12
        ):
            candidate = self._price_from_profit_pct(direction, entry, desired_lock_pct)
            valid = (
                candidate < mark - stop_min_gap
                if direction == "LONG"
                else candidate > mark + stop_min_gap
            )
            if valid:
                new_stop = candidate

        if new_stop is not None:
            return BaselineLessProfitDecision(
                state="BASELINELESS_PROFIT_LOCK",
                action="TIGHTEN_STOP",
                current_profit_pct=current_profit_pct,
                peak_profit_pct=peak_profit_pct,
                giveback_pct=giveback_pct,
                giveback_fraction=giveback_fraction,
                protected_profit_pct=protected_profit_pct,
                armed=True,
                desired_lock_pct=desired_lock_pct,
                new_stop=new_stop,
                reason="BASELINELESS_PROFIT_LOCK_LADDER",
            )

        return BaselineLessProfitDecision(
            state="BASELINELESS_HOLD",
            action="NONE",
            current_profit_pct=current_profit_pct,
            peak_profit_pct=peak_profit_pct,
            giveback_pct=giveback_pct,
            giveback_fraction=giveback_fraction,
            protected_profit_pct=protected_profit_pct,
            armed=True,
            desired_lock_pct=desired_lock_pct,
            reason="BASELINELESS_ARMED_NO_SAFE_CHANGE",
        )
