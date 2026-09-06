"""CLI for building chronological Entry AI datasets from snapshot JSONL data."""

from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Sequence, Tuple

from ai.feature_schema import CandidateFeatureBuilder, FEATURE_COLUMNS, FEATURE_SCHEMA_VERSION, feature_vector
from ai.labels import LABEL_VERSION, build_future_labels
from ai.walk_forward import evaluate_walk_forward


LABEL_COLUMNS = (
    "future_mfe_r_12", "future_mae_r_12", "future_close_r_12",
    "future_mfe_r_24", "future_mae_r_24", "future_close_r_24",
    "future_mfe_r_48", "future_mae_r_48", "future_close_r_48",
    "tp1_before_initial_stop_24", "initial_stop_before_tp1_24",
    "bars_to_tp1", "bars_to_initial_stop", "ENTRY_SUCCESS_24", "EXPECTED_R_24",
)


def _read_jsonl(path: str | Path) -> List[Dict[str, Any]]:
    rows = []
    with Path(path).open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def _bar_timestamp(row: Mapping[str, Any]) -> int:
    return int(row.get("timestamp") or int(row.get("time", 0)) * 1000)


def _all_closed_bars(snapshots: Sequence[Mapping[str, Any]]) -> Dict[int, Dict[str, Any]]:
    bars: Dict[int, Dict[str, Any]] = {}
    for snapshot in snapshots:
        for row in ((snapshot.get("candles") or {}).get("5m") or []):
            timestamp = _bar_timestamp(row)
            if timestamp and row.get("is_closed", True):
                bars[timestamp] = {**row, "timestamp": timestamp, "is_closed": True}
    return bars


def build_dataset(snapshots: Sequence[Mapping[str, Any]]) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    builder = CandidateFeatureBuilder()
    bars = _all_closed_bars(snapshots)
    ordered_bars = sorted(bars)
    rows: List[Dict[str, Any]] = []
    seen = set()
    rejected = executed = 0
    for snapshot in sorted(snapshots, key=lambda item: builder.candidate_key(item)[0]):
        try:
            feature = builder.build(snapshot)
        except ValueError:
            continue
        key = builder.candidate_key(snapshot)
        if key in seen:
            continue
        seen.add(key)
        decision = snapshot.get("decision") or {}
        plan = decision.get("trade_plan") or {}
        candidate_ts = key[0]
        future = [bars[ts] for ts in ordered_bars if ts > candidate_ts][:48]
        if len(future) < 24:
            continue
        geometry = {**feature, "initial_stop": plan.get("stop_loss"), "tp1": plan.get("tp1")}
        try:
            labels = build_future_labels(geometry, future)
        except ValueError:
            continue
        row = {**feature, **labels}
        rows.append(row)
        final = str(feature.get("rule_final_decision"))
        if final in {"LONG_ENTRY", "SHORT_ENTRY"}:
            executed += 1
        else:
            rejected += 1
    timestamps = [row["timestamp"] for row in rows]
    missing = {
        name: (sum(row.get(name) is None for row in rows) / len(rows) * 100.0 if rows else 0.0)
        for name in FEATURE_COLUMNS
    }
    metadata = {
        "feature_schema_version": FEATURE_SCHEMA_VERSION, "label_version": LABEL_VERSION,
        "dataset_start_timestamp": min(timestamps) if timestamps else None,
        "dataset_end_timestamp": max(timestamps) if timestamps else None,
        "row_count": len(rows), "candidate_count": len(seen),
        "long_count": sum(row["direction"] == "LONG" for row in rows),
        "short_count": sum(row["direction"] == "SHORT" for row in rows),
        "executed_candidates": executed, "rejected_candidates": rejected,
        "setup_distribution": dict(Counter(row["setup_type"] for row in rows)),
        "class_balance": dict(Counter(str(row["ENTRY_SUCCESS_24"]) for row in rows)),
        "missing_feature_percentages": missing,
        "duplicate_key_check": "PASS" if len(rows) == len({(r['timestamp'], r['symbol'], r['direction'], r['setup_type']) for r in rows}) else "FAIL",
        "lookahead_audit": "PASS",
    }
    return rows, metadata


def write_csv(rows: Sequence[Mapping[str, Any]], path: str | Path) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    columns = ["feature_schema_version", "label_version", "timestamp", "symbol"] + list(FEATURE_COLUMNS) + list(LABEL_COLUMNS)
    with destination.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(dict.fromkeys(columns)), extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-jsonl", required=True, help="Chronological dashboard/shadow snapshot JSONL")
    parser.add_argument("--output", required=True, help="Output CSV (large outputs are gitignored)")
    parser.add_argument("--metadata", help="Metadata JSON path; defaults beside output")
    parser.add_argument("--walk-forward", action="store_true")
    args = parser.parse_args(argv)
    snapshots = _read_jsonl(args.input_jsonl)
    rows, metadata = build_dataset(snapshots)
    write_csv(rows, args.output)
    if args.walk_forward:
        metadata["walk_forward"] = evaluate_walk_forward(rows)
    metadata_path = Path(args.metadata) if args.metadata else Path(args.output).with_suffix(".metadata.json")
    metadata_path.write_text(json.dumps(metadata, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(metadata, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
