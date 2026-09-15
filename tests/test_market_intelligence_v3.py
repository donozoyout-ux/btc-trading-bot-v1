from datetime import datetime, timedelta, timezone

from integrations.ai_analyst import AIAnalyst
from integrations.news_engine import NewsEngine


def test_ai_v3_remains_shadow_only_when_unconfigured():
    ai = AIAnalyst(None, "gpt-5.6-luna", enabled=True)
    result = ai.analyze({"shadow_contract": {"deterministic_final_decision": "NO_TRADE"}})
    assert result["status"] == "UNAVAILABLE"
    assert result["trade_opinion"] == "WAIT"
    assert result["execution_authority"] is False


def test_ai_v3_schema_forbids_execution_authority():
    schema = AIAnalyst.SCHEMA
    assert schema["properties"]["execution_authority"]["const"] is False
    assert "setup_quality" in schema["required"]
    assert "trade_opinion" in schema["required"]
    assert schema["properties"]["setup_quality"]["maximum"] == 100


def test_news_v3_weights_fresh_high_impact_event(monkeypatch):
    engine = NewsEngine(["https://www.coindesk.com/rss"], enabled=True)
    now = datetime.now(timezone.utc)
    fresh = (now - timedelta(minutes=20)).isoformat()
    older = (now - timedelta(hours=18)).isoformat()

    rows = [
        {
            "source": "www.coindesk.com",
            "title": "Bitcoin exchange hack triggers liquidation wave",
            "title_key": "bitcoin exchange hack triggers liquidation wave",
            "published_at": fresh,
            "age_hours": 0.33,
            "category": "HACK",
            "sentiment": "BEARISH",
            "sentiment_score": -60.0,
            "importance": "HIGH",
            "btc_relevance": "HIGH",
            "impact_score": 95.0,
            "risk_score": 100.0,
            "url": "https://example.com/1",
        },
        {
            "source": "www.coindesk.com",
            "title": "Bitcoin adoption expands",
            "title_key": "bitcoin adoption expands",
            "published_at": older,
            "age_hours": 18.0,
            "category": "BITCOIN",
            "sentiment": "BULLISH",
            "sentiment_score": 8.0,
            "importance": "MEDIUM",
            "btc_relevance": "HIGH",
            "impact_score": 12.0,
            "risk_score": 12.0,
            "url": "https://example.com/2",
        },
    ]
    monkeypatch.setattr(engine, "_fetch", lambda url, now: rows)

    result = engine.evaluate(force=True)
    assert result["status"] == "AVAILABLE"
    assert result["news_risk"] == "EXTREME"
    assert result["trade_risk"] == "BLOCK"
    assert result["sentiment"] == "BEARISH"
    assert result["news_risk_score"] == 100.0
    assert result["important_events"][0]["category"] == "HACK"


def test_news_v3_deduplicates_same_headline(monkeypatch):
    engine = NewsEngine(
        ["https://www.coindesk.com/rss", "https://cointelegraph.com/rss"],
        enabled=True,
    )
    row = {
        "source": "www.coindesk.com",
        "title": "Bitcoin ETF inflow accelerates",
        "title_key": "bitcoin etf inflow accelerates",
        "published_at": datetime.now(timezone.utc).isoformat(),
        "age_hours": 0.1,
        "category": "ETF",
        "sentiment": "BULLISH",
        "sentiment_score": 30.0,
        "importance": "HIGH",
        "btc_relevance": "HIGH",
        "impact_score": 80.0,
        "risk_score": 80.0,
        "url": "https://example.com",
    }
    monkeypatch.setattr(engine, "_fetch", lambda url, now: [dict(row)])
    result = engine.evaluate(force=True)
    assert len(result["items"]) == 1
    assert result["sentiment"] == "BULLISH"
