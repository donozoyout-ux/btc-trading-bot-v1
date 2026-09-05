import pytest

from config.constants import LocationQuality, SetupType, StructureType, TradeDirection
from core.models import Candle, ConfluenceZone, LocationResult, MarketStructure, SetupSignal, TradePlan
from engines.entry_quality_engine import EntryQualityEngine
from engines.trigger_engine import EntryTriggerEngine
from execution.testnet_executor import TestnetExecutor
from data.binance_execution_client import ExecutionError
from core.state import BotState
from journal.execution_journal import ExecutionJournal
from tests.test_testnet_execution import FakeExecutionClient, enabled_settings, snapshot


def candles(last_open=100, last_close=106, last_high=107, last_low=99, last_volume=100):
    rows = [Candle(timestamp=i * 300_000, open=100, high=101, low=99, close=100, volume=100, is_closed=True) for i in range(20)]
    rows[-1] = Candle(timestamp=19 * 300_000, open=last_open, high=last_high, low=last_low, close=last_close, volume=last_volume, is_closed=True)
    return rows


def quality(direction=TradeDirection.LONG, structure=StructureType.MIXED, bos=None, choch=None, support=None, resistance=None, setup_type=SetupType.TREND_PULLBACK, breakout_level=None, rows_5m=None, rows_15m=None):
    plan = TradePlan(setup_type=setup_type, direction=direction, entry_price=100, stop_loss=98 if direction == TradeDirection.LONG else 102,
                     tp1=104 if direction == TradeDirection.LONG else 96, tp2=105 if direction == TradeDirection.LONG else 95,
                     invalidation=98 if direction == TradeDirection.LONG else 102, risk_reward=2)
    setup = SetupSignal(setup_type=setup_type, direction=direction, detected=True, invalidation_level=plan.stop_loss,
                        target_level=plan.tp1, retest_hold=setup_type == SetupType.BREAKOUT_RETEST, breakout_level=breakout_level)
    location = LocationResult(quality=LocationQuality.NEUTRAL, current_price=100, nearest_support=support,
                              nearest_resistance=resistance,
                              distance_to_support_pct=0.001 if support else 999,
                              distance_to_resistance_pct=0.001 if resistance else 999)
    market_structure = MarketStructure(timeframe="5m", structure=structure, last_bos=bos, last_choch=choch)
    return EntryQualityEngine(2, 0.005, 1.5).evaluate(direction, plan, setup, location, market_structure, rows_5m or candles(), rows_15m or candles())


def test_opposing_5m_bos_blocks_both_directions():
    assert "OPPOSING_5M_BOS" in quality(TradeDirection.LONG, StructureType.BEARISH, "BEARISH_BOS").reason_codes
    assert "OPPOSING_5M_BOS" in quality(TradeDirection.SHORT, StructureType.BULLISH, "BULLISH_BOS").reason_codes


def test_opposing_5m_choch_blocks_both_directions():
    assert "OPPOSING_5M_CHOCH" in quality(TradeDirection.LONG, choch="BEARISH_CHOCH").reason_codes
    assert "OPPOSING_5M_CHOCH" in quality(TradeDirection.SHORT, choch="BULLISH_CHOCH").reason_codes


def test_nearby_resistance_blocks_long_unless_converted_breakout():
    resistance = ConfluenceZone(level_type="RESISTANCE", price_min=100.2, price_max=100.4, center=100.3, strength=3)
    assert "TOO_CLOSE_TO_RESISTANCE" in quality(resistance=resistance).reason_codes
    converted = quality(resistance=resistance, setup_type=SetupType.BREAKOUT_RETEST, breakout_level=100.3)
    assert "TOO_CLOSE_TO_RESISTANCE" not in converted.reason_codes
    unrelated = quality(resistance=resistance, setup_type=SetupType.BREAKOUT_RETEST, breakout_level=90)
    assert "TOO_CLOSE_TO_RESISTANCE" in unrelated.reason_codes


