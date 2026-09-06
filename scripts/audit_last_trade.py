"""Create an evidence-only forensic report for the latest TESTNET entry."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional


ENTRY_ACTIONS = {"ENTRY_SNAPSHOT", "ENTRY", "MARKET_ENTRY", "ENTRY_FILLED", "ORDER_SUBMITTED"}
MANAGEMENT_ACTIONS = {
    "POSITION_MANAGEMENT", "RECOVERY_WAIT", "HOLD", "POSITION_HOLD", "NO_CHANGE",
    "MANAGEMENT_NO_CHANGE", "EXIT_EARLY", "EARLY_EXIT", "STOP_TIGHTEN",
    "STOP_TIGHTENED", "TP2_REPLAN", "TP2_REPLANNED",
}


def _jsonl(path: Path) -> List[Dict[str, Any]]:
    if not path.is_file():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            rows.append(json.loads(line))
        except (ValueError, TypeError):
            continue
    return rows


def build_audit(journal_dir: str | Path) -> Dict[str, Any]:
    directory = Path(journal_dir)
    executions = _jsonl(directory / "execution_events.jsonl")
    decisions = _jsonl(directory / "shadow_decisions.jsonl")
    entries = [event for event in executions if str(event.get("action", "")).upper() in ENTRY_ACTIONS]
    if not entries:
        return {"status": "SOURCE_DATA_UNAVAILABLE", "missing_fields": ["latest TESTNET entry event"], "entry": None, "management_decisions": []}
    entry_event = max(entries, key=lambda event: int(event.get("timestamp") or 0))
    decision_id = entry_event.get("decision_id")
    matching = next((row for row in reversed(decisions) if row.get("decision_id") == decision_id), None)
    frozen = next((event for event in reversed(executions) if event.get("decision_id") == decision_id and event.get("action") == "ENTRY_SNAPSHOT"), None)
    details = (frozen or entry_event).get("details") or {}
    context = entry_event.get("context") or {}
    entry_ts = int(entry_event.get("timestamp") or 0)
    after = [event for event in executions if int(event.get("timestamp") or 0) >= entry_ts and str(event.get("action", "")).upper() in MANAGEMENT_ACTIONS]
    missing: List[str] = []
    if matching is None:
        missing.append("matching decision journal snapshot")
    chart = details.get("chart_intelligence") or (matching or {}).get("chart_state") or {}
    frames = chart.get("timeframes") or {}
    frame_5m = frames.get("5m") or {}
    quality = details.get("entry_quality_assessment") or {}
    plan = details.get("trade_plan") or {}
    risk = details.get("risk_assessment") or (matching or {}).get("risk_state")
    direction = context.get("direction") or details.get("direction")
    if direction is None:
        direction = {"BUY": "LONG", "SELL": "SHORT"}.get(str(entry_event.get("side") or "").upper())
    requested = {
        "timestamp": entry_ts, "direction": direction,
        "setup": context.get("setup_type") or details.get("setup_type"),
        "final_decision": (matching or {}).get("final_decision"),
        "rule_engine_reasons": quality.get("reason_codes") or context.get("reason_codes") or details.get("reason_codes"),
        "regime": details.get("regime") or context.get("regime"), "regime_score": details.get("regime_score"),
        "regime_confidence": details.get("regime_confidence") or context.get("confidence"),
        "volatility": details.get("volatility") or context.get("volatility"),
        "structure_4h": (frames.get("4h") or {}).get("structure"),
        "structure_1h": (frames.get("1h") or {}).get("structure"),
        "structure_15m": (frames.get("15m") or {}).get("structure"),
        "structure_5m": frame_5m.get("structure"),
        "support_distance": quality.get("distance_to_support_pct"),
        "resistance_distance": quality.get("distance_to_resistance_pct"),
        "atr_distance": quality.get("atr_extension_5m") or quality.get("atr_extension_15m"),
        "rsi": frame_5m.get("rsi"), "bollinger": frame_5m.get("bollinger"),
        "ema_trend": {"ema20": frame_5m.get("ema20"), "ema50": frame_5m.get("ema50"),
                      "ema200": frame_5m.get("ema200"), "trend": frame_5m.get("trend")},
        "momentum": frame_5m.get("momentum") or frame_5m.get("trend"),
        "volume_state": frame_5m.get("volume_state"), "rvol": frame_5m.get("relative_volume"),
        "bos": frame_5m.get("bos"), "choch": frame_5m.get("choch"),
        "overextension": frame_5m.get("overextension_atr"),
        "derivatives": details.get("derivatives") or (matching or {}).get("derivatives_state"),
        "funding": ((details.get("derivatives") or {}).get("funding_rate") or ((matching or {}).get("derivatives_state") or {}).get("funding_rate")),
        "open_interest": ((details.get("derivatives") or {}).get("open_interest") or ((matching or {}).get("derivatives_state") or {}).get("open_interest")),
        "long_short_ratio": ((details.get("derivatives") or {}).get("long_short_ratio") or ((matching or {}).get("derivatives_state") or {}).get("long_short_ratio")),
        "risk_assessment": risk, "entry": details.get("planned_entry") or plan.get("entry_price") or entry_event.get("price"),
        "initial_stop": plan.get("stop_loss") or details.get("planned_stop"),
        "tp1": plan.get("tp1") or details.get("tp1"), "tp2": plan.get("tp2") or details.get("tp2"),
        "risk_reward": plan.get("risk_reward") or details.get("risk_reward"),
    }
    for name, value in requested.items():
        if value is None:
            missing.append(name)
    management = []
    for event in sorted(after, key=lambda item: int(item.get("timestamp") or 0)):
        payload = event.get("details") or event.get("context") or {}
        management.append({
            "timestamp": event.get("timestamp"), "mark_price": event.get("price") or payload.get("mark"),
            "current_r": payload.get("current_r"), "mfe_r": payload.get("mfe_r"), "mae_r": payload.get("mae_r"),
            "structure": payload.get("structure"), "bos": payload.get("bos"), "choch": payload.get("choch"),
            "regime": payload.get("regime"), "momentum": payload.get("momentum"), "volume": payload.get("volume"),
            "position_manager_state": payload.get("state") or event.get("action"),
            "reason_codes": payload.get("reason_codes") or ([event.get("reason")] if event.get("reason") else []),
            "thesis_valid": payload.get("thesis_valid"), "action": event.get("action"),
        })
    return {"status": "INCOMPLETE" if missing else "AVAILABLE", "missing_fields": sorted(set(missing)),
            "entry": requested, "management_decisions": management}


def _markdown(audit: Mapping[str, Any]) -> str:
    lines = ["# Last Trade Forensic Audit", "", f"Status: **{audit['status']}**", ""]
    if audit.get("missing_fields"):
        lines += ["## Missing source fields", ""] + [f"- `{field}`" for field in audit["missing_fields"]] + [""]
    lines += ["## Entry evidence", "", "```json", json.dumps(audit.get("entry"), indent=2, ensure_ascii=False), "```", "",
              "## Closed 5M management decisions", "", "```json", json.dumps(audit.get("management_decisions"), indent=2, ensure_ascii=False), "```", ""]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--journal-dir", default="journal_logs")
    parser.add_argument("--output-dir", default="reports/ai")
    args = parser.parse_args()
    audit = build_audit(args.journal_dir)
    output = Path(args.output_dir)
    output.mkdir(parents=True, exist_ok=True)
    (output / "last-trade-forensic-audit.json").write_text(json.dumps(audit, indent=2, ensure_ascii=False), encoding="utf-8")
    (output / "last-trade-forensic-audit.md").write_text(_markdown(audit), encoding="utf-8")
    print(audit["status"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
