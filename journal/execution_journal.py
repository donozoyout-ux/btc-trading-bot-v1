"""Append-only sanitized audit journal for TESTNET execution events."""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Optional


class ExecutionJournal:
    def __init__(self, log_dir: str = "journal_logs"):
        self.directory = Path(log_dir)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.events_file = self.directory / "execution_events.jsonl"
        self.state_file = self.directory / "execution_state.json"

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
        safe = dict(state)
        safe["updated_at"] = int(time.time() * 1000)
        self.state_file.write_text(json.dumps(safe, ensure_ascii=False, indent=2), encoding="utf-8")

    def read_state(self) -> Dict[str, Any]:
        if not self.state_file.exists():
            return {}
        try:
            return json.loads(self.state_file.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return {}
