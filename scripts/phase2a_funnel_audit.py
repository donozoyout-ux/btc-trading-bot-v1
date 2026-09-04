"""Phase 2A funnel audit: join opened trades (trade_trace evaluation_ids) to
journaled DecisionReports and classify exactly which funnel gate each
trade-opening evaluation would hit under current funnel rules.

No trading behavior involved: read-only analysis of v4 run artifacts.
Outputs reports/phase2-funnel-reconciliation.json (preliminary; counts part
is refreshed after the funnel-semantics fix + final instrumented rerun).
"""
import json
import re
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
JOURNAL = ROOT / "journal_logs" / "decisions.jsonl"
SUMMARY = ROOT / "reports" / "historical-backtest-phase1-summary.json"

EV_FIRST = b'"evaluation_id":"EV-00000001"'
EV_ANY = re.compile(r'"evaluation_id":"(EV-\d+)"')


def find_last_run_start(path: Path) -> int:
    """Byte offset of the last EV-00000001 line (start of latest logged run)."""
    last = -1
    with open(path, "rb") as f:
        chunk_size = 1 << 20
        overlap = len(EV_FIRST) + 64
        pos = 0
        buf = b""
        # Two-pass: first collect all match offsets in one streaming pass.
        offsets = []
        while True:
            chunk = f.read(chunk_size)
            if not chunk:
                break
            buf = buf + chunk
            start = 0
            while True:
                i = buf.find(EV_FIRST, start)
                if i < 0:
                    break
                offsets.append(pos + i)
                start = i + 1
            pos += len(chunk)
            buf = buf[-overlap:]
    if not offsets:
        raise RuntimeError("no EV-00000001 found in journal")
    # Map match offset -> line start by scanning back to previous newline.
    with open(path, "rb") as f:
        m = offsets[-1]
        back = min(m, 4096)
        f.seek(m - back)
        tail = f.read(back)
        nl = tail.rfind(b"\n")
        line_start = m - back + nl + 1
    print(f"journal matches for EV-00000001: {len(offsets)}; using last at byte {line_start}")
    return line_start


def load_run_reports(path: Path, start: int):
    """Parse journal lines from `start`; return {ev_id: minimal report dict}."""
    reports = {}
    order = []
    with open(path, "rb") as f:
        f.seek(start)
        for raw in f:
            if b'"evaluation_id":"EV-' not in raw:
                continue
            try:
                d = json.loads(raw.decode("utf-8"))
            except Exception:
                continue
            ev = d.get("evaluation_id", "")
            if not ev.startswith("EV-"):
                continue
            ra = d.get("risk_assessment") or {}
            reports[ev] = {
                "evaluation_id": ev,
                "timestamp": d.get("timestamp"),
                "reason": d.get("reason", ""),
                "structure_4h": d.get("structure_4h", ""),
                "structure_1h": d.get("structure_1h", ""),
                "location": d.get("location", ""),
                "setup": d.get("setup", ""),
                "trigger_state": d.get("trigger_state", ""),
                "derivatives": d.get("derivatives", ""),
                "final_decision": d.get("final_decision", ""),
                "kill_switch_active": bool(d.get("kill_switch_active", False)),
                "risk_decision": (ra.get("decision", "") if isinstance(ra, dict) else ""),
                "risk_reason_code": (ra.get("reason_code", "") if isinstance(ra, dict) else ""),
                "risk_rejection_reason": (ra.get("rejection_reason", "") if isinstance(ra, dict) else ""),
                "has_risk": ra is not None and isinstance(ra, dict) and bool(ra.get("decision")),
            }
            order.append(ev)
    return reports, order


