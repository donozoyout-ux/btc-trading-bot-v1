"""Persistence for enriched shadow-mode decisions."""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any, Dict, Set


class ShadowDecisionJournal:
    def __init__(self, log_dir: str = "journal_logs"):
        self.path = Path(log_dir) / "shadow_decisions.jsonl"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._seen: Set[str] = set()
        self._lock = threading.Lock()

    @staticmethod
    def envelope(snapshot: Dict[str, Any]) -> Dict[str, Any]:
        decision = snapshot.get("decision", {})
        timestamp = decision.get("timestamp") or snapshot.get("meta", {}).get("generated_at")
        decision_id = snapshot.get("decision_id") or f"SHADOW-BTCUSDT-{timestamp}"
        return {
            "decision_id": decision_id,
            "timestamp": timestamp,
            "shadow_mode": True,
            "orders_enabled": False,
            "market_state": snapshot.get("market", {}),
            "chart_state": snapshot.get("chart_intelligence", {}),
            "strategy_state": snapshot.get("strategy", {}),
            "news_state": snapshot.get("news", {}),
            "derivatives_state": snapshot.get("derivatives", {}),
            "risk_state": {
                "status": decision.get("risk_status"),
                "assessment": decision.get("risk_assessment"),
                "kill_switch": decision.get("kill_switch_active"),
            },
            "ai_explanation": snapshot.get("ai_analyst", {}),
            "account_state": snapshot.get("account", {}),
            "final_decision": snapshot.get("final_decision", "NO_TRADE"),
        }

    def record(self, snapshot: Dict[str, Any]) -> bool:
        payload = self.envelope(snapshot)
        decision_id = str(payload["decision_id"])
        with self._lock:
            if decision_id in self._seen:
                return False
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")
            self._seen.add(decision_id)
            return True
