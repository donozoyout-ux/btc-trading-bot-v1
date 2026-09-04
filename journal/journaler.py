"""Journaling Engine recording every market decision cycle and trade lifecycle."""

import json
import os
from pathlib import Path
from typing import Optional, Dict, Any, List
from loguru import logger

from core.models import DecisionReport, TradeRecord


class Journaler:
    """
    Persists:
    1. decisions.jsonl: Detailed audit log of every 5M evaluation cycle.
    2. trades.jsonl: Complete history of opened and closed trades.
    """

    def __init__(self, log_dir: str = "journal_logs"):
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.decisions_file = self.log_dir / "decisions.jsonl"
        self.trades_file = self.log_dir / "trades.jsonl"

    def log_decision(self, report: DecisionReport) -> None:
        """Appends a 5M cycle decision report to decisions.jsonl."""
        try:
            line = report.model_dump_json() + "\n"
            with open(self.decisions_file, "a", encoding="utf-8") as f:
                f.write(line)
        except Exception as e:
            logger.error(f"Failed to log decision to journal: {e}")

    def log_trade(self, trade: TradeRecord) -> None:
        """Appends a trade record to trades.jsonl."""
        try:
            line = trade.model_dump_json() + "\n"
            with open(self.trades_file, "a", encoding="utf-8") as f:
                f.write(line)
        except Exception as e:
            logger.error(f"Failed to log trade to journal: {e}")

    def read_all_trades(self) -> List[TradeRecord]:
        """Reads all historical trades from trades.jsonl."""
        trades: List[TradeRecord] = []
        if not self.trades_file.exists():
            return trades

        with open(self.trades_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    trades.append(TradeRecord.model_validate_json(line))
        return trades
