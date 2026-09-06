"""Versioned, zero-lookahead feature extraction for closed 5M candidates."""

from __future__ import annotations

import math
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

import numpy as np


FEATURE_SCHEMA_VERSION = "entry-ai-v1"

CATEGORICAL_FEATURES = (
    "direction", "setup_type", "regime", "regime_confidence", "volatility",
    "structure_4h", "structure_1h", "structure_15m", "structure_5m",
    "location_quality", "momentum_direction", "volume_state", "candle_direction",
    "funding_class", "crowding", "management_profile", "target_mode",
    "rule_final_decision", "rule_risk_decision", "rule_entry_quality_decision",
)

NUMERIC_FEATURES = (
    "regime_score", "volatility_percentile", "overextended_up", "overextended_down",
    "bullish_bos_4h", "bearish_bos_4h", "bullish_bos_1h", "bearish_bos_1h",
    "bullish_bos_15m", "bearish_bos_15m", "bullish_bos_5m", "bearish_bos_5m",
    "bullish_choch", "bearish_choch", "support_distance_pct", "resistance_distance_pct",
    "support_distance_atr", "resistance_distance_atr", "nearest_support_strength",
    "nearest_resistance_strength", "rsi_5m", "rsi_15m", "rsi_1h",
    "price_vs_ema20_atr", "ema20_vs_ema50_atr", "ema50_slope_atr", "momentum_strength",
    "bb_position", "bb_width_atr", "distance_upper_band_atr", "distance_lower_band_atr",
    "atr", "atr_pct", "rolling_atr_percentile", "rvol", "volume_expansion",
    "volume_contraction", "body_ratio", "upper_wick_ratio", "lower_wick_ratio",
    "funding_rate", "open_interest", "oi_change_pct", "long_short_ratio",
    "taker_buy_volume_ratio", "funding_available", "open_interest_available",
    "oi_change_available", "long_short_ratio_available", "taker_flow_available",
    "planned_entry", "stop_distance_atr", "stop_distance_pct", "tp1_r", "tp2_r",
    "risk_reward",
)

FEATURE_COLUMNS = CATEGORICAL_FEATURES + NUMERIC_FEATURES


def _value(value: Any, default: Any = None) -> Any:
    if hasattr(value, "value"):
        return value.value
    return default if value is None else value


def _finite(value: Any) -> Optional[float]:
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (TypeError, ValueError):
        return None


def _latest_indicator(payload: Mapping[str, Any], timeframe: str, name: str) -> Optional[float]:
    return _finite(((payload.get(timeframe) or {}).get("latest") or {}).get(name))


def _field(payload: Mapping[str, Any], name: str) -> Tuple[Optional[float], int]:
    raw = payload.get(name)
    if isinstance(raw, Mapping):
        value = _finite(raw.get("value"))
        source = str(_value(raw.get("source"), "UNAVAILABLE")).upper()
        stale = bool(raw.get("is_stale"))
        return (value, 1) if value is not None and source != "UNAVAILABLE" and not stale else (None, 0)
    value = _finite(raw)
    return (value, int(value is not None))


def _frame(chart: Mapping[str, Any], timeframe: str) -> Mapping[str, Any]:
    return ((chart.get("timeframes") or {}).get(timeframe) or {})


def _flag(text: Any, token: str) -> int:
    return int(token in str(_value(text, "")).upper())


def _closed_candles(snapshot: Mapping[str, Any], timestamp_ms: int) -> List[Mapping[str, Any]]:
    rows = list(((snapshot.get("candles") or {}).get("5m") or []))
    closed: List[Mapping[str, Any]] = []
    for row in rows:
        raw_ts = int(row.get("timestamp") or int(row.get("time", 0)) * 1000)
        if raw_ts <= timestamp_ms and row.get("is_closed", True):
            closed.append(row)
    return sorted(closed, key=lambda item: int(item.get("timestamp") or int(item.get("time", 0)) * 1000))


