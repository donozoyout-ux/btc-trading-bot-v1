import json
from datetime import datetime, timedelta, timezone

import pytest
import requests

from dashboard_server import DashboardRuntime
from integrations.ai_analyst import AIAnalyst, AIAnalystError
from integrations.news_engine import NewsEngine
from journal.ai_shadow_journal import AIShadowJournal


def _ai_payload(**overrides):
    payload = {
        "market_bias": "BEARISH",
        "setup_quality": 72,
        "trade_opinion": "WAIT",
        "best_setup": "TREND_PULLBACK SHORT",
        "market_view": "Bearish macro, mixed micro structure.",
        "confirmations": ["4H trend"],
        "conflicts": ["5M bounce"],
        "risk_notes": ["Support nearby"],
        "news_summary": "Caution",
        "derivatives_summary": "Funding neutral",
        "timeframe_alignment": {"4h": "bearish", "1h": "bearish", "15m": "mixed", "5m": "bounce"},
        "trade_location_assessment": "Too close to support",
        "invalidation_watch": ["1H CHoCH"],
        "decision_explanation": "Wait for confirmation.",
        "confidence": 81,
        "execution_authority": True,
    }
    payload.update(overrides)
    return payload


class _Response:
    def __init__(self, status=200, payload=None):
        self.status_code = status
        self._payload = payload if payload is not None else {"output_text": json.dumps(_ai_payload())}

    def json(self):
        return self._payload


def _eligible_context(candle=1000):
    return {
        "closed_5m_candle_timestamp": candle,
        "shadow_contract": {
            "deterministic_final_decision": "SHORT_ENTRY",
            "setup_eligible": True,
            "hard_blockers": [],
            "risk_authorized": True,
            "evidence_gate_authorized": True,
        },
    }


@pytest.mark.parametrize("status,category", [(401, "GROQ_AUTH_ERROR"), (429, "GROQ_RATE_LIMIT"), (500, "GROQ_API_ERROR")])
def test_groq_safe_http_error_categories(monkeypatch, status, category):
    monkeypatch.setattr(requests, "post", lambda *args, **kwargs: _Response(status=status))
    with pytest.raises(AIAnalystError, match=category):
        AIAnalyst("not-a-real-secret", enabled=True).analyze(_eligible_context())


def test_groq_invalid_json_is_safe(monkeypatch):
    monkeypatch.setattr(requests, "post", lambda *args, **kwargs: _Response(payload={"output_text": "not-json"}))
    with pytest.raises(AIAnalystError, match="AI_RESPONSE_INVALID"):
        AIAnalyst("not-a-real-secret", enabled=True).analyze(_eligible_context())


def test_ai_schema_and_application_guard_force_shadow_only(monkeypatch):
    monkeypatch.setattr(requests, "post", lambda *args, **kwargs: _Response(payload={"output_text": json.dumps(_ai_payload(trade_opinion="TAKE", setup_quality=125))}))
    ai = AIAnalyst("not-a-real-secret", enabled=True)
    result = ai.analyze({
        "shadow_contract": {
            "deterministic_final_decision": "NO_TRADE",
            "setup_eligible": False,
            "hard_blockers": ["NO_DETERMINISTIC_SETUP"],
            "risk_authorized": False,
        }
    })
    assert result["trade_opinion"] == "WAIT"
    assert result["setup_quality"] == 100
    assert result["execution_authority"] is False
    assert set(result["timeframe_alignment"]) == {"4h", "1h", "15m", "5m"}
    assert AIAnalyst.SCHEMA["properties"]["execution_authority"]["enum"] == [False]


def test_ai_calls_provider_once_per_closed_5m_candle(monkeypatch):
    calls = []
    monkeypatch.setattr(requests, "post", lambda *args, **kwargs: calls.append(kwargs) or _Response())
    ai = AIAnalyst("not-a-real-secret", enabled=True)
    first = ai.analyze(_eligible_context(1000))
    second = ai.analyze(_eligible_context(1000))
    ai.analyze(_eligible_context(2000))
    assert first == second
    assert len(calls) == 2
    assert all("not-a-real-secret" not in str(ai.safe_status()) for _ in [0])


