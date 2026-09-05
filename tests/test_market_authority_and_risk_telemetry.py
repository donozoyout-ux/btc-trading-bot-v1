import copy

import requests

import dashboard_server as base
from config.constants import (
    DecisionStatus,
    DerivativesStatus,
    LocationQuality,
    MarketRegime,
    RiskDecision,
    SetupType,
    StructureType,
    TradeDirection,
    TriggerState,
    VolatilityLevel,
)
from core.models import DecisionReport, RiskAssessment
from data.render_market_client import RenderResilientBinanceFuturesMarketClient
from engines.strategy_orchestrator import StrategyOrchestrator
from render_server import RenderDashboardRuntime


def _report(
    *,
    final=DecisionStatus.NO_TRADE,
    setup=SetupType.NONE,
    setup_direction=TradeDirection.WAIT,
    trigger=TriggerState.NO_SETUP,
    risk_status=RiskDecision.REJECT_TRADE,
    risk_assessment=None,
):
    return DecisionReport(
        timestamp=1,
        price=80000.0,
        regime=MarketRegime.BULL,
        regime_score=20.0,
        confidence="MEDIUM",
        volatility=VolatilityLevel.NORMAL,
        structure_4h=StructureType.BULLISH,
        structure_1h=StructureType.BULLISH,
        location=LocationQuality.NEUTRAL,
        setup=setup,
        setup_direction=setup_direction,
        trigger_state=trigger,
        derivatives=DerivativesStatus.NEUTRAL,
        risk_status=risk_status,
        final_decision=final,
        reason="test",
        risk_assessment=risk_assessment,
    )


def test_no_setup_without_risk_assessment_does_not_report_risk_reject():
    report = _report()
    summary = StrategyOrchestrator().summarize(report, {}, {}, {})
    assert "NO_DETERMINISTIC_SETUP" in summary["blocking_reasons"]
    assert "RISK_REJECT" not in summary["blocking_reasons"]
    assert summary["risk_evaluated"] is False


def test_watch_decision_survives_until_risk_is_actually_evaluated():
    report = _report(
        final=DecisionStatus.LONG_WATCH,
        setup=SetupType.TREND_PULLBACK,
        setup_direction=TradeDirection.LONG,
        trigger=TriggerState.WAITING_TRIGGER,
    )
    summary = StrategyOrchestrator().summarize(report, {}, {}, {})
    assert "RISK_REJECT" not in summary["blocking_reasons"]
    assert StrategyOrchestrator.final_decision(report) == DecisionStatus.LONG_WATCH.value


def test_actual_risk_rejection_is_reported_and_blocks_final_decision():
    assessment = RiskAssessment(
        decision=RiskDecision.REJECT_TRADE,
        direction=TradeDirection.LONG,
        rejection_reason="BAD_RISK_REWARD",
    )
    report = _report(
        final=DecisionStatus.LONG_ENTRY,
        setup=SetupType.TREND_PULLBACK,
        setup_direction=TradeDirection.LONG,
        trigger=TriggerState.ENTRY_READY,
        risk_assessment=assessment,
    )
    summary = StrategyOrchestrator().summarize(report, {}, {}, {})
    assert "RISK_REJECT" in summary["blocking_reasons"]
    assert StrategyOrchestrator.final_decision(report) == DecisionStatus.NO_TRADE.value


def _http_error(status_code: int) -> requests.HTTPError:
    response = requests.Response()
    response.status_code = status_code
    return requests.HTTPError(str(status_code), response=response)


class PrimaryOi451:
    def get_open_interest(self, *_args, **_kwargs):
        raise _http_error(451)


class FallbackOi:
    def get_open_interest(self, *_args, **_kwargs):
        return 394257006.30


class PrimaryOiHealthy:
    def get_open_interest(self, *_args, **_kwargs):
        return 12345.0


def test_testnet_fallback_oi_is_display_only_and_not_authoritative():
    client = RenderResilientBinanceFuturesMarketClient(
        primary=PrimaryOi451(),
        fallback=FallbackOi(),
        restriction_cooldown_seconds=30,
    )
    assert client.get_open_interest("BTCUSDT") is None
    telemetry = client.fallback_derivatives_telemetry()["get_open_interest"]
    assert telemetry["value"] == 394257006.30
    assert telemetry["source"] == "BINANCE_TESTNET_FALLBACK"
    assert telemetry["trading_authority"] is False
    assert client.status()["market_data_trading_safe"] is False


def test_production_futures_derivative_remains_authoritative():
    client = RenderResilientBinanceFuturesMarketClient(
        primary=PrimaryOiHealthy(),
        fallback=FallbackOi(),
        restriction_cooldown_seconds=30,
    )
    assert client.get_open_interest("BTCUSDT") == 12345.0
    assert client.status()["market_data_source"] == "PRODUCTION_FUTURES_PUBLIC"
    assert client.status()["market_data_trading_safe"] is True
    assert client.status()["market_basis"] == "FUTURES_NATIVE"


class FakeLearning:
    def analyze(self):
        return {
            "status": "WARMUP",
            "mode": "ADVISORY_ONLY",
            "samples": 0,
            "auto_parameter_changes": False,
        }


