"""Read-only strategy interpretation over the authoritative pipeline decision."""

from __future__ import annotations

from typing import Any, Dict, List

from config.constants import DecisionStatus, RiskDecision, SetupType
from core.models import DecisionReport


class StrategyOrchestrator:
    """Unify Setup A/B/C telemetry without replacing deterministic engines."""

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
        elif report.final_decision in (DecisionStatus.LONG_ENTRY, DecisionStatus.LONG_WATCH):
            direction = "LONG"
        elif report.final_decision in (DecisionStatus.SHORT_ENTRY, DecisionStatus.SHORT_WATCH):
            direction = "SHORT"

        reasons: List[str] = []
        blockers: List[str] = []
        if setup_type != SetupType.NONE.value:
            reasons.append(report.reason)
        else:
            blockers.append("NO_DETERMINISTIC_SETUP")
        if mtf.get("conflicts"):
            blockers.extend(mtf["conflicts"])
        if news.get("news_risk") in ("HIGH", "EXTREME"):
            blockers.append(f"NEWS_RISK_{news['news_risk']}")
        if report.derivatives.value == "REJECT":
            blockers.append("DERIVATIVES_REJECT")
        if report.kill_switch_active:
            blockers.append("KILL_SWITCH_ACTIVE")
        if report.risk_status != RiskDecision.ACCEPT_TRADE:
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

        eligible = bool(
            report.final_decision in (DecisionStatus.LONG_ENTRY, DecisionStatus.SHORT_ENTRY)
            and report.risk_status == RiskDecision.ACCEPT_TRADE
            and not report.kill_switch_active
        )
        return {
            "setup_type": setup_type,
            "direction": direction,
            "eligible": eligible,
            "score": score,
            "reasons": [reason for reason in reasons if reason],
            "blocking_reasons": list(dict.fromkeys(blockers)),
            "entry_trigger_state": report.trigger_state.value,
            "trade_plan": report.trade_plan.model_dump(mode="json") if report.trade_plan else None,
            "risk_decision": report.risk_status.value,
            "execution_authority": False,
        }

    @staticmethod
    def final_decision(report: DecisionReport, ai: Dict[str, Any] | None = None) -> str:
        """AI is deliberately ignored; a risk rejection always resolves to NO_TRADE."""
        if report.kill_switch_active or report.risk_status != RiskDecision.ACCEPT_TRADE:
            return DecisionStatus.NO_TRADE.value
        return report.final_decision.value
