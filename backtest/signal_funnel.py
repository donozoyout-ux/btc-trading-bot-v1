"""Conditional signal funnel for backtest pipeline analysis.

Phase 2A semantics: the conditional chain contains ONLY true pipeline gates —
stages where the pipeline itself vetoes progress. STRUCTURE and LOCATION are
NOT gates (the pipeline consumes them as setup-detection inputs but never
vetoes on their coarse labels); they are recorded as parallel market-context
observations that cannot swallow evaluations. This guarantees every opened
trade's evaluation flows through the full chain: TRADES_OPENED ==
EXECUTABLE_CANDIDATES == RISK_PASS by construction, and all three equal the
execution-path trade count.

Strict chain semantics: a stage is counted ONLY if every previous TRUE gate
passed in the same evaluation. A failing evaluation is recorded exactly once,
as a rejection at the FIRST gate it failed, with an explicit reason.
"""

from typing import Dict, List
from dataclasses import dataclass, field


@dataclass
class FunnelStage:
    count: int = 0
    rejections: Dict[str, int] = field(default_factory=dict)

    def add_pass(self, count: int = 1) -> None:
        self.count += count

    def add_rejection(self, reason: str, count: int = 1) -> None:
        self.rejections[reason] = self.rejections.get(reason, 0) + count


class SignalFunnel:
    """Strict conditional signal funnel with 14 stages mirroring the pipeline.

    Pipeline order:
    TOTAL → DATA_HEALTH → REGIME → KILL_SWITCH → STRUCTURE →
    GOOD_LOCATION → SETUP → ENTRY_TRIGGER → MOMENTUM → DERIVATIVES →
    TRADE_PLAN → RISK → EXECUTABLE → TRADES_OPENED

    Stage semantics (Phase 2A):
    - RISK_PASS: risk engine ACCEPTed the trade plan.
    - EXECUTABLE_CANDIDATES: risk accepted AND final decision is LONG/SHORT_ENTRY
      (derived from the canonical DecisionReport; in the current architecture the
      derivatives veto and kill-switch veto both precede risk evaluation, so this
      coincides with RISK_PASS — kept as an explicit stage so any future
      post-risk veto becomes visible instead of silently collapsing counts).
    - TRADES_OPENED: a TradeRecord was actually created by the execution path
      (derived from _process_entry's return value, not inferred from the report).

    Invariant: every stage count <= previous stage count.
    Every conversion_from_previous <= 100%.
    """

    STAGES = [
        "TOTAL_EVALUATIONS",
        "DATA_HEALTH_PASS",
        "REGIME_ELIGIBLE",
        "KILL_SWITCH_PASS",
        "STRUCTURE_ELIGIBLE",
        "GOOD_TRADE_LOCATION",
        "SETUP_DETECTED",
        "ENTRY_TRIGGER_DETECTED",
        "MOMENTUM_PASS",
        "DERIVATIVES_ACCEPTABLE",
        "TRADE_PLAN_CREATED",
        "RISK_PASS",
        "EXECUTABLE_CANDIDATES",
        "TRADES_OPENED",
    ]

    def __init__(self):
        self.stages: Dict[str, FunnelStage] = {s: FunnelStage() for s in self.STAGES}

    def record_evaluation(self, total: int = 1) -> None:
        self.stages["TOTAL_EVALUATIONS"].add_pass(total)

    def record_pass(self, stage: str, count: int = 1) -> None:
        if stage in self.stages:
            self.stages[stage].add_pass(count)

    def record_rejection(self, stage: str, reason: str, count: int = 1) -> None:
        """Record an evaluation rejected at `stage` (first failure point)."""
        if stage in self.stages:
            self.stages[stage].add_rejection(reason, count)

    def record_trade_opened(self) -> None:
        # Kept for backward compatibility; prefer record_pass("TRADES_OPENED").
        self.stages["TRADES_OPENED"].add_pass()

    def get_funnel(self) -> Dict:
        """Return conditional funnel: each count <= previous count by construction."""
        result = {}
        prev_count = None
        for stage_name in self.STAGES:
            stage = self.stages[stage_name]
            count = stage.count
            conversion_from_prev = 0.0
            conversion_from_total = 0.0
            if prev_count is not None and prev_count > 0:
                conversion_from_prev = (count / prev_count) * 100.0
            if self.stages["TOTAL_EVALUATIONS"].count > 0:
                conversion_from_total = (count / self.stages["TOTAL_EVALUATIONS"].count) * 100.0
            result[stage_name] = {
                "count": count,
                "conversion_from_prev_pct": round(conversion_from_prev, 2),
                "conversion_from_total_pct": round(conversion_from_total, 2),
                "top_rejection_reasons": dict(
                    sorted(stage.rejections.items(), key=lambda x: x[1], reverse=True)[:10]
                ),
            }
            prev_count = count
        return result
