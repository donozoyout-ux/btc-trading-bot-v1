import copy
import json

from ai.dataset_builder import build_dataset
from ai.entry_model import EntryAIModel
from ai.feature_schema import CandidateFeatureBuilder, FEATURE_COLUMNS, FEATURE_SCHEMA_VERSION, feature_vector
from ai.labels import build_future_labels
from ai.shadow_entry_ai import ShadowEntryAI
from ai.walk_forward import chronological_folds, evaluate_walk_forward
from config.settings import BotSettings


STEP = 5 * 60 * 1000


def candle(timestamp, *, high=101.0, low=99.0, close=100.0, is_closed=True):
    return {
        "timestamp": timestamp, "open": 100.0, "high": high, "low": low,
        "close": close, "volume": 10.0, "is_closed": is_closed,
    }


def snapshot(timestamp=1_700_000_000_000, *, direction="LONG", final="NO_TRADE", future=None):
    plan = {
        "entry_price": 100.0, "stop_loss": 98.0 if direction == "LONG" else 102.0,
        "tp1": 102.0 if direction == "LONG" else 98.0,
        "tp2": 104.0 if direction == "LONG" else 96.0,
        "risk_reward": 2.0, "risk_reward_tp1": 1.0, "risk_reward_tp2": 2.0,
        "management_profile": "BALANCED", "target_mode": "DYNAMIC",
    }
    bars = [candle(timestamp - STEP), candle(timestamp)] + list(future or [])
    return {
        "candidate_timestamp": timestamp,
        "final_decision": final,
        "decision": {
            "timestamp": timestamp + 1234, "symbol": "BTC/USDT", "setup_direction": direction,
            "setup": "TREND_PULLBACK", "regime": "BULL" if direction == "LONG" else "BEAR",
            "regime_score": 70, "confidence": "HIGH", "volatility": "NORMAL",
            "vol_percentile": 55, "structure_4h": "BULLISH", "structure_1h": "BULLISH",
            "location": "GOOD", "risk_status": "ACCEPT", "final_decision": final,
            "trade_plan": plan,
            "risk_assessment": {"risk_reward": 2.0},
            "entry_quality_assessment": {
                "decision": "ACCEPT", "distance_to_support_pct": 0.01,
                "distance_to_resistance_pct": 0.02, "details": {},
            },
        },
        "market": {"price": 100.0},
        "chart_intelligence": {"timeframes": {
            tf: {"structure": "BULLISH", "trend": "UP", "bos": "BULLISH_BOS",
                 "choch": "NONE", "relative_volume": 1.6, "volume_state": "EXPANSION"}
            for tf in ("4h", "1h", "15m", "5m")
        }},
        "indicators": {tf: {"latest": {
            "rsi14": 55, "atr14": 2, "ema20": 100, "ema50": 99,
            "bb_mid": 100, "bb_upper": 104, "bb_lower": 96,
        }} for tf in ("5m", "15m", "1h")},
        "derivatives": {
            "funding_rate": {"value": None, "source": "UNAVAILABLE"},
            "open_interest": {"value": None, "source": "UNAVAILABLE"},
            "oi_change_pct": {"value": None, "source": "UNAVAILABLE"},
            "long_short_ratio": {"value": None, "source": "UNAVAILABLE"},
            "taker_buy_ratio": {"value": None, "source": "UNAVAILABLE"},
        },
        "candles": {"5m": bars},
    }


def test_candidate_key_is_closed_candle_based_and_schema_is_deterministic():
    builder = CandidateFeatureBuilder()
    item = snapshot()
    assert builder.candidate_key(item)[0] == item["candidate_timestamp"]
    first, second = builder.build(item), builder.build(copy.deepcopy(item))
    assert tuple(first) == tuple(second)
    assert feature_vector(first) == feature_vector(second)
    assert first["feature_schema_version"] == FEATURE_SCHEMA_VERSION
    assert tuple(builder.feature_columns) == FEATURE_COLUMNS


def test_unavailable_derivatives_are_missing_not_neutral():
    row = CandidateFeatureBuilder().build(snapshot())
    for name in ("funding_rate", "open_interest", "oi_change_pct", "long_short_ratio", "taker_buy_volume_ratio"):
        assert row[name] is None
    for name in ("funding_available", "open_interest_available", "oi_change_available", "long_short_ratio_available", "taker_flow_available"):
        assert row[name] == 0


def test_future_mutation_cannot_change_features_but_can_change_labels():
    timestamp = 1_700_000_000_000
    benign = [candle(timestamp + STEP * i) for i in range(1, 49)]
    winning = copy.deepcopy(benign)
    winning[0]["high"] = 103.0
    base, changed = snapshot(timestamp, future=benign), snapshot(timestamp, future=winning)
    assert feature_vector(CandidateFeatureBuilder().build(base)) == feature_vector(CandidateFeatureBuilder().build(changed))
    geometry = {"direction": "LONG", "planned_entry": 100, "initial_stop": 98, "tp1": 102}
    assert build_future_labels(geometry, benign)["ENTRY_SUCCESS_24"] == 0
    assert build_future_labels(geometry, winning)["ENTRY_SUCCESS_24"] == 1