def test_nearby_support_blocks_short_unless_that_exact_level_was_converted():
    support = ConfluenceZone(level_type="SUPPORT", price_min=99.6, price_max=99.8, center=99.7, strength=3)
    assert "TOO_CLOSE_TO_SUPPORT" in quality(direction=TradeDirection.SHORT, support=support).reason_codes
    assert "TOO_CLOSE_TO_SUPPORT" not in quality(direction=TradeDirection.SHORT, support=support, setup_type=SetupType.BREAKOUT_RETEST, breakout_level=99.7).reason_codes


def test_strong_body_with_ordinary_volume_is_not_entry_ready_but_rejection_plus_support_may_be():
    engine = EntryTriggerEngine(volume_rvol_threshold=1.5)
    ordinary = candles()
    for row in ordinary[-4:-1]:
        row.high = 110
    confirmed, label = engine.evaluate_5m_patterns(ordinary, TradeDirection.LONG)
    assert confirmed is False
    assert "VOLUME_NORMAL" in label and "VOLUME_EXPANSION" not in label
    confirmed, label = engine.evaluate_5m_patterns(candles(last_open=100, last_close=102, last_high=103, last_low=95, last_volume=160), TradeDirection.LONG)
    assert confirmed is True
    assert "Wick Rejection" in label and "VOLUME_EXPANSION" in label


def test_open_5m_candle_cannot_create_trigger_or_change_entry_quality_metrics():
    engine = EntryTriggerEngine(volume_rvol_threshold=1.5)
    closed = candles(last_open=100, last_close=100, last_high=101, last_low=99, last_volume=100)
    assert engine.evaluate_5m_patterns(closed, TradeDirection.LONG)[0] is False
    open_bar = Candle(timestamp=20 * 300_000, open=100, high=120, low=99, close=119, volume=1000, is_closed=False)
    assert engine.evaluate_5m_patterns(closed + [open_bar], TradeDirection.LONG)[0] is False
    baseline = quality(rows_5m=closed, rows_15m=closed)
    injected = quality(rows_5m=closed + [open_bar], rows_15m=closed + [open_bar])
    assert injected.atr_extension_5m == baseline.atr_extension_5m
    assert injected.atr_extension_15m == baseline.atr_extension_15m
    assert injected.rsi_5m == baseline.rsi_5m


def test_live_testnet_sizing_uses_exchange_wallet_and_expected_risk(tmp_path):
    client = FakeExecutionClient(wallet_balance=5000)
    executor = TestnetExecutor(client, settings=enabled_settings(tmp_path))
    state = BotState(account_balance_usdt=10000)
    payload = snapshot()
    payload["risk_capital"].update(sizing_capital_usdt=5000.0, wallet_balance_usdt=5000.0, available_balance_usdt=5000.0)
    payload["decision"]["risk_assessment"].update(position_size_btc=0.025, risk_amount_usdt=25.0, risk_pct_used=0.005)
    result = executor.process_snapshot(payload, state)
    assert result["status"] == "OPENED"
    assert state.account_balance_usdt == 5000
    assert client.market_calls[0]["quantity"] == pytest.approx(0.025)


def test_missing_exchange_balance_and_bad_basis_fail_before_order(tmp_path):
    unavailable = FakeExecutionClient(wallet_balance=None)
    unavailable_payload = snapshot()
    unavailable_payload["risk_capital"] = {"source": "UNAVAILABLE", "sizing_capital_usdt": None}
    with pytest.raises(ExecutionError, match="RISK_CAPITAL_UNAVAILABLE"):
        TestnetExecutor(unavailable, settings=enabled_settings(tmp_path / "a")).process_snapshot(unavailable_payload, BotState())
    assert unavailable.market_calls == []
    bad_basis = FakeExecutionClient(mark_price=81000)
    with pytest.raises(ExecutionError, match="EXECUTION_PRICE_DEVIATION"):
        TestnetExecutor(bad_basis, settings=enabled_settings(tmp_path / "b")).process_snapshot(snapshot(), BotState())
    assert bad_basis.market_calls == []

    missing_mark = FakeExecutionClient(mark_price=None)
    with pytest.raises(ExecutionError, match="MARK_PRICE_UNAVAILABLE"):
        TestnetExecutor(missing_mark, settings=enabled_settings(tmp_path / "c")).process_snapshot(snapshot(), BotState())
    assert missing_mark.market_calls == []


