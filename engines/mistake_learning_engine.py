"""Advisory post-trade learning engine.

The engine learns from execution journal outcomes without mutating live strategy
parameters. It produces evidence-backed cautions and review candidates only.
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List


class MistakeLearningEngine:
    """Summarize recurring execution/trade mistakes from persisted journal data.

    This component is intentionally advisory-only: it never changes settings,
    never enables/disables setups, and never creates execution authority.
    """

    ERROR_ACTIONS = {
        "PROTECTION_FAILURE",
        "RECONCILIATION_FAILURE",
        "UNPROTECTED_POSITION",
        "UNEXPECTED_OPEN_ORDERS",
        "ORDER_REJECTED",
        "STOP_LOSS",
    }
    CLOSE_ACTIONS = {"STOP_LOSS", "TAKE_PROFIT", "POSITION_CLOSED", "ORDER_CLOSED"}

    def __init__(self, journal_dir: str | Path, min_samples: int = 3) -> None:
        self.journal_dir = Path(journal_dir)
        self.min_samples = max(2, int(min_samples))

    def _candidate_files(self) -> List[Path]:
        if not self.journal_dir.exists():
            return []
        return sorted(
            [
                path
                for path in self.journal_dir.rglob("*.jsonl")
                if path.is_file() and "execution" in path.name.lower()
            ],
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )

    @staticmethod
    def _read_rows(paths: Iterable[Path], max_rows: int = 1000) -> List[Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []
        for path in paths:
            try:
                with path.open("r", encoding="utf-8") as handle:
                    for line in handle:
                        if len(rows) >= max_rows:
                            break
                        try:
                            item = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        if isinstance(item, dict):
                            rows.append(item)
            except OSError:
                continue
        rows.sort(key=lambda row: int(row.get("timestamp") or 0))
        return rows[-max_rows:]

    @staticmethod
    def _row_context(row: Dict[str, Any]) -> Dict[str, Any]:
        context = row.get("context")
        return dict(context) if isinstance(context, dict) else {}

    @classmethod
    def _context_key(cls, row: Dict[str, Any]) -> str:
        context = cls._row_context(row)
        parts = []
        for key in ("setup_type", "direction", "regime", "market_basis", "volatility"):
            value = context.get(key)
            if value not in (None, ""):
                parts.append(f"{key}={value}")
        if not parts:
            for key in ("side", "reason"):
                value = row.get(key)
                if value not in (None, ""):
                    parts.append(f"{key}={value}")
        return " | ".join(parts[:5]) or "UNCLASSIFIED"

    @classmethod
    def _attach_entry_context(cls, rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Carry the last real ENTRY context into its later close/error event."""
        enriched: List[Dict[str, Any]] = []
        active_context: Dict[str, Any] = {}
        for original in rows:
            row = dict(original)
            action = str(row.get("action") or row.get("event") or "").upper()
            context = cls._row_context(row)
            if action == "ENTRY" and context:
                active_context = dict(context)
            elif not context and active_context and (
                action in cls.CLOSE_ACTIONS or action in cls.ERROR_ACTIONS
            ):
                row["context"] = dict(active_context)
            enriched.append(row)
            if action in cls.CLOSE_ACTIONS:
                active_context = {}
        return enriched

    def analyze(self) -> Dict[str, Any]:
        rows = self._attach_entry_context(self._read_rows(self._candidate_files()))
        if not rows:
            return {
                "status": "WARMUP",
                "mode": "ADVISORY_ONLY",
                "samples": 0,
                "trade_entries": 0,
                "mistake_events": 0,
                "top_mistakes": [],
                "review_candidates": [],
                "auto_parameter_changes": False,
            }

        actions = Counter(str(row.get("action") or row.get("event") or "UNKNOWN").upper() for row in rows)
        mistake_rows = [
            row
            for row in rows
            if str(row.get("action") or row.get("event") or "").upper() in self.ERROR_ACTIONS
            or str(row.get("status") or "").upper() in {
                "FAILED",
                "REJECTED",
                "KILL_SWITCH",
                "POSITION_FLATTENED",
            }
        ]
        contexts: Dict[str, int] = defaultdict(int)
        for row in mistake_rows:
            contexts[self._context_key(row)] += 1

        top_mistakes = [
            {"action": action, "count": count}
            for action, count in actions.most_common(10)
            if action in self.ERROR_ACTIONS or "FAIL" in action or "REJECT" in action
        ]
        review_candidates = [
            {
                "context": context,
                "count": count,
                "recommendation": "REVIEW_AND_BACKTEST",
            }
            for context, count in sorted(contexts.items(), key=lambda item: item[1], reverse=True)
            if count >= self.min_samples
        ][:8]

        trade_entries = actions.get("ENTRY", 0)
        return {
            "status": "READY" if trade_entries >= self.min_samples else "WARMUP",
            "mode": "ADVISORY_ONLY",
            "samples": len(rows),
            "trade_entries": trade_entries,
            "mistake_events": len(mistake_rows),
            "stop_losses": actions.get("STOP_LOSS", 0),
            "take_profits": actions.get("TAKE_PROFIT", 0),
            "protection_failures": actions.get("PROTECTION_FAILURE", 0),
            "top_mistakes": top_mistakes,
            "review_candidates": review_candidates,
            "auto_parameter_changes": False,
        }