def funnel_gate(rep):
    """First funnel gate an evaluation fails under CURRENT runner rules.

    Returns (passed_all, gate_or_EXECUTED).
    Mirrors _record_funnel_from_report with ks fixed rule approximated by
    kill_switch_active (pre-cycle snapshot unavailable in journal; a cycle
    counted KILL-blocked historically also has kill_switch_active True at
    decision time EXCEPT the 113 reset-released ones, handled separately).
    """
    if "DATA UNSAFE" in (rep["reason"] or ""):
        return False, "DATA_HEALTH_PASS"
    # KILL_SWITCH_PASS (current code): ks_latched_before AND kill_switch_active.
    # Journal lacks ks_latched_before; use kill_switch_active as proxy and flag it.
    if rep["kill_switch_active"] and rep["final_decision"] == "NO_TRADE" \
            and str(rep["reason"]).startswith("Kill Switch Activated"):
        return False, "KILL_SWITCH_PASS(proxy)"
    if rep["structure_4h"] == "MIXED" and rep["structure_1h"] == "MIXED":
        return False, "STRUCTURE_ELIGIBLE"
    if rep["location"] in ("BAD_LOCATION", "NEUTRAL"):
        return False, "GOOD_TRADE_LOCATION"
    if rep["setup"] == "NONE":
        return False, "SETUP_DETECTED"
    if rep["trigger_state"] != "ENTRY_READY":
        return False, "ENTRY_TRIGGER_DETECTED"
    if rep["derivatives"] == "REJECT":
        return False, "DERIVATIVES_ACCEPTABLE"
    if not rep["has_risk"]:
        return False, "TRADE_PLAN_CREATED(no-risk)"
    if rep["risk_decision"] != "ACCEPT_TRADE":
        return False, f"RISK_PASS({rep['risk_reason_code']})"
    if rep["final_decision"] not in ("LONG_ENTRY", "SHORT_ENTRY"):
        return False, "EXECUTABLE_CANDIDATES"
    return True, "EXECUTED"


def main():
    start = find_last_run_start(JOURNAL)
    reports, order = load_run_reports(JOURNAL, start)
    print(f"parsed {len(reports)} journaled evaluations from latest run")

    summary = json.loads(SUMMARY.read_text(encoding="utf-8"))
    trace = summary.get("trade_trace", {})
    print(f"trade_trace entries: {len(trace)}")

    gate_hist = Counter()
    missing = []
    gate_examples = {}
    for trade_id, t in trace.items():
        ev = t["evaluation_id"]
        rep = reports.get(ev)
        if rep is None:
            missing.append(ev)
            continue
        ok, gate = funnel_gate(rep)
        gate_hist[("EXECUTED-clean" if ok else gate)] += 1
        if not ok and gate not in gate_examples:
            gate_examples[gate] = {
                "evaluation_id": ev, "trade_id": trade_id, "report": rep,
            }

    print("=== trade-opening evaluations by funnel gate outcome ===")
    for gate, n in gate_hist.most_common():
        print(f"  {gate}: {n}")
    print(f"missing journal reports for {len(missing)} trade evaluations")

    # Sanity: contiguity of EV sequence in parsed run
    nums = sorted(int(e.split("-")[1]) for e in reports)
    contiguous = (nums == list(range(1, len(nums) + 1)))
    print(f"EV sequence 1..{len(nums)} contiguous: {contiguous}")

    out = {
        "method": (
            "Joined trade_trace evaluation_ids (v4 run) to journaled DecisionReports "
            "(latest EV-00000001-anchored journal block). Classified each trade-opening "
            "evaluation by the first funnel gate it would hit under current rules."
        ),
        "journal_evaluations_parsed": len(reports),
        "ev_sequence_contiguous": contiguous,
        "trade_evaluations_missing_journal": len(missing),
        "trade_opening_evaluations_by_gate": dict(gate_hist),
        "examples_per_divergent_gate": gate_examples,
    }
    out_path = ROOT / "reports" / "phase2-funnel-audit-raw.json"
    out_path.write_text(json.dumps(out, indent=2, default=str), encoding="utf-8")
    print(f"wrote {out_path}")


if __name__ == "__main__":
    main()