class FakeFallbackBinance:
    def status(self):
        return {
            "market_data_source": "TESTNET_PUBLIC_FALLBACK",
            "market_data_trading_safe": False,
            "market_basis": "TESTNET_FUTURES",
            "fallback_active": True,
        }

    def fallback_derivatives_telemetry(self):
        return {
            "get_open_interest": {
                "value": 111.0,
                "source": "BINANCE_TESTNET_FALLBACK",
                "observed_at": 10,
                "trading_authority": False,
            },
            "get_funding_rate": {
                "value": 0.0001,
                "source": "BINANCE_TESTNET_FALLBACK",
                "observed_at": 10,
                "trading_authority": False,
            },
        }


class FakeProductionBinance:
    def status(self):
        return {
            "market_data_source": "PRODUCTION_FUTURES_PUBLIC",
            "market_data_trading_safe": True,
            "market_basis": "FUTURES_NATIVE",
            "fallback_active": False,
        }

    def fallback_derivatives_telemetry(self):
        return {}


class FakeSpotProxyBinance:
    def status(self):
        return {
            "market_data_source": "BINANCE_SPOT_PUBLIC_PROXY",
            "market_data_trading_safe": True,
            "market_basis": "SPOT_PROXY",
            "fallback_active": True,
            "spot_proxy_status": "AVAILABLE",
        }

    def fallback_derivatives_telemetry(self):
        return {
            "get_open_interest": {
                "value": 222.0,
                "source": "BINANCE_TESTNET_FALLBACK",
                "observed_at": 10,
                "trading_authority": False,
            }
        }


def _base_snapshot():
    return {
        "final_decision": "LONG_ENTRY",
        "meta": {},
        "sources": {"binance": {"status": "HEALTHY"}},
        "strategy": {"eligible": True, "blocking_reasons": [], "hard_blockers": []},
        "derivatives": {"status": "CONFIRM"},
        "market": {},
    }


def _runtime(binance):
    runtime = object.__new__(RenderDashboardRuntime)
    runtime.binance = binance
    runtime.learning_engine = FakeLearning()
    return runtime


def test_render_snapshot_blocks_new_entry_when_market_is_testnet_fallback(monkeypatch):
    payload = _base_snapshot()
    monkeypatch.setattr(base.DashboardRuntime, "snapshot", lambda self, force=False: copy.deepcopy(payload))
    runtime = _runtime(FakeFallbackBinance())

    snapshot = RenderDashboardRuntime.snapshot(runtime, force=True)

    assert snapshot["meta"]["market_data_trading_safe"] is False
    assert snapshot["final_decision"] == "NO_TRADE"
    assert snapshot["strategy"]["eligible"] is False
    assert "MARKET_DATA_NOT_TRADING_SAFE" in snapshot["strategy"]["blocking_reasons"]
    assert "MARKET_DATA_NOT_TRADING_SAFE" in snapshot["strategy"]["hard_blockers"]
    assert snapshot["sources"]["binance"]["status"] == "DEGRADED"
    assert snapshot["derivatives"]["open_interest"]["source"] == "BINANCE_TESTNET_FALLBACK"
    assert snapshot["derivatives"]["trading_authority"] == "SUPPLEMENTAL_OR_NONE"
    assert snapshot["learning"]["mode"] == "ADVISORY_ONLY"


def test_render_snapshot_keeps_entry_authority_on_native_production_futures(monkeypatch):
    payload = _base_snapshot()
    monkeypatch.setattr(base.DashboardRuntime, "snapshot", lambda self, force=False: copy.deepcopy(payload))
    runtime = _runtime(FakeProductionBinance())

    snapshot = RenderDashboardRuntime.snapshot(runtime, force=True)

    assert snapshot["meta"]["market_data_trading_safe"] is True
    assert snapshot["meta"]["market_basis"] == "FUTURES_NATIVE"
    assert snapshot["final_decision"] == "LONG_ENTRY"
    assert snapshot["strategy"]["eligible"] is True
    assert snapshot["derivatives"]["trading_authority"] == "PRODUCTION_FUTURES"
    assert "MARKET_DATA_NOT_TRADING_SAFE" not in snapshot["strategy"]["blocking_reasons"]
    assert "MARKET_DATA_NOT_TRADING_SAFE" not in snapshot["strategy"]["hard_blockers"]


def test_render_snapshot_allows_testnet_forward_test_on_real_spot_proxy(monkeypatch):
    payload = _base_snapshot()
    monkeypatch.setattr(base.DashboardRuntime, "snapshot", lambda self, force=False: copy.deepcopy(payload))
    runtime = _runtime(FakeSpotProxyBinance())

    snapshot = RenderDashboardRuntime.snapshot(runtime, force=True)

    assert snapshot["meta"]["market_data_trading_safe"] is True
    assert snapshot["meta"]["market_basis"] == "SPOT_PROXY"
    assert snapshot["meta"]["forward_test_price_proxy"] is True
    assert snapshot["final_decision"] == "LONG_ENTRY"
    assert snapshot["strategy"]["eligible"] is True
    assert snapshot["derivatives"]["trading_authority"] == "SUPPLEMENTAL_OR_NONE"
    assert snapshot["derivatives"]["open_interest"]["source"] == "BINANCE_TESTNET_FALLBACK"
