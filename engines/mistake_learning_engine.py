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
                            return rows
                        try:
                            item = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        if isinstance(item, dict):
                            rows.append(item)
            except OSError:
                continue
        return rows

    @staticmethod
    def _context_key(row: Dict[str, Any]) -> str:
        parts = []
        for key in ("setup", "setup_type", "side", "regime", "reason"):
            value = row.get(key)
            if value not in (None, ""):
                parts.append(f"{key}={value}")
        return " | ".join(parts[:4]) or "UNCLASSIFIED"

    def analyze(self) -> Dict[str, Any]:
        rows = self._read_rows(self._candidate_files())
        if not rows:
            return {
                "status": "WARMUP",
                "mode": "ADVISORY_ONLY",
                "samples": 0,
                "mistake_events": 0,
                "top_mistakes": [],
                "review_candidates": [],
                "auto_parameter_changes": False,
            }

        actions = Counter(str(row.get("action") or row.get("event") or "UNKNOWN") for row in rows)
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
            for action, count in actions.most_common(8)
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

        return {
            "status": "READY" if len(rows) >= self.min_samples else "WARMUP",
            "mode": "ADVISORY_ONLY",
            "samples": len(rows),
            "mistake_events": len(mistake_rows),
            "top_mistakes": top_mistakes,
            "review_candidates": review_candidates,
            "auto_parameter_changes": False,
        }
