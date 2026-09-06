"""Offline-only future outcome labels for Entry AI V1."""

from __future__ import annotations

from typing import Any, Dict, Iterable, Mapping, Optional


LABEL_VERSION = "entry-ai-labels-v1"
HORIZONS = (12, 24, 48)


def _number(value: Any) -> Optional[float]:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def build_future_labels(candidate: Mapping[str, Any], future_candles: Iterable[Mapping[str, Any]]) -> Dict[str, Any]:
    """Label a candidate conservatively; same-bar stop/TP resolves against the trade."""
    direction = str(candidate.get("direction") or "").upper()
    entry = _number(candidate.get("planned_entry") or candidate.get("entry_price"))
    stop = _number(candidate.get("initial_stop") or candidate.get("stop_loss"))
    tp1 = _number(candidate.get("tp1"))
    if direction not in {"LONG", "SHORT"} or None in (entry, stop, tp1):
        raise ValueError("direction, planned entry, initial stop and TP1 are required")
    risk = abs(entry - stop)
    if risk <= 0:
        raise ValueError("initial risk must be positive")
    candles = [row for row in future_candles if row.get("is_closed", True)]
    result: Dict[str, Any] = {"label_version": LABEL_VERSION}

    def r(price: float) -> float:
        return (price - entry) / risk if direction == "LONG" else (entry - price) / risk

    for horizon in HORIZONS:
        window = candles[:horizon]
        highs = [_number(c.get("high")) for c in window]
        lows = [_number(c.get("low")) for c in window]
        closes = [_number(c.get("close")) for c in window]
        valid_highs, valid_lows, valid_closes = ([v for v in values if v is not None] for values in (highs, lows, closes))
        if not valid_highs or not valid_lows or not valid_closes:
            result[f"future_mfe_r_{horizon}"] = None
            result[f"future_mae_r_{horizon}"] = None
            result[f"future_close_r_{horizon}"] = None
        else:
            favorable = max(valid_highs) if direction == "LONG" else min(valid_lows)
            adverse = min(valid_lows) if direction == "LONG" else max(valid_highs)
            result[f"future_mfe_r_{horizon}"] = r(favorable)
            result[f"future_mae_r_{horizon}"] = r(adverse)
            result[f"future_close_r_{horizon}"] = r(valid_closes[-1])

    tp_bar = stop_bar = None
    for index, candle in enumerate(candles[:48], start=1):
        high, low = _number(candle.get("high")), _number(candle.get("low"))
        if high is None or low is None:
            continue
        tp_hit = high >= tp1 if direction == "LONG" else low <= tp1
        stop_hit = low <= stop if direction == "LONG" else high >= stop
        if tp_hit and stop_hit:
            # Existing conservative policy: ambiguity is a stop-first outcome.
            stop_bar = stop_bar or index
            break
        if stop_hit:
            stop_bar = index
            break
        if tp_hit:
            tp_bar = index
            break
    success = int(tp_bar is not None and tp_bar <= 24 and (stop_bar is None or tp_bar < stop_bar))
    result.update({
        "tp1_before_initial_stop_24": success,
        "initial_stop_before_tp1_24": int(stop_bar is not None and stop_bar <= 24),
        "bars_to_tp1": tp_bar,
        "bars_to_initial_stop": stop_bar,
        "ENTRY_SUCCESS_24": success,
        # Fixed V1 definition: realized close-at-24 R, capped at the first TP/SL barrier.
        "EXPECTED_R_24": 1.0 if success else -1.0 if stop_bar is not None and stop_bar <= 24 else result.get("future_close_r_24"),
    })
    return result
