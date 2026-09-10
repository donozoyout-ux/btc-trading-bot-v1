"""Append-only sanitized audit journal for TESTNET execution events."""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Optional

from storage.state_repository import StateRepository, create_state_repository, sanitize_state


class ExecutionJournal:
    def __init__(self, log_dir: str = "journal_logs", state_repository: Optional[StateRepository] = None):
        self.directory = Path(log_dir)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.events_file = self.directory / "execution_events.jsonl"
        self.state_file = self.directory / "execution_state.json"
        self.state_repository = state_repository or create_state_repository(self.state_file)

    @property
    def durable_state(self) -> str:
        return self.state_repository.durability

    def record(
        self,
        *,
        decision_id: Optional[str],
        action: str,
        side: Optional[str] = None,
        quantity: Optional[float] = None,
        price: Optional[float] = None,
        binance_order_id: Optional[int] = None,
        status: str,
        reason: Optional[str] = None,
        position_before: Optional[Dict[str, Any]] = None,
        position_after: Optional[Dict[str, Any]] = None,
        context: Optional[Dict[str, Any]] = None,
        details: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        event = {
            "event_id": f"EXEC-{uuid.uuid4().hex[:16]}",
            "decision_id": decision_id,
            "timestamp": int(time.time() * 1000),
            "symbol": "BTCUSDT",
            "action": action,
            "side": side,
            "quantity": quantity,
            "price": price,
            "binance_order_id": binance_order_id,
            "status": status,
            "reason": reason,
            "position_before": position_before,
            "position_after": position_after,
        "context": dict(context or {}),
        "details": details,
        }
        with self.events_file.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n")
        return event

    def write_state(self, state: Dict[str, Any]) -> None:
        safe = sanitize_state(dict(state))
        safe["updated_at"] = int(time.time() * 1000)
        self.state_repository.save("execution_state", safe)

    def read_state(self) -> Dict[str, Any]:
        try:
            return self.state_repository.load("execution_state")
        except (OSError, ValueError, TypeError):
            return {}