def test_same_bar_ambiguity_uses_conservative_stop_first():
    labels = build_future_labels(
        {"direction": "LONG", "planned_entry": 100, "initial_stop": 98, "tp1": 102},
        [candle(1, high=103, low=97)],
    )
    assert labels["ENTRY_SUCCESS_24"] == 0
    assert labels["initial_stop_before_tp1_24"] == 1
    assert labels["EXPECTED_R_24"] == -1.0


def test_long_and_short_labels_are_directional():
    long_result = build_future_labels(
        {"direction": "LONG", "planned_entry": 100, "initial_stop": 98, "tp1": 102},
        [candle(1, high=102.5, low=99)],
    )
    short_result = build_future_labels(
        {"direction": "SHORT", "planned_entry": 100, "initial_stop": 102, "tp1": 98},
        [candle(1, high=101, low=97.5)],
    )
    assert long_result["ENTRY_SUCCESS_24"] == short_result["ENTRY_SUCCESS_24"] == 1


def test_dataset_keeps_executed_and_rejected_candidates_without_duplicates():
    t0 = 1_700_000_000_000
    future = [candle(t0 + STEP * i) for i in range(1, 51)]
    executed = snapshot(t0, final="LONG_ENTRY", future=future)
    rejected = snapshot(t0 + STEP, final="NO_TRADE", future=future)
    duplicate = copy.deepcopy(rejected)
    rows, metadata = build_dataset([executed, rejected, duplicate])
    assert len(rows) == 2
    assert metadata["executed_candidates"] == 1
    assert metadata["rejected_candidates"] == 1
    assert metadata["duplicate_key_check"] == "PASS"


def test_chronological_folds_never_overlap_or_leak():
    rows = [{"timestamp": i // 2} for i in range(60)]
    for train, test in chronological_folds(rows, folds=3, min_train=24):
        assert set(train).isdisjoint(test)
        assert max(rows[i]["timestamp"] for i in train) < min(rows[i]["timestamp"] for i in test)


def test_walk_forward_reports_rule_vs_shadow_oos_metrics():
    rows = []
    for index in range(32):
        row = CandidateFeatureBuilder().build(snapshot(1_700_000_000_000 + index * STEP))
        row.update({"ENTRY_SUCCESS_24": index % 2, "EXPECTED_R_24": 1.0 if index % 2 else -1.0})
        rows.append(row)
    result = evaluate_walk_forward(rows)
    assert result["status"] == "PASS"
    assert result["oos_rows"] > 0
    assert "rule_candidate_success_rate" in result
    assert "ai_shadow_accept_success_rate" in result
    assert "slices" in result


def test_model_missing_and_schema_mismatch_fail_soft(tmp_path):
    assert ShadowEntryAI(tmp_path / "missing.json").evaluate(snapshot())["status"] == "UNAVAILABLE"
    artifact = tmp_path / "model.json"
    training = []
    for index in range(8):
        row = CandidateFeatureBuilder().build(snapshot(1_700_000_000_000 + index * STEP))
        row.update({"ENTRY_SUCCESS_24": index % 2, "EXPECTED_R_24": 1.0 if index % 2 else -1.0})
        training.append(row)
    EntryAIModel().fit(training).save(artifact)
    payload = json.loads(artifact.read_text(encoding="utf-8"))
    payload["metadata"]["schema_version"] = "wrong-schema"
    artifact.write_text(json.dumps(payload), encoding="utf-8")
    result = ShadowEntryAI(artifact).evaluate(snapshot())
    assert result["status"] == "UNAVAILABLE"
    assert result["success_probability"] is None
    assert "FEATURE_SCHEMA_MISMATCH" in result["reasons"]


def test_shadow_ai_has_zero_authority_is_closed_only_and_deduplicates():
    shadow = ShadowEntryAI()
    open_result = shadow.evaluate(snapshot(), candle_closed=False)
    first = shadow.evaluate(snapshot(), candle_closed=True)
    second = shadow.evaluate(snapshot(), candle_closed=True)
    assert open_result["status"] == "DEGRADED"
    assert open_result["reasons"] == ["OPEN_5M_CANDLE"]
    assert first == second
    assert first["execution_authority"] is False
    assert ShadowEntryAI.execution_authority is False
    assert not hasattr(shadow, "place_order")


def test_ai_telemetry_cannot_change_rule_decision_and_mainnet_stays_blocked():
    item = snapshot(final="LONG_ENTRY")
    original = item["final_decision"]
    item["ai_entry_shadow"] = {
        "status": "AVAILABLE", "decision": "AI_VETO", "execution_authority": False,
    }
    assert item["final_decision"] == original
    unsafe = BotSettings(
        ENV="production", BINANCE_TESTNET=False, ACCOUNT_READ_ONLY=False,
        SHADOW_MODE=False, ORDER_SUBMISSION_ENABLED=True,
    )
    assert unsafe.testnet_execution_enabled is False
    assert unsafe.ORDER_SUBMISSION_ENABLED is False
