"""Advisory post-trade learning engine.

This engine mines persisted TESTNET execution events for recurring operational
mistakes and repeated loss patterns. It is deliberately advisory-only: it never
changes strategy parameters, risk limits, setup flags, or execution authority.
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional


class MistakeLearningEngine:
    """Build evidence-backed review candidates from the execution journal."""

    OPERATIONAL_ERROR_ACTIONS = {
        "PROTECTION_FAILURE",
        "RECONCILIATION_FAILURE",
        "UNPROTECTED_POSITION",
        "UNEXPECTED_OPEN_ORDERS",
        "ORDER_REJECTED",
    }
    LOSS_ACTIONS = {"STOP_LOSS"}
    WIN_ACTIONS = {"TAKE_PROFIT"}
    CLOSE_ACTIONS = LOSS_ACTIONS | WIN_ACTIONS | {"POSITION_CLOSED", "ORDER_CLOSED"}

    def __init__(self, journal_dir: str | Path, min_samples: int = 5) -> None:
        self.journal_dir = Path(journal_dir)
        self.min_samples = max(3, int(min_samples))

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
        )

    @staticmethod
    def _read_rows(paths: Iterable[Path], max_rows: int = 2000) -> List[Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []
        for path in paths:
            try:
                with path.open("r", encoding="utf-8") as handle:
                    for line in handle:
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
        nested = row.get("context")
        context = dict(nested) if isinstance(nested, dict) else {}
        aliases = {
            "setup": "setup_type",
            "setup_type": "setup_type",
            "direction": "direction",
            "regime": "regime",
            "volatility": "volatility",
            "location": "location",
            "trigger": "trigger",
            "market_basis": "market_basis",
            "market_data_source": "market_data_source",
        }
        for source_key, target_key in aliases.items():
            value = row.get(source_key)
            if value not in (None, "") and context.get(target_key) in (None, ""):
                context[target_key] = value
        if context.get("direction") in (None, "") and row.get("side") not in (None, ""):
            side = str(row.get("side")).upper()
            context["direction"] = "LONG" if side == "BUY" else "SHORT" if side == "SELL" else side
        return context

    @classmethod
    def _context_key(cls, row_or_context: Dict[str, Any]) -> str:
        context = cls._row_context(row_or_context) if "context" in row_or_context else dict(row_or_context)
        parts = []
        for key in ("setup_type", "direction", "regime", "market_basis"):
            value = context.get(key)
            if value not in (None, ""):
                parts.append(f"{key}={value}")
        return " | ".join(parts) or "UNCLASSIFIED"

    @staticmethod
    def _action(row: Dict[str, Any]) -> str:
        return str(row.get("action") or row.get("event") or "UNKNOWN").upper()

    def _trade_outcomes(self, rows: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
        """Correlate terminal closes with the most recent real entry context."""
        active_context: Optional[Dict[str, Any]] = None
        outcomes: Dict[str, Dict[str, Any]] = defaultdict(
            lambda: {"trades": 0, "wins": 0, "losses": 0, "other_closes": 0}
        )
        for row in rows:
            action = self._action(row)
            if action == "ENTRY" and str(row.get("decision_id") or "") != "SMOKE_TEST":
                active_context = self._row_context(row)
                continue
            if action not in self.CLOSE_ACTIONS:
                continue
            context = self._row_context(row) or dict(active_context or {})
            key = self._context_key(context)
            bucket = outcomes[key]
            bucket["trades"] += 1
            if action in self.WIN_ACTIONS:
                bucket["wins"] += 1
            elif action in self.LOSS_ACTIONS:
                bucket["losses"] += 1
            else:
                bucket["other_closes"] += 1
            active_context = None
        return dict(outcomes)

    def analyze(self) -> Dict[str, Any]:
        rows = self._read_rows(self._candidate_files())
        if not rows:
            return {
                "status": "WARMUP",
                "mode": "ADVISORY_ONLY",
                "samples": 0,
                "closed_trades": 0,
                "mistake_events": 0,
                "top_mistakes": [],
                "trade_patterns": [],
                "review_candidates": [],
                "auto_parameter_changes": False,
            }

        actions = Counter(self._action(row) for row in rows)
        mistake_rows = [
            row
            for row in rows
            if self._action(row) in (self.OPERATIONAL_ERROR_ACTIONS | self.LOSS_ACTIONS)
            or str(row.get("status") or "").upper()
            in {"FAILED", "REJECTED", "KILL_SWITCH", "POSITION_FLATTENED"}
        ]

        context_errors: Dict[str, int] = defaultdict(int)
        for row in mistake_rows:
            context_errors[self._context_key(self._row_context(row) or row)] += 1

        top_mistakes = [
            {"action": action, "count": count}
            for action, count in actions.most_common(10)
            if action in (self.OPERATIONAL_ERROR_ACTIONS | self.LOSS_ACTIONS)
            or "FAIL" in action
            or "REJECT" in action
        ]

        outcomes = self._trade_outcomes(rows)
        trade_patterns = []
        review_candidates = []
        for context, bucket in sorted(
            outcomes.items(), key=lambda item: item[1]["trades"], reverse=True
        ):
            trades = int(bucket["trades"])
            wins = int(bucket["wins"])
            losses = int(bucket["losses"])
            decisive = wins + losses
            loss_rate = (losses / decisive) if decisive else None
            pattern = {
                "context": context,
                "trades": trades,
                "wins": wins,
                "losses": losses,
                "other_closes": int(bucket["other_closes"]),
                "loss_rate": round(loss_rate, 4) if loss_rate is not None else None,
                "sample_ready": trades >= self.min_samples,
            }
            trade_patterns.append(pattern)
            if (
                trades >= self.min_samples
                and decisive >= self.min_samples
                and loss_rate is not None
                and loss_rate >= 0.60
            ):
                review_candidates.append(
                    {
                        "context": context,
                        "count": trades,
                        "evidence": f"loss_rate={loss_rate:.1%} over {decisive} decisive closes",
                        "recommendation": "REVIEW_AND_BACKTEST",
                    }
                )

        for context, count in sorted(context_errors.items(), key=lambda item: item[1], reverse=True):
            if count >= self.min_samples:
                review_candidates.append(
                    {
                        "context": context,
                        "count": count,
                        "evidence": "repeated operational/loss events",
                        "recommendation": "REVIEW_AND_BACKTEST",
                    }
                )

        closed_trades = sum(bucket["trades"] for bucket in outcomes.values())
        return {
            "status": "READY" if closed_trades >= self.min_samples else "WARMUP",
            "mode": "ADVISORY_ONLY",
            "samples": len(rows),
            "closed_trades": closed_trades,
            "mistake_events": len(mistake_rows),
            "stop_losses": actions.get("STOP_LOSS", 0),
            "take_profits": actions.get("TAKE_PROFIT", 0),
            "protection_failures": actions.get("PROTECTION_FAILURE", 0),
            "top_mistakes": top_mistakes[:8],
            "trade_patterns": trade_patterns[:10],
            "review_candidates": review_candidates[:8],
            "auto_parameter_changes": False,
        }
