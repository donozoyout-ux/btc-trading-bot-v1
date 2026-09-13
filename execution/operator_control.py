"""Persistent operator controls shared by Telegram and the TESTNET runtime."""

from __future__ import annotations

import time
import threading
from pathlib import Path
from typing import Any, Dict

from storage.state_repository import create_state_repository


OPERATOR_EXECUTION_MUTEX = threading.RLock()


class OperatorControlState:
    KEY = "operator_control"

    def __init__(self, journal_dir: str = "journal_logs", state_repository=None) -> None:
        path = Path(journal_dir) / "operator_control_state.json"
        self.repository = state_repository or create_state_repository(path)

    @property
    def durability(self) -> str:
        return self.repository.durability

    def read(self) -> Dict[str, Any]:
        try:
            value = self.repository.load(self.KEY) or {}
        except Exception:
            value = {}
        return {
            "manual_entry_lock": bool(value.get("manual_entry_lock", False)),
            "locked_at": value.get("locked_at"),
            "locked_by": value.get("locked_by"),
            "reason": value.get("reason"),
            "updated_at": value.get("updated_at"),
        }

    def lock_entries(self, *, locked_by: str, reason: str) -> Dict[str, Any]:
        now = int(time.time() * 1000)
        value = {
            "manual_entry_lock": True,
            "locked_at": now,
            "locked_by": locked_by,
            "reason": reason,
            "updated_at": now,
        }
        self.repository.save(self.KEY, value)
        return dict(value)

    def unlock_entries(self, *, unlocked_by: str) -> Dict[str, Any]:
        now = int(time.time() * 1000)
        value = {
            "manual_entry_lock": False,
            "locked_at": None,
            "locked_by": unlocked_by,
            "reason": "OPERATOR_RESUMED_AUTO_ENTRIES",
            "updated_at": now,
        }
        self.repository.save(self.KEY, value)
        return dict(value)