def test_ai_network_failure_does_not_break_dashboard_snapshot_path(monkeypatch):
    ai = AIAnalyst("not-a-real-secret", enabled=True)
    monkeypatch.setattr(requests, "post", lambda *args, **kwargs: (_ for _ in ()).throw(requests.ConnectionError()))
    runtime = DashboardRuntime.__new__(DashboardRuntime)
    runtime.ai_analyst = ai
    result = runtime.analyze_ai({})
    assert result["status"] == "UNAVAILABLE"
    assert result["error_category"] == "GROQ_NETWORK_ERROR"
    assert result["execution_authority"] is False


def _news_row(title, age, category="HACK", risk=100, impact=95, sentiment=-50):
    return {
        "source": "www.coindesk.com", "title": title,
        "title_key": NewsEngine._title_key(title), "published_at": datetime.now(timezone.utc).isoformat(),
        "age_hours": age, "category": category,
        "sentiment": "BEARISH" if sentiment < 0 else "BULLISH", "sentiment_score": sentiment,
        "importance": "HIGH", "btc_relevance": "HIGH", "impact_score": impact,
        "risk_score": risk, "url": "https://example.invalid/story",
    }


def test_news_v4_near_duplicate_suppression_and_clustering(monkeypatch):
    engine = NewsEngine(["https://one.invalid/rss", "https://two.invalid/rss"])
    rows = [
        _news_row("Bitcoin exchange hack triggers major liquidation wave", 0.5),
        _news_row("Major Bitcoin exchange hack triggers liquidation wave", 0.6),
    ]
    monkeypatch.setattr(engine, "_fetch", lambda url, now: rows)
    result = engine.evaluate(force=True)
    assert len(result["items"]) == 1
    assert result["event_clusters"][0]["category"] == "HACK"
    assert result["trade_risk"] == "BLOCK"


def test_news_v4_source_degradation_and_old_event_decay(monkeypatch):
    engine = NewsEngine(["https://good.invalid/rss", "https://bad.invalid/rss"])
    old = _news_row("Bitcoin exchange hack reported yesterday", 18, risk=70, impact=45)

    def fetch(url, now):
        if "bad" in url:
            raise requests.ConnectionError()
        return [old]

    monkeypatch.setattr(engine, "_fetch", fetch)
    result = engine.evaluate(force=True)
    assert result["status"] == "DEGRADED"
    assert result["trade_risk"] != "BLOCK"
    assert {row["status"] for row in result["sources"]} == {"AVAILABLE", "UNAVAILABLE"}


def test_news_v4_recency_decay_and_specific_categories():
    assert NewsEngine._recency_weight(0.5) > NewsEngine._recency_weight(18)
    assert NewsEngine._category("Spot Bitcoin ETF inflow accelerates") == "ETF_FLOW"
    assert NewsEngine._category("Major stablecoin depeg hits crypto") == "STABLECOIN"


def test_ai_shadow_journal_persists_links_and_reports(tmp_path):
    journal = AIShadowJournal(str(tmp_path))
    snapshot = {
        "meta": {"generated_at": 1_000_000}, "final_decision": "LONG_ENTRY",
        "decision": {"regime": "BULL", "setup": "TREND_PULLBACK"},
        "strategy": {"setup_type": "TREND_PULLBACK", "direction": "LONG"},
        "news": {"news_risk": "LOW"},
        "ai_analyst": {**_ai_payload(trade_opinion="AVOID", setup_quality=80), "status": "AVAILABLE", "observed_at": 1_000_000},
    }
    assert journal.record(snapshot, 900_000) is True
    assert journal.record(snapshot, 900_000) is False
    assert journal.reconcile_completed_trades([{
        "entry_opened_at": 1_100_000, "closed_at": 1_500_000,
        "net_pnl_usdt": -5, "exit_reason": "STOP_LOSS",
    }]) == 1
    restarted = AIShadowJournal(str(tmp_path))
    stats = restarted.summary()
    assert stats["by_opinion"]["AVOID"]["sample_size"] == 1
    assert stats["by_opinion"]["AVOID"]["stop_rate"] == 100
    assert stats["quality_gte_75"]["average_pnl_usdt"] == -5
    assert stats["execution_authority"] is False
