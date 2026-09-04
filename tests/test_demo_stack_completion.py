"""Regression coverage for the read-only demo intelligence stack."""

import json
from pathlib import Path

import requests
import pytest

from config.settings import BotSettings
from config.constants import (
    DecisionStatus,
    DerivativesStatus,
    LocationQuality,
    MarketRegime,
    RiskDecision,
    SetupType,
    StructureType,
    TriggerState,
    VolatilityLevel,
)
from core.models import Candle, DecisionReport
from data.binance_client import BinanceFuturesClient
from engines.chart_reader_v3 import ChartReadingEngineV3, MultiTimeframeInterpreter
from engines.strategy_orchestrator import StrategyOrchestrator
from execution.testnet_executor import TestnetExecutor as ReadOnlyTestnetExecutor
from integrations.ai_analyst import AIAnalystV2
from integrations.news_engine import NewsEngineV2
from journal.shadow_journal import ShadowDecisionJournal
from notifications.telegram_notifier import TelegramEventNotifier


def candles(count=220, open_last=False):
    rows = []
    for index in range(count):
        price = 30_000 + index * 10
        rows.append(Candle(timestamp=index * 300_000, open=price - 2, high=price + 8, low=price - 8, close=price + 2, volume=100 + index, is_closed=True))
    if open_last:
        rows.append(Candle(timestamp=count * 300_000, open=99_000, high=120_000, low=1, close=110_000, volume=1_000_000, is_closed=False))
    return rows


def rejected_report():
    return DecisionReport(
        timestamp=1,
        price=30_000,
        regime=MarketRegime.BULL,
        regime_score=25,
        confidence="MEDIUM",
        volatility=VolatilityLevel.NORMAL,
        structure_4h=StructureType.BULLISH,
        structure_1h=StructureType.BULLISH,
        location=LocationQuality.GOOD_LONG_LOCATION,
        setup=SetupType.TREND_PULLBACK,
        trigger_state=TriggerState.ENTRY_READY,
        derivatives=DerivativesStatus.CONFIRM,
        risk_status=RiskDecision.REJECT_TRADE,
        final_decision=DecisionStatus.LONG_WATCH,
        reason="Risk rejected",
    )


def test_chart_reader_uses_closed_candles_only():
    result = ChartReadingEngineV3().analyze_timeframe("5m", candles(open_last=True))
    assert result["closed_candles"] == 220
    assert result["last_closed_at"] == (219 * 300_000) + 300_000
    assert result["ema20"] < 40_000


def test_demo_safety_flags_cannot_be_disabled_by_environment_values():
    settings = BotSettings(ACCOUNT_READ_ONLY=False, SHADOW_MODE=False)
    assert settings.ACCOUNT_READ_ONLY is True
    assert settings.SHADOW_MODE is True


def test_binance_order_method_is_hard_blocked_for_mainnet_and_testnet():
    for testnet in (False, True):
        client = BinanceFuturesClient(api_key="key", api_secret="secret", testnet=testnet)
        with pytest.raises(RuntimeError, match="ORDER_SUBMISSION_DISABLED"):
            client.place_order()


def test_chart_swings_are_not_exposed_before_confirmation_close():
    result = ChartReadingEngineV3().analyze_timeframe("5m", candles())
    swings = result["swing_highs"] + result["swing_lows"]
    assert all(swing["confirmed_at"] <= result["last_closed_at"] for swing in swings)


def test_mtf_output_is_deterministic():
    chart = {"timeframes": {tf: {"structure": "BULLISH", "trend": "UP", "bos": None, "choch": None, "retest_state": "NONE"} for tf in ("4h", "1h", "15m", "5m")}}
    interpreter = MultiTimeframeInterpreter()
    assert interpreter.interpret(chart) == interpreter.interpret(chart)
    assert interpreter.interpret(chart)["overall_bias"] == "STRONG_LONG"


def test_ai_execution_authority_is_forced_false(monkeypatch):
    class Response:
        status_code = 200

        def json(self):
            payload = {"market_view": "Long bias", "best_setup": "TREND_PULLBACK", "conflicts": [], "risk_notes": [], "decision_explanation": "Advisory", "confidence": 90, "execution_authority": True}
            return {"output": [{"content": [{"type": "output_text", "text": json.dumps(payload)}]}]}

    monkeypatch.setattr(requests, "post", lambda *args, **kwargs: Response())
    result = AIAnalystV2("secret", enabled=True).analyze({"risk": {"status": "REJECT_TRADE"}})
    assert result["execution_authority"] is False