class CandidateFeatureBuilder:
    """Build a stable feature vector from data observable at candidate time T."""

    schema_version = FEATURE_SCHEMA_VERSION
    feature_columns = FEATURE_COLUMNS

    @staticmethod
    def candidate_key(snapshot: Mapping[str, Any]) -> Tuple[int, str, str, str]:
        decision = snapshot.get("decision") or {}
        # Runtime identity is pinned to a closed 5M candle, not HTTP poll time.
        timestamp = int(snapshot.get("candidate_timestamp") or decision.get("timestamp") or snapshot.get("timestamp") or 0)
        symbol = str(decision.get("symbol") or snapshot.get("symbol") or "BTCUSDT").replace("/", "")
        direction = str(_value(decision.get("setup_direction") or snapshot.get("direction"), "WAIT"))
        setup = str(_value(decision.get("setup") or snapshot.get("setup_type"), "NONE"))
        return timestamp, symbol, direction, setup

    def build(self, snapshot: Mapping[str, Any]) -> Dict[str, Any]:
        decision = snapshot.get("decision") or {}
        timestamp, symbol, direction, setup = self.candidate_key(snapshot)
        if timestamp <= 0:
            raise ValueError("candidate timestamp is required")
        if direction not in {"LONG", "SHORT"} or setup == "NONE":
            raise ValueError("directional setup candidate is required")

        chart = snapshot.get("chart_intelligence") or {}
        indicators = snapshot.get("indicators") or {}
        market = snapshot.get("market") or {}
        derivatives = snapshot.get("derivatives") or {}
        plan = decision.get("trade_plan") or snapshot.get("trade_plan") or {}
        quality = decision.get("entry_quality_assessment") or {}
        risk = decision.get("risk_assessment") or {}
        candles = _closed_candles(snapshot, timestamp)
        candle = candles[-1] if candles else {}
        price = _finite(market.get("price") or decision.get("price") or candle.get("close"))
        atr = _latest_indicator(indicators, "5m", "atr14") or _finite(decision.get("current_atr"))
        ema20 = _latest_indicator(indicators, "5m", "ema20")
        ema50 = _latest_indicator(indicators, "5m", "ema50")
        bb_mid = _latest_indicator(indicators, "5m", "bb_mid")
        bb_upper = _latest_indicator(indicators, "5m", "bb_upper")
        bb_lower = _latest_indicator(indicators, "5m", "bb_lower")
        f4, f1, f15, f5 = (_frame(chart, tf) for tf in ("4h", "1h", "15m", "5m"))
        support_pct = _finite(quality.get("distance_to_support_pct"))
        resistance_pct = _finite(quality.get("distance_to_resistance_pct"))
        funding, funding_ok = _field(derivatives, "funding_rate")
        oi, oi_ok = _field(derivatives, "open_interest")
        oi_change, oi_change_ok = _field(derivatives, "oi_change_pct")
        ls_ratio, ls_ok = _field(derivatives, "long_short_ratio")
        taker, taker_ok = _field(derivatives, "taker_buy_ratio")
        open_v, high_v, low_v, close_v = (_finite(candle.get(k)) for k in ("open", "high", "low", "close"))
        total_range = (high_v - low_v) if None not in (high_v, low_v) and high_v > low_v else None
        rvol = _finite(f5.get("relative_volume"))
        bb_width = (bb_upper - bb_lower) if None not in (bb_upper, bb_lower) else None
        bb_position = ((price - bb_lower) / bb_width) if price is not None and bb_width and bb_width > 0 else None
        entry = _finite(plan.get("entry_price"))
        stop = _finite(plan.get("stop_loss"))
        stop_distance = abs(entry - stop) if None not in (entry, stop) else None

        row: Dict[str, Any] = {
            "feature_schema_version": FEATURE_SCHEMA_VERSION,
            "timestamp": timestamp, "symbol": symbol, "direction": direction, "setup_type": setup,
            "regime": str(_value(decision.get("regime"), "UNAVAILABLE")),
            "regime_score": _finite(decision.get("regime_score")),
            "regime_confidence": str(_value(decision.get("confidence"), "UNAVAILABLE")),
            "volatility": str(_value(decision.get("volatility"), "UNAVAILABLE")),
            "volatility_percentile": _finite(decision.get("vol_percentile")),
            "overextended_up": int(bool(decision.get("overextended_up"))),
            "overextended_down": int(bool(decision.get("overextended_down"))),
            "structure_4h": str(_value(f4.get("structure") or decision.get("structure_4h"), "UNAVAILABLE")),
            "structure_1h": str(_value(f1.get("structure") or decision.get("structure_1h"), "UNAVAILABLE")),
            "structure_15m": str(_value(f15.get("structure"), "UNAVAILABLE")),
            "structure_5m": str(_value(f5.get("structure"), "UNAVAILABLE")),
            "bullish_bos_4h": _flag(f4.get("bos"), "BULLISH"), "bearish_bos_4h": _flag(f4.get("bos"), "BEARISH"),
            "bullish_bos_1h": _flag(f1.get("bos"), "BULLISH"), "bearish_bos_1h": _flag(f1.get("bos"), "BEARISH"),
            "bullish_bos_15m": _flag(f15.get("bos"), "BULLISH"), "bearish_bos_15m": _flag(f15.get("bos"), "BEARISH"),
            "bullish_bos_5m": _flag(f5.get("bos"), "BULLISH"), "bearish_bos_5m": _flag(f5.get("bos"), "BEARISH"),
            "bullish_choch": _flag(f5.get("choch"), "BULLISH"), "bearish_choch": _flag(f5.get("choch"), "BEARISH"),
            "support_distance_pct": support_pct, "resistance_distance_pct": resistance_pct,
            "support_distance_atr": (support_pct * price / atr) if None not in (support_pct, price, atr) and atr else None,
            "resistance_distance_atr": (resistance_pct * price / atr) if None not in (resistance_pct, price, atr) and atr else None,
            "nearest_support_strength": _finite(quality.get("details", {}).get("nearest_support_strength")),
            "nearest_resistance_strength": _finite(quality.get("details", {}).get("nearest_resistance_strength")),
            "location_quality": str(_value(decision.get("location"), "UNAVAILABLE")),
            "rsi_5m": _latest_indicator(indicators, "5m", "rsi14"), "rsi_15m": _latest_indicator(indicators, "15m", "rsi14"),
            "rsi_1h": _latest_indicator(indicators, "1h", "rsi14"),
            "price_vs_ema20_atr": ((price - ema20) / atr) if None not in (price, ema20, atr) and atr else None,
            "ema20_vs_ema50_atr": ((ema20 - ema50) / atr) if None not in (ema20, ema50, atr) and atr else None,
            "ema50_slope_atr": _finite(f5.get("ema50_slope_atr")),
            "momentum_direction": str(_value(f5.get("trend"), "UNAVAILABLE")),
            "momentum_strength": _finite(f5.get("momentum_strength")),
            "bb_position": bb_position, "bb_width_atr": (bb_width / atr) if bb_width is not None and atr else None,
            "distance_upper_band_atr": ((bb_upper - price) / atr) if None not in (bb_upper, price, atr) and atr else None,
            "distance_lower_band_atr": ((price - bb_lower) / atr) if None not in (bb_lower, price, atr) and atr else None,
            "atr": atr, "atr_pct": (atr / price) if atr is not None and price else None,
            "rolling_atr_percentile": _finite(decision.get("vol_percentile")),
            "volume_state": str(_value(f5.get("volume_state"), "UNAVAILABLE")), "rvol": rvol,
            "volume_expansion": int(rvol is not None and rvol >= 1.5),
            "volume_contraction": int(rvol is not None and rvol <= (1.0 / 1.5)),
            "body_ratio": (abs(close_v - open_v) / total_range) if None not in (close_v, open_v, total_range) and total_range else None,
            "upper_wick_ratio": ((high_v - max(open_v, close_v)) / total_range) if None not in (high_v, open_v, close_v, total_range) and total_range else None,
            "lower_wick_ratio": ((min(open_v, close_v) - low_v) / total_range) if None not in (low_v, open_v, close_v, total_range) and total_range else None,
            "candle_direction": "BULLISH" if close_v is not None and open_v is not None and close_v > open_v else "BEARISH" if close_v is not None and open_v is not None and close_v < open_v else "DOJI",
            "funding_rate": funding, "funding_class": str(_value(derivatives.get("funding_class"), "UNAVAILABLE")),
            "open_interest": oi, "oi_change_pct": oi_change, "long_short_ratio": ls_ratio,
            "crowding": str(_value(derivatives.get("crowding"), "UNAVAILABLE")), "taker_buy_volume_ratio": taker,
            "funding_available": funding_ok, "open_interest_available": oi_ok, "oi_change_available": oi_change_ok,
            "long_short_ratio_available": ls_ok, "taker_flow_available": taker_ok,
            "planned_entry": entry,
            "stop_distance_atr": (stop_distance / atr) if stop_distance is not None and atr else None,
            "stop_distance_pct": (stop_distance / entry) if stop_distance is not None and entry else None,
            "tp1_r": _finite(plan.get("risk_reward_tp1")), "tp2_r": _finite(plan.get("risk_reward_tp2")),
            "risk_reward": _finite(plan.get("risk_reward") or risk.get("risk_reward")),
            "management_profile": str(_value(plan.get("management_profile"), "UNAVAILABLE")),
            "target_mode": str(_value(plan.get("target_mode"), "UNAVAILABLE")),
            "rule_final_decision": str(_value(decision.get("final_decision") or snapshot.get("final_decision"), "UNAVAILABLE")),
            "rule_risk_decision": str(_value(decision.get("risk_status"), "UNAVAILABLE")),
            "rule_entry_quality_decision": str(_value(quality.get("decision"), "UNAVAILABLE")),
        }
        return row


def feature_vector(row: Mapping[str, Any]) -> Tuple[Any, ...]:
    """Stable equality-friendly representation used by zero-lookahead audits."""
    return tuple(row.get(column) for column in FEATURE_COLUMNS)
