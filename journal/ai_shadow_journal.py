"""Durable, execution-independent history and outcome metrics for AI opinions."""

from __future__ import annotations

import threading
from typing import Any, Dict, Iterable, List, Optional

from storage.state_repository import StateRepository, create_state_repository


class AIShadowJournal:
    """Stores advisory analyses and links them to later exchange-proven trades."""

    def __init__(
        self,
        log_dir: str = "journal_logs",
        state_repository: Optional[StateRepository] = None,
    ):
        self.repository = state_repository or create_state_repository(
            f"{log_dir}/ai_shadow_journal.json"
        )
        self._lock = threading.Lock()
        try:
            stored = self.repository.load("ai_shadow_journal")
        except Exception:
            stored = {}
        self._persistence_error = False
        self._records: List[Dict[str, Any]] = list(stored.get("records") or [])[-500:]

    @staticmethod
    def _record(snapshot: Dict[str, Any], candle_timestamp: int) -> Dict[str, Any]:
        ai = snapshot.get("ai_analyst") or {}
        strategy = snapshot.get("strategy") or {}
        news = snapshot.get("news") or {}
        decision = snapshot.get("decision") or {}
        return {
            "analysis_id": f"AI-BTCUSDT-{int(candle_timestamp)}",
            "timestamp": ai.get("observed_at") or (snapshot.get("meta") or {}).get("generated_at"),
            "candle_timestamp": int(candle_timestamp),
            "deterministic_decision": snapshot.get("final_decision"),
            "setup": strategy.get("setup_type") or decision.get("setup"),
            "direction": strategy.get("direction"),
            "regime": decision.get("regime"),
            "market_bias": ai.get("market_bias"),
            "setup_quality": ai.get("setup_quality"),
            "trade_opinion": ai.get("trade_opinion"),
            "confidence": ai.get("confidence"),
            "conflicts": list(ai.get("conflicts") or []),
            "news_risk": news.get("news_risk"),
            "derivatives_summary": ai.get("derivatives_summary"),
            "execution_authority": False,
            "outcome": None,
        }

    def _save(self) -> None:
        try:
            self.repository.save("ai_shadow_journal", {"records": self._records[-500:]})
            self._persistence_error = False
        except Exception:
            # AI telemetry is non-authoritative and must never stop the bot.
            self._persistence_error = True

    def record(self, snapshot: Dict[str, Any], candle_timestamp: int) -> bool:
        ai = snapshot.get("ai_analyst") or {}
        if ai.get("status") != "AVAILABLE":
            return False
        row = self._record(snapshot, candle_timestamp)
        with self._lock:
            if any(item.get("analysis_id") == row["analysis_id"] for item in self._records):
                return False
            self._records.append(row)
            self._records = self._records[-500:]
            self._save()
        return True

    def reconcile_completed_trades(self, trades: Iterable[Dict[str, Any]]) -> int:
        """Attach the latest opinion at/before entry; never infer from future analysis."""
        linked = 0
        with self._lock:
            for trade in trades:
                opened_at = trade.get("entry_opened_at")
                closed_at = trade.get("closed_at")
                if opened_at is None or closed_at is None:
                    continue
                trade_key = f"{int(opened_at)}:{int(closed_at)}"
                if any((row.get("outcome") or {}).get("trade_key") == trade_key for row in self._records):
                    continue
                candidates = [
                    row for row in self._records
                    if row.get("timestamp") is not None
                    and int(row["timestamp"]) <= int(opened_at)
                    and int(opened_at) - int(row["timestamp"]) <= 10 * 60 * 1000
                ]
                if not candidates:
                    continue
                target = max(candidates, key=lambda row: int(row.get("timestamp") or 0))
                target["outcome"] = {
                    "trade_key": trade_key,
                    "entry_opened_at": int(opened_at),
                    "closed_at": int(closed_at),
                    "net_pnl_usdt": float(trade.get("net_pnl_usdt") or 0.0),
                    "exit_reason": str(trade.get("exit_reason") or "UNKNOWN"),
                }
                linked += 1
            if linked:
                self._save()
        return linked

    def summary(self) -> Dict[str, Any]:
        result: Dict[str, Any] = {"status": "DEGRADED" if self._persistence_error else "AVAILABLE", "total_analyses": len(self._records), "by_opinion": {}}
        for opinion in ("TAKE", "WAIT", "AVOID"):
            rows = [row for row in self._records if row.get("trade_opinion") == opinion]
            completed = [row["outcome"] for row in rows if row.get("outcome")]
            wins = [row for row in completed if float(row.get("net_pnl_usdt") or 0) > 0]
            gross_profit = sum(max(0.0, float(row.get("net_pnl_usdt") or 0)) for row in completed)
            gross_loss = abs(sum(min(0.0, float(row.get("net_pnl_usdt") or 0)) for row in completed))
            result["by_opinion"][opinion] = {
                "analysis_count": len(rows),
                "sample_size": len(completed),
                "win_rate": round(len(wins) / len(completed) * 100, 2) if completed else None,
                "profit_factor": round(gross_profit / gross_loss, 3) if gross_loss else None,
                "average_pnl_usdt": round(sum(float(row.get("net_pnl_usdt") or 0) for row in completed) / len(completed), 4) if completed else None,
                "stop_rate": round(sum(row.get("exit_reason") == "STOP_LOSS" for row in completed) / len(completed) * 100, 2) if completed else None,
            }
        quality_rows = [
            row for row in self._records
            if float(row.get("setup_quality") or 0) >= 75 and row.get("outcome")
        ]
        result["quality_gte_75"] = {
            "sample_size": len(quality_rows),
            "average_pnl_usdt": round(sum(float(row["outcome"].get("net_pnl_usdt") or 0) for row in quality_rows) / len(quality_rows), 4) if quality_rows else None,
        }
        result["execution_authority"] = False
        result["durability"] = self.repository.durability
        return result
