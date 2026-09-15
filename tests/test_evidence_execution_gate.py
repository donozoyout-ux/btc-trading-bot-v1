from config.settings import BotSettings
from core.state import BotState
from execution.testnet_executor import TestnetExecutor
from journal.execution_journal import ExecutionJournal
from tests.test_testnet_execution import FakeExecutionClient, enabled_settings, snapshot


def gate_settings(tmp_path, **overrides):
    base = dict(
        EVIDENCE_EXECUTION_GATE_ENABLED=True,
        EVIDENCE_ALLOWED_SETUP="TREND_PULLBACK",
        EVIDENCE_ALLOWED_DIRECTION="SHORT",
        EVIDENCE_ALLOWED_REGIME="STRONG_BEAR",
        EVIDENCE_REQUIRE_FUTURES_NATIVE=True,
        PERFORMANCE_GUARD_ENABLED=False,
    )
    base.update(overrides)
    return enabled_settings(tmp_path, **base)


def allowed_snapshot():
    payload = snapshot()
    payload["final_decision"] = "SHORT_ENTRY"
    payload["decision"]["regime"] = "STRONG_BEAR"
    payload["strategy"].update(
        setup_type="TREND_PULLBACK",
        direction="SHORT",
    )
    payload["sources"]["binance"].update(
        market_basis="FUTURES_NATIVE",
        market_data_source="PRODUCTION_FUTURES_PUBLIC",
    )
    return payload


def test_evidence_gate_allows_only_supported_slice(tmp_path):
    client = FakeExecutionClient()
    executor = TestnetExecutor(
        client,
        settings=gate_settings(tmp_path),
        execution_journal=ExecutionJournal(str(tmp_path)),
    )
    result = executor._evidence_execution_gate(allowed_snapshot())
    assert result["allowed"] is True
    assert result["reason"] == "PASS"


def test_evidence_gate_blocks_long_direction(tmp_path):
    payload = allowed_snapshot()
    payload["final_decision"] = "LONG_ENTRY"
    payload["strategy"]["direction"] = "LONG"
    executor = TestnetExecutor(
        FakeExecutionClient(),
        settings=gate_settings(tmp_path),
        execution_journal=ExecutionJournal(str(tmp_path)),
    )
    result = executor._evidence_execution_gate(payload)
    assert result["allowed"] is False
    assert result["reason"] == "UNVALIDATED_DIRECTION"


def test_evidence_gate_blocks_breakout_retest(tmp_path):
    payload = allowed_snapshot()
    payload["strategy"]["setup_type"] = "BREAKOUT_RETEST"
    executor = TestnetExecutor(
        FakeExecutionClient(),
        settings=gate_settings(tmp_path),
        execution_journal=ExecutionJournal(str(tmp_path)),
    )
    result = executor._evidence_execution_gate(payload)
    assert result["allowed"] is False
    assert result["reason"] == "UNVALIDATED_SETUP"


def test_evidence_gate_blocks_non_strong_bear_regime(tmp_path):
    payload = allowed_snapshot()
    payload["decision"]["regime"] = "BEAR"
    executor = TestnetExecutor(
        FakeExecutionClient(),
        settings=gate_settings(tmp_path),
        execution_journal=ExecutionJournal(str(tmp_path)),
    )
    result = executor._evidence_execution_gate(payload)
    assert result["allowed"] is False
    assert result["reason"] == "UNVALIDATED_REGIME"


def test_evidence_gate_blocks_spot_proxy(tmp_path):
    payload = allowed_snapshot()
    payload["sources"]["binance"].update(
        market_basis="SPOT_PROXY",
        market_data_source="BINANCE_SPOT_PUBLIC_PROXY",
    )
    executor = TestnetExecutor(
        FakeExecutionClient(),
        settings=gate_settings(tmp_path),
        execution_journal=ExecutionJournal(str(tmp_path)),
    )
    result = executor._evidence_execution_gate(payload)
    assert result["allowed"] is False
    assert result["reason"] == "NON_FUTURES_NATIVE_MARKET_DATA"


def test_process_snapshot_does_not_submit_unvalidated_long(tmp_path):
    client = FakeExecutionClient()
    executor = TestnetExecutor(
        client,
        settings=gate_settings(tmp_path),
        execution_journal=ExecutionJournal(str(tmp_path)),
    )
    payload = snapshot()
    payload["decision"]["regime"] = "STRONG_BULL"
    payload["strategy"].update(setup_type="TREND_PULLBACK", direction="LONG")
    payload["sources"]["binance"].update(
        market_basis="FUTURES_NATIVE",
        market_data_source="PRODUCTION_FUTURES_PUBLIC",
    )
    result = executor.process_snapshot(payload, BotState())
    assert result["status"] == "EVIDENCE_GATE_BLOCKED"
    assert result["gate"]["reason"] == "UNVALIDATED_DIRECTION"
    assert client.market_calls == []


def test_process_snapshot_supported_short_can_reach_order_path(tmp_path):
    client = FakeExecutionClient()
    executor = TestnetExecutor(
        client,
        settings=gate_settings(tmp_path),
        execution_journal=ExecutionJournal(str(tmp_path)),
    )
    result = executor.process_snapshot(allowed_snapshot(), BotState())
    assert result["status"] == "OPENED"
    assert client.market_calls
    assert client.market_calls[0]["side"] == "SELL"
