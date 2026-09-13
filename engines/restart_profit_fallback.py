"""Profit fallback for recovered positions without a provable R baseline.

This engine deliberately avoids inventing an initial stop. It works only with
exchange-proven facts that remain available after restart: side, entry, mark,
current size and current unrealized PnL. It returns desired actions; exchange
mutation remains the executor's responsibility.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class RestartProfitFallbackDecision:
    state: str
    action: str
    current_pnl_usdt: float
    peak_pnl_usdt: float
    giveback_usdt: float
    giveback_pct: float
    armed: bool
    desired_lock_usdt: Optional[float] = None
    partial_fraction: Optional[float] = None
    reason: str = ""


class RestartProfitFallback:
    """Deterministic USDT-based profit preservation when R is unavailable."""

    def __init__(
        self,
        *,
        enabled: bool = True,
        arm_usdt: float = 5.0,
        partial_trigger_usdt: float = 10.0,
        partial_fraction: float = 0.30,
        lock_1_trigger_usdt: float = 10.0,
        lock_1_usdt: float = 4.0,
        lock_2_trigger_usdt: float = 15.0,
        lock_2_usdt: float = 8.0,
        lock_3_trigger_usdt: float = 25.0,
        lock_3_usdt: float = 15.0,
        giveback_exit_arm_usdt: float = 12.0,
        giveback_exit_pct: float = 0.40,
        giveback_exit_min_usdt: float = 5.0,
        giveback_exit_min_current_usdt: float = 2.0,
        min_lock_improvement_usdt: float = 1.0,
    ) -> None:
        self.enabled = enabled
        self.arm_usdt = arm_usdt
        self.partial_trigger_usdt = partial_trigger_usdt
        self.partial_fraction = partial_fraction
        self.locks = (
            (lock_3_trigger_usdt, lock_3_usdt),
            (lock_2_trigger_usdt, lock_2_usdt),
            (lock_1_trigger_usdt, lock_1_usdt),
        )
        self.giveback_exit_arm_usdt = giveback_exit_arm_usdt
        self.giveback_exit_pct = giveback_exit_pct
        self.giveback_exit_min_usdt = giveback_exit_min_usdt
        self.giveback_exit_min_current_usdt = giveback_exit_min_current_usdt
        self.min_lock_improvement_usdt = min_lock_improvement_usdt

    def evaluate(
        self,
        *,
        current_pnl_usdt: float,
        previous_peak_pnl_usdt: float = 0.0,
        currently_locked_usdt: float = 0.0,
        partial_taken: bool = False,
    ) -> RestartProfitFallbackDecision:
        current = float(current_pnl_usdt)
        peak = max(float(previous_peak_pnl_usdt or 0.0), current)
        giveback = max(0.0, peak - current)
        giveback_pct = giveback / peak if peak > 0 else 0.0
        armed = self.enabled and peak >= self.arm_usdt

        if not self.enabled:
            return RestartProfitFallbackDecision(
                state="RESTART_FALLBACK_DISABLED", action="NONE",
                current_pnl_usdt=current, peak_pnl_usdt=peak,
                giveback_usdt=giveback, giveback_pct=giveback_pct,
                armed=False, reason="RESTART_PROFIT_FALLBACK_DISABLED",
            )

        if not armed:
            return RestartProfitFallbackDecision(
                state="RESTART_FALLBACK_WAIT", action="NONE",
                current_pnl_usdt=current, peak_pnl_usdt=peak,
                giveback_usdt=giveback, giveback_pct=giveback_pct,
                armed=False, reason="RESTART_PROFIT_FALLBACK_NOT_ARMED",
            )

        if (
            peak >= self.giveback_exit_arm_usdt
            and giveback >= self.giveback_exit_min_usdt
            and giveback_pct + 1e-12 >= self.giveback_exit_pct
            and current >= self.giveback_exit_min_current_usdt
        ):
            return RestartProfitFallbackDecision(
                state="RESTART_FALLBACK_EXIT", action="CLOSE_FULL",
                current_pnl_usdt=current, peak_pnl_usdt=peak,
                giveback_usdt=giveback, giveback_pct=giveback_pct,
                armed=True, reason="RESTART_PROFIT_GIVEBACK_EXIT",
            )

        desired_lock = None
        for trigger, lock_value in self.locks:
            if peak >= trigger:
                desired_lock = lock_value
                break

        if current >= self.partial_trigger_usdt and not partial_taken and 0 < self.partial_fraction < 1:
            return RestartProfitFallbackDecision(
                state="RESTART_FALLBACK_PARTIAL", action="CLOSE_PARTIAL",
                current_pnl_usdt=current, peak_pnl_usdt=peak,
                giveback_usdt=giveback, giveback_pct=giveback_pct,
                armed=True, desired_lock_usdt=desired_lock,
                partial_fraction=self.partial_fraction,
                reason="RESTART_PROFIT_PARTIAL_REALIZATION",
            )

        if desired_lock is not None and desired_lock > currently_locked_usdt + self.min_lock_improvement_usdt - 1e-12:
            return RestartProfitFallbackDecision(
                state="RESTART_FALLBACK_TIGHTEN", action="TIGHTEN_STOP",
                current_pnl_usdt=current, peak_pnl_usdt=peak,
                giveback_usdt=giveback, giveback_pct=giveback_pct,
                armed=True, desired_lock_usdt=desired_lock,
                reason="RESTART_PROFIT_LOCK_LADDER",
            )

        return RestartProfitFallbackDecision(
            state="RESTART_FALLBACK_HOLD", action="NONE",
            current_pnl_usdt=current, peak_pnl_usdt=peak,
            giveback_usdt=giveback, giveback_pct=giveback_pct,
            armed=True, desired_lock_usdt=desired_lock,
            reason="RESTART_PROFIT_FALLBACK_ARMED_NO_SAFE_CHANGE",
        )
