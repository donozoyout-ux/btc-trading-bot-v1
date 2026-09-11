"""Daily net-realized profit objective and latched new-entry guard."""

from __future__ import annotations

import time
from datetime import datetime, timedelta
from typing import Any, Dict, Iterable, Optional
from zoneinfo import ZoneInfo

from storage.state_repository import StateRepository


TRADING_INCOME_TYPES = {"REALIZED_PNL", "COMMISSION", "FUNDING_FEE"}


def _number(value: Any) -> Optional[float]:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


class DailyProfitTargetEngine:
    """Calculate a daily objective without influencing signal or risk logic."""

    STATE_KEY = "daily_profit_target"

    def __init__(
        self,
        repository: StateRepository,
        *,
        enabled: bool = True,
        target_pct: float = 0.01,
        lock_new_entries: bool = True,
        timezone_name: str = "Europe/Istanbul",
        near_ratio: float = 0.80,
    ):
        self.repository = repository
        self.enabled = bool(enabled)
        self.target_pct = float(target_pct)
        self.lock_new_entries = bool(lock_new_entries)
        self.timezone_name = timezone_name
        self.near_ratio = float(near_ratio)

    def _day(self, now: Optional[datetime]) -> tuple[str, int, int]:
        zone = ZoneInfo(self.timezone_name)
        current = now.astimezone(zone) if now is not None else datetime.now(zone)
        start = current.replace(hour=0, minute=0, second=0, microsecond=0)
        end = start + timedelta(days=1)
        return start.date().isoformat(), int(start.timestamp() * 1000), int(end.timestamp() * 1000)

    @staticmethod
    def _rows(rows: Iterable[Dict[str, Any]], start_ms: int, end_ms: int) -> list[Dict[str, Any]]:
        return sorted(
            [
                row for row in rows
                if start_ms <= int(row.get("time") or 0) < end_ms
                and str(row.get("asset") or "USDT").upper() == "USDT"
            ],
            key=lambda row: int(row.get("time") or 0),
        )

    def build(
        self,
        *,
        current_wallet_usdt: Optional[float],
        income_rows: Iterable[Dict[str, Any]],
        income_history_available: bool,
        now: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        date, start_ms, end_ms = self._day(now)
        observed_at = int((now.timestamp() if now is not None else time.time()) * 1000)
        durable = self.repository.durability
        if not self.enabled:
            return {
                "status": "DISABLED", "enabled": False, "date_istanbul": date,
                "target_pct": self.target_pct, "new_entries_allowed": True,
                "entry_block_reason": None, "durable_state": durable, "observed_at": observed_at,
            }

        persisted = self.repository.load(self.STATE_KEY)
        same_day = persisted.get("date_istanbul") == date
        rows = self._rows(income_rows, start_ms, end_ms) if income_history_available else []
        opening = _number(persisted.get("opening_balance_usdt")) if same_day else None
        opening_source = "PERSISTED" if opening is not None else "UNAVAILABLE"
        if opening is None and current_wallet_usdt is not None and income_history_available:
            wallet_delta = sum(_number(row.get("income")) or 0.0 for row in rows)
            opening = float(current_wallet_usdt) - wallet_delta
            if opening > 0:
                opening_source = "BINANCE_RECONSTRUCTED"
            else:
                opening = None

        reached_once = bool(persisted.get("target_reached_once")) if same_day else False
        reached_at = persisted.get("reached_at") if same_day else None
        notified = bool(persisted.get("notification_sent")) if same_day else False
        if opening is None or not income_history_available:
            if reached_once:
                return {
                    "status": "LOCKED_FOR_DAY", "enabled": True, "date_istanbul": date,
                    "target_pct": self.target_pct, "opening_balance_usdt": opening,
                    "opening_balance_source": opening_source, "target_profit_usdt": _number(persisted.get("target_profit_usdt")),
                    "realized_pnl_usdt": None, "commission_usdt": None, "funding_usdt": None,
                    "net_realized_pnl_usdt": None, "remaining_to_target_usdt": None,
                    "progress_ratio": None, "progress_pct": None, "target_reached_now": False,
                    "target_reached_once": True, "reached_at": reached_at, "new_entries_allowed": False,
                    "entry_block_reason": "DAILY_PROFIT_TARGET_REACHED", "durable_state": durable,
                    "observed_at": observed_at, "notification_pending": not notified,
                }
            return {
                "status": "DATA_UNAVAILABLE", "enabled": True, "date_istanbul": date,
                "target_pct": self.target_pct, "opening_balance_usdt": opening,
                "opening_balance_source": opening_source, "target_profit_usdt": None,
                "realized_pnl_usdt": None, "commission_usdt": None, "funding_usdt": None,
                "net_realized_pnl_usdt": None, "remaining_to_target_usdt": None,
                "progress_ratio": None, "progress_pct": None, "target_reached_now": False,
                "target_reached_once": False, "reached_at": None, "new_entries_allowed": False,
                "entry_block_reason": "DAILY_PROFIT_TARGET_DATA_UNAVAILABLE", "durable_state": durable,
                "observed_at": observed_at, "notification_pending": False,
            }

        target = opening * self.target_pct
        totals = {kind: 0.0 for kind in TRADING_INCOME_TYPES}
        cumulative = 0.0
        crossed_at = None
        for row in rows:
            kind = str(row.get("incomeType") or "").upper()
            if kind not in TRADING_INCOME_TYPES:
                continue
            amount = _number(row.get("income")) or 0.0
            totals[kind] += amount
            cumulative += amount
            if crossed_at is None and cumulative + 1e-12 >= target:
                crossed_at = int(row.get("time") or observed_at)
        if crossed_at is not None and not reached_once:
            reached_once = True
            reached_at = crossed_at
        target_reached_now = cumulative + 1e-12 >= target
        progress = cumulative / target if target > 0 else None
        locked = reached_once and self.lock_new_entries
        if locked:
            status = "TARGET_REACHED" if target_reached_now else "LOCKED_FOR_DAY"
        elif progress is not None and progress >= self.near_ratio:
            status = "NEAR_TARGET"
        else:
            status = "ACTIVE"
        state = {
            "date_istanbul": date, "opening_balance_usdt": opening,
            "target_profit_usdt": target, "target_reached_once": reached_once,
            "reached_at": reached_at, "notification_sent": notified,
        }
        self.repository.save(self.STATE_KEY, state)
        return {
            "status": status, "enabled": True, "date_istanbul": date,
            "target_pct": self.target_pct, "opening_balance_usdt": opening,
            "opening_balance_source": opening_source, "target_profit_usdt": target,
            "realized_pnl_usdt": totals["REALIZED_PNL"],
            "commission_usdt": totals["COMMISSION"], "funding_usdt": totals["FUNDING_FEE"],
            "net_realized_pnl_usdt": cumulative,
            "remaining_to_target_usdt": max(0.0, target - cumulative),
            "progress_ratio": progress, "progress_pct": progress * 100 if progress is not None else None,
            "target_reached_now": target_reached_now, "target_reached_once": reached_once,
            "reached_at": reached_at, "new_entries_allowed": not locked,
            "entry_block_reason": "DAILY_PROFIT_TARGET_REACHED" if locked else None,
            "durable_state": durable, "observed_at": observed_at,
            "notification_pending": reached_once and not notified,
        }

    def mark_notification_sent(self) -> None:
        state = self.repository.load(self.STATE_KEY)
        if state:
            state["notification_sent"] = True
            self.repository.save(self.STATE_KEY, state)