def test_bad_fill_triggers_immediate_flatten(tmp_path):
    client = FakeExecutionClient(fill_price=80100)
    executor = TestnetExecutor(client, settings=enabled_settings(tmp_path))
    with pytest.raises(ExecutionError, match="EXECUTION_PRICE_DEVIATION"):
        executor.process_snapshot(snapshot(), BotState())
    assert client.close_calls == 1
    assert client.position["position_amt"] == 0


def test_post_fill_reconciliation_failure_attempts_flatten_and_latches_if_unconfirmed(tmp_path):
    class ReconciliationFailureClient(FakeExecutionClient):
        def get_position(self, symbol="BTCUSDT"):
            if self.market_calls:
                raise RuntimeError("position read failed")
            return super().get_position(symbol)

    client = ReconciliationFailureClient()
    state = BotState()
    with pytest.raises(RuntimeError, match="position read failed"):
        TestnetExecutor(client, settings=enabled_settings(tmp_path)).process_snapshot(snapshot(), state)
    assert client.close_calls == 1
    assert state.kill_switch_activated is True


def test_executor_preserves_risk_engine_capped_quantity_and_snapshot_mark_order(tmp_path):
    client = FakeExecutionClient(wallet_balance=1000)
    journal = ExecutionJournal(str(tmp_path))
    events = []
    original_record = journal.record
    original_mark = client.get_mark_price
    original_market = client.place_market_order

    def record(**kwargs):
        events.append(kwargs["action"])
        return original_record(**kwargs)

    def mark(*args, **kwargs):
        events.append("GET_MARK")
        return original_mark(*args, **kwargs)

    def market(*args, **kwargs):
        events.append("MARKET_ORDER")
        return original_market(*args, **kwargs)

    journal.record = record
    client.get_mark_price = mark
    client.place_market_order = market
    payload = snapshot()
    payload["strategy"]["trade_plan"].update(entry_price=80000.0, stop_loss=79999.0)
    payload["decision"]["risk_assessment"].update(position_size_btc=0.0125, risk_amount_usdt=5.0, risk_pct_used=0.005)
    TestnetExecutor(client, settings=enabled_settings(tmp_path), execution_journal=journal).process_snapshot(payload, BotState())
    assert client.market_calls[0]["quantity"] == pytest.approx(0.0125)
    assert events.index("ENTRY_SNAPSHOT") < events.index("GET_MARK") < events.index("MARKET_ORDER")

    oversized = snapshot(ts=2, decision_id="OVERSIZED")
    oversized["strategy"]["trade_plan"].update(entry_price=80000.0, stop_loss=79999.0)
    oversized["decision"]["risk_assessment"].update(position_size_btc=5.0, risk_amount_usdt=5.0, risk_pct_used=0.005)
    oversized_client = FakeExecutionClient(wallet_balance=1000)
    with pytest.raises(ExecutionError, match="POSITION_SIZE_EXCEEDS_LEVERAGE_CAP"):
        TestnetExecutor(oversized_client, settings=enabled_settings(tmp_path / "oversized")).process_snapshot(oversized, BotState())
    assert oversized_client.market_calls == []


def test_explicit_empty_hard_blockers_does_not_promote_legacy_warning_to_blocker(tmp_path):
    payload = snapshot()
    payload["strategy"]["blocking_reasons"] = ["MTF advisory conflict"]
    payload["strategy"]["warnings"] = ["MTF advisory conflict"]
    client = FakeExecutionClient()
    result = TestnetExecutor(client, settings=enabled_settings(tmp_path)).process_snapshot(payload, BotState())
    assert result["status"] == "OPENED"