def test_ai_cannot_override_risk_reject():
    report = rejected_report()
    assert StrategyOrchestrator.final_decision(report, {"best_setup": "LONG", "confidence": 100}) == "NO_TRADE"


def test_news_unavailable_is_not_neutral(monkeypatch):
    engine = NewsEngineV2(["https://example.invalid/rss"])
    monkeypatch.setattr(engine, "_fetch", lambda url: (_ for _ in ()).throw(requests.ConnectionError()))
    result = engine.evaluate(force=True)
    assert result["status"] == "UNAVAILABLE"
    assert result["news_risk"] == "UNAVAILABLE"
    assert result["sentiment"] == "UNAVAILABLE"


def test_telegram_deduplicates_identical_event():
    class Client:
        def __init__(self):
            self.calls = 0

        def send_message(self, text):
            self.calls += 1
            return {"sent": True}

    client = Client()
    notifier = TelegramEventNotifier(client)
    first = notifier.notify("WAIT_TRIGGER", {"direction": "LONG"}, dedupe_key="same")
    second = notifier.notify("WAIT_TRIGGER", {"direction": "LONG"}, dedupe_key="same")
    assert first["sent"] is True
    assert second["deduplicated"] is True
    assert client.calls == 1


def test_shadow_phase_testnet_executor_never_calls_order_client():
    class Client:
        def __init__(self):
            self.calls = 0

        def place_order(self, **kwargs):
            self.calls += 1

    client = Client()
    executor = ReadOnlyTestnetExecutor(client, journaler=None)
    assert executor.orders_enabled is False
    assert executor.process_decision(None, None) is None
    assert client.calls == 0


def test_dashboard_has_only_allowlisted_helper_posts_and_no_order_route():
    source = Path("dashboard_server.py").read_text(encoding="utf-8")
    assert '"/api/telegram/test"' in source
    assert '"/api/telegram/current-decision"' in source
    assert '"/api/ai/analyze"' in source
    for forbidden in ("/api/order", "/api/buy", "/api/sell", "/api/close-position"):
        assert forbidden not in source


def test_frontend_contains_no_secret_values_or_env_names():
    assets = (Path("dashboard/index.html").read_text(encoding="utf-8") + Path("dashboard/app.js").read_text(encoding="utf-8"))
    for forbidden in ("BINANCE_API_KEY", "BINANCE_API_SECRET", "OPENAI_API_KEY", "TELEGRAM_BOT_TOKEN", "DASHBOARD_ADMIN_TOKEN", "signature"):
        assert forbidden not in assets


def test_shadow_journal_persists_complete_decision_envelope(tmp_path):
    journal = ShadowDecisionJournal(str(tmp_path))
    snapshot = {
        "decision_id": "SHADOW-1", "decision": {"timestamp": 1, "risk_status": "REJECT_TRADE"},
        "market": {"price": 30_000}, "chart_intelligence": {"status": "AVAILABLE"},
        "strategy": {"setup_type": "NONE"}, "news": {"status": "UNAVAILABLE"},
        "derivatives": {}, "ai_analyst": {"execution_authority": False},
        "account": {"environment": "TESTNET"}, "final_decision": "NO_TRADE",
    }
    assert journal.record(snapshot) is True
    assert journal.record(snapshot) is False
    saved = json.loads((tmp_path / "shadow_decisions.jsonl").read_text(encoding="utf-8"))
    assert saved["orders_enabled"] is False
    assert saved["shadow_mode"] is True
    assert saved["final_decision"] == "NO_TRADE"
    assert saved["chart_state"]["status"] == "AVAILABLE"


def test_admin_token_protection_is_constant_time_and_redacts_snapshot_account():
    source = Path("dashboard_server.py").read_text(encoding="utf-8")
    assert "hmac.compare_digest" in source
    assert '"status": "PROTECTED"' in source
    assert '"error": "ADMIN_AUTH_REQUIRED"' in source
