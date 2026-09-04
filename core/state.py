"""Runtime state management with decomposed risk-control guards.

Three independent guard types:
- DAILY_LOSS_GUARD: simulation trading day boundary resets
- CONSECUTIVE_LOSS_GUARD: cooldown that also resets at new simulation trading day
- EMERGENCY_LATCH: operational/technical only, never auto-trips from trading losses
"""

import json
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field

from config.constants import MarketRegime, TriggerState, GuardType
from core.models import TradeRecord, RegimeResult


class BotState(BaseModel):
    """Encapsulates runtime state, risk tracking, and kill switch conditions.
    Supports persistence and restart recovery via SQLite database."""
    account_balance_usdt: float = 10_000.0
    start_of_day_balance_usdt: float = 10_000.0
    current_day: str = Field(default_factory=lambda: datetime.now(timezone.utc).strftime("%Y-%m-%d"))

    # Active Position
    active_position: Optional[TradeRecord] = None

    # Streak & Circuit Breaker Tracking
    consecutive_losses: int = 0
    daily_realized_pnl_usdt: float = 0.0

    # Decomposed risk-control guards
    kill_switch_activated: bool = False
    kill_switch_reason: str = ""
    guard_type: GuardType = GuardType.OTHER_RISK_CONTROL_BLOCK

    daily_loss_guard_active: bool = False
    daily_loss_guard_block_count: int = 0
    daily_loss_guard_total_blocked: int = 0

    consecutive_loss_cooldown_active: bool = False
    consecutive_loss_cooldown_block_count: int = 0
    consecutive_loss_cooldown_total_blocked: int = 0

    emergency_latch_active: bool = False
    emergency_latch_block_count: int = 0
    emergency_latch_total_blocked: int = 0

    # Regime Stability buffer
    recent_regimes_4h: List[MarketRegime] = Field(default_factory=list)
    active_regime: Optional[MarketRegime] = None

    # 5M Trigger state machine
    trigger_state: TriggerState = TriggerState.NO_SETUP

    # Kill-switch event timeline for reporting
    kill_switch_events: List[Dict[str, Any]] = Field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "snapshot_ts": int(datetime.now(timezone.utc).timestamp() * 1000),
            "account_balance_usdt": self.account_balance_usdt,
            "start_of_day_balance_usdt": self.start_of_day_balance_usdt,
            "current_day": self.current_day,
            "active_position": self.active_position.model_dump() if self.active_position else None,
            "consecutive_losses": self.consecutive_losses,
            "daily_realized_pnl_usdt": self.daily_realized_pnl_usdt,
            "kill_switch_activated": self.kill_switch_activated,
            "kill_switch_reason": self.kill_switch_reason,
            "guard_type": self.guard_type.value,
            "daily_loss_guard_active": self.daily_loss_guard_active,
            "daily_loss_guard_block_count": self.daily_loss_guard_block_count,
            "daily_loss_guard_total_blocked": self.daily_loss_guard_total_blocked,
            "consecutive_loss_cooldown_active": self.consecutive_loss_cooldown_active,
            "consecutive_loss_cooldown_block_count": self.consecutive_loss_cooldown_block_count,
            "consecutive_loss_cooldown_total_blocked": self.consecutive_loss_cooldown_total_blocked,
            "emergency_latch_active": self.emergency_latch_active,
            "emergency_latch_block_count": self.emergency_latch_block_count,
            "emergency_latch_total_blocked": self.emergency_latch_total_blocked,
            "active_regime": self.active_regime.value if self.active_regime else None,
            "trigger_state": self.trigger_state.value,
            "recent_regimes_4h": [r.value for r in self.recent_regimes_4h],
            "kill_switch_events": self.kill_switch_events,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "BotState":
        return cls(
            account_balance_usdt=data["account_balance_usdt"],
            start_of_day_balance_usdt=data["start_of_day_balance_usdt"],
            current_day=data["current_day"],
            active_position=TradeRecord.model_validate(data["active_position"]) if data.get("active_position") else None,
            consecutive_losses=data["consecutive_losses"],
            daily_realized_pnl_usdt=data["daily_realized_pnl_usdt"],
            kill_switch_activated=data["kill_switch_activated"],
            kill_switch_reason=data.get("kill_switch_reason", ""),
            guard_type=GuardType(data.get("guard_type", GuardType.OTHER_RISK_CONTROL_BLOCK.value)),
            daily_loss_guard_active=data.get("daily_loss_guard_active", False),
            daily_loss_guard_block_count=data.get("daily_loss_guard_block_count", 0),
            daily_loss_guard_total_blocked=data.get("daily_loss_guard_total_blocked", 0),
            consecutive_loss_cooldown_active=data.get("consecutive_loss_cooldown_active", False),
            consecutive_loss_cooldown_block_count=data.get("consecutive_loss_cooldown_block_count", 0),
            consecutive_loss_cooldown_total_blocked=data.get("consecutive_loss_cooldown_total_blocked", 0),
            emergency_latch_active=data.get("emergency_latch_active", False),
            emergency_latch_block_count=data.get("emergency_latch_block_count", 0),
            emergency_latch_total_blocked=data.get("emergency_latch_total_blocked", 0),
            active_regime=MarketRegime(data["active_regime"]) if data.get("active_regime") else None,
            trigger_state=TriggerState(data["trigger_state"]),
            recent_regimes_4h=[MarketRegime(r) for r in data.get("recent_regimes_4h", [])],
            kill_switch_events=data.get("kill_switch_events", []),
        )

    def save_to_db(self, db_manager) -> None:
        try:
            db_manager.save_state_snapshot(self.to_dict())
        except Exception as e:
            from loguru import logger
            logger.error(f"Failed to save state to database: {e}")

    @classmethod
    def load_from_db(cls, db_manager) -> Optional["BotState"]:
        try:
            row = db_manager.load_latest_state()
            if row:
                return cls.from_dict(dict(row))
            return None
        except Exception as e:
            from loguru import logger
            logger.error(f"Failed to load state from database: {e}")
            return None

    def reset_daily_metrics_if_new_day(self, current_ts: Optional[int] = None) -> None:
        """Reset daily PnL tracker when simulation trading day changes."""
        if current_ts is not None:
            today_str = datetime.fromtimestamp(current_ts / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
        else:
            today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        if today_str != self.current_day:
            old_day = self.current_day
            self.current_day = today_str
            self.daily_realized_pnl_usdt = 0.0
            self.start_of_day_balance_usdt = self.account_balance_usdt
            self.trigger_state = TriggerState.NO_SETUP

            # DAILY_LOSS_GUARD resets at new simulation trading day
            if self.daily_loss_guard_active:
                self.daily_loss_guard_active = False

            # CONSECUTIVE_LOSS_GUARD cooldown resets at new simulation trading day
            # Per spec: "cooldown resets on new trading day" — not permanent emergency latch
            if self.consecutive_loss_cooldown_active:
                self.consecutive_loss_cooldown_active = False
                self.consecutive_losses = 0  # Reset streak counter too

            # EMERGENCY_LATCH does NOT reset on trading day change
            # Only explicit operational reset clears it

            # Combined kill_switch_activated: clear if no guard is still active
            if not self.daily_loss_guard_active and not self.consecutive_loss_cooldown_active and not self.emergency_latch_active:
                self.kill_switch_activated = False
                self.kill_switch_reason = ""
                self.guard_type = GuardType.OTHER_RISK_CONTROL_BLOCK

    def register_trade_closed(self, trade: TradeRecord) -> None:
        """Update account metrics and consecutive loss counts upon trade exit."""
        self.daily_realized_pnl_usdt += trade.pnl_usdt
        self.account_balance_usdt += trade.pnl_usdt

        if trade.pnl_usdt < 0:
            self.consecutive_losses += 1
        elif trade.pnl_usdt > 0:
            self.consecutive_losses = 0

        self.active_position = None
        self.trigger_state = TriggerState.NO_SETUP

    def _activate_guard(self, guard_type: GuardType, reason: str, events_log: List[Dict]) -> None:
        """Activate a specific guard and record the event."""
        self.kill_switch_activated = True
        self.kill_switch_reason = reason
        self.guard_type = guard_type

        event = {
            "timestamp": int(datetime.now(timezone.utc).timestamp() * 1000),
            "guard_type": guard_type.value,
            "reason": reason,
        }
        events_log.append(event)

        if guard_type == GuardType.DAILY_LOSS_GUARD:
            self.daily_loss_guard_active = True
            self.daily_loss_guard_block_count += 1
            self.daily_loss_guard_total_blocked += 1
        elif guard_type == GuardType.CONSECUTIVE_LOSS_GUARD:
            self.consecutive_loss_cooldown_active = True
            self.consecutive_loss_cooldown_block_count += 1
            self.consecutive_loss_cooldown_total_blocked += 1
        elif guard_type == GuardType.EMERGENCY_LATCH:
            self.emergency_latch_active = True
            self.emergency_latch_block_count += 1
            self.emergency_latch_total_blocked += 1

    def check_kill_switch(self, max_daily_loss_pct: float, max_consecutive_losses: int) -> bool:
        """Evaluate decomposed risk-control guards.

        A) DAILY_LOSS_GUARD: intraday drawdown limit, resets at new simulation day.
        B) CONSECUTIVE_LOSS_GUARD: cooldown (NOT permanent latch), resets at new simulation day.
        C) EMERGENCY_LATCH: operational/technical only, NEVER auto-trips from trading losses.

        Returns True if ANY guard blocks new trades.
        """
        if self.kill_switch_activated:
            return True

        # --- DAILY LOSS GUARD ---
        drawdown_pct = 0.0
        if self.start_of_day_balance_usdt > 0:
            drawdown_pct = -self.daily_realized_pnl_usdt / self.start_of_day_balance_usdt

        if drawdown_pct >= max_daily_loss_pct:
            reason = f"Daily loss limit reached: -{drawdown_pct*100:.2f}% >= {max_daily_loss_pct*100:.2f}%"
            self._activate_guard(GuardType.DAILY_LOSS_GUARD, reason, self.kill_switch_events)
            return True

        # --- CONSECUTIVE LOSS GUARD (COOLDOWN, not permanent latch) ---
        if self.consecutive_losses >= max_consecutive_losses:
            reason = f"Consecutive loss cooldown: {self.consecutive_losses} >= {max_consecutive_losses}"
            self._activate_guard(GuardType.CONSECUTIVE_LOSS_GUARD, reason, self.kill_switch_events)
            return True

        return False

    def activate_emergency_latch(self, reason: str) -> None:
        """Manual activation ONLY for operational/technical critical conditions.

        NEVER call from trading loss evaluation. Only for:
        - data corruption
        - exchange/API critical failure
        - position reconciliation failure
        - duplicate execution risk
        - corrupted persistent state
        - explicit emergency stop
        """
        self._activate_guard(GuardType.EMERGENCY_LATCH, reason, self.kill_switch_events)

    def get_guard_block_counts(self) -> Dict[str, int]:
        """Return total blocks per guard type for reporting."""
        return {
            GuardType.DAILY_LOSS_GUARD.value: self.daily_loss_guard_total_blocked,
            GuardType.CONSECUTIVE_LOSS_GUARD.value: self.consecutive_loss_cooldown_total_blocked,
            GuardType.EMERGENCY_LATCH.value: self.emergency_latch_total_blocked,
            GuardType.OTHER_RISK_CONTROL_BLOCK.value: 0,
        }