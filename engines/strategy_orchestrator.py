"""Read-only strategy interpretation over the authoritative pipeline decision."""

from __future__ import annotations

from typing import Any, Dict, List

from config.constants import DecisionStatus, DerivativesStatus, RiskDecision, SetupType, TriggerState
from core.models import DecisionReport


class StrategyOrchestrator:
    """Unify Setup A/B/C telemetry without replacing deterministic engines."""

    @staticmethod
    def _risk_rejected(report: DecisionReport) -> bool:
        """Return true only when risk was actually reached and rejected.

        Modern pipeline reports carry ``risk_assessment``. A narrow legacy
        compatibility path remains for historical tests/reports that encoded an
        ENTRY_READY risk rejection only through ``risk_status``.
        """
        if report.risk_assessment is not None:
            return report.risk_assessment.decision != RiskDecision.ACCEPT_TRADE
        return bool(
            report.trigger_state == TriggerState.ENTRY_READY
            and report.risk_status == RiskDecision.REJECT_TRADE
            and report.derivatives != DerivativesStatus.REJECT
            and not report.kill_switch_active
            and report.setup != SetupType.NONE
        )

    def summarize(
        self,
        report: DecisionReport,
        chart: Dict[str, Any],
        mtf: Dict[str, Any],
        news: Dict[str, Any],
    ) -> Dict[str, Any]:
        setup_type = report.setup.value
        direction = "WAIT"
        if report.trade_plan:
            direction = report.trade_plan.direction.value
        elif report.setup != SetupType.NONE:
            direction = report.setup_direction.value
        elif report.final_decision in (DecisionStatus.LONG_ENTRY, DecisionStatus.LONG_WATCH):
            direction = "LONG"
        elif report.final_decision in (DecisionStatus.SHORT_ENTRY, DecisionStatus.SHORT_WATCH):
            direction = "SHORT"

        reasons: List[str] = []
        blockers: List[str] = []
        if setup_type != SetupType.NONE.value:
            reasons.append(report.reason)
        else:
            if "EXPERIMENTAL_SETUP_DISABLED" in report.reason:
                blockers.append("EXPERIMENTAL_SETUP_DISABLED")
            else:
                blockers.append("NO_DETERMINISTIC_SETUP")
        if report.derivatives.value == "REJECT":
            blockers.append("DERIVATIVES_REJECT")
        if report.entry_quality_assessment and report.entry_quality_assessment.decision == "REJECT":
            blockers.extend(report.entry_quality_assessment.reason_codes)
        if report.kill_switch_active:
            blockers.append("KILL_SWITCH_ACTIVE")
        if self._risk_rejected(report):
            blockers.append("RISK_REJECT")

        score = 0
        if setup_type != SetupType.NONE.value:
            score += 35
        if mtf.get("overall_bias") in ("STRONG_LONG", "STRONG_SHORT"):
            score += 25
        elif mtf.get("overall_bias") in ("LONG", "SHORT"):
            score += 15
        if report.trigger_state.value == "ENTRY_READY":
            score += 20
        if report.derivatives.value == "CONFIRM":
            score += 10
        if news.get("news_risk") == "LOW":
            score += 10
        score = min(score, 100)

        warnings = list(dict.fromkeys((mtf.get("conflicts") or []) + ([f"NEWS_RISK_{news['news_risk']}"] if news.get("news_risk") in ("HIGH", "EXTREME") else [])))
        # Advisory MTF/news context never enters the hard-blocker collection.
        hard_blockers = list(dict.fromkeys(blockers))
        eligible = bool(
            report.final_decision in (DecisionStatus.LONG_ENTRY, DecisionStatus.SHORT_ENTRY)
            and report.risk_assessment is not None
            and report.risk_assessment.decision == RiskDecision.ACCEPT_TRADE
            and not report.kill_switch_active
            and not hard_blockers
        )
        return {
            "setup_type": setup_type,
            "direction": direction,
            "eligible": eligible,
            "score": score,
            "reasons": [reason for reason in reasons if reason],
            "blocking_reasons": hard_blockers,
            "hard_blockers": hard_blockers,
            "warnings": warnings,
            "entry_quality_assessment": report.entry_quality_assessment.model_dump(mode="json") if report.entry_quality_assessment else None,
            "entry_trigger_state": report.trigger_state.value,
            "trade_plan": report.trade_plan.model_dump(mode="json") if report.trade_plan else None,
            "risk_decision": report.risk_status.value,
            "risk_evaluated": report.risk_assessment is not None or self._risk_rejected(report),
            "execution_authority": False,
        }

    @classmethod
    def final_decision(cls, report: DecisionReport, ai: Dict[str, Any] | None = None) -> str:
        """AI is advisory only; WATCH survives until risk is actually evaluated."""
        if report.kill_switch_active or cls._risk_rejected(report):
            return DecisionStatus.NO_TRADE.value
        return report.final_decision.value
