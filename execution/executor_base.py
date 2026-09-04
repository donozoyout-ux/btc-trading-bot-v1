"""Abstract Base Class for order executors."""

from abc import ABC, abstractmethod
from typing import Optional
from core.models import DecisionReport, TradeRecord
from core.state import BotState


class BaseExecutor(ABC):
    """Abstract interface for trade executors."""

    @abstractmethod
    def process_decision(self, report: DecisionReport, state: BotState) -> Optional[TradeRecord]:
        """Processes a decision report and opens an order if ENTRY is indicated."""
        pass

    @abstractmethod
    def update_open_positions(self, state: BotState) -> None:
        """Polls or evaluates active positions for exit or reconciliation."""
        pass
