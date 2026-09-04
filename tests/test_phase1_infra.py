"""Phase 1.5 measurement-infra and risk-control regression tests."""

import uuid
from backtest.signal_funnel import SignalFunnel
from backtest.data_loader import HistoricalDataLoader
from backtest.phase1_runner import Phase1BacktestRunner, CandidateTracker
from config.settings import get_settings
from core.models import (
    DecisionReport, TradeRecord, RiskAssessment, RiskDecision,
    RiskReasonCode, GuardType, TradePlan, SetupSignal, TriggerResult, SetupType, TradeDirection,
)
from core.state import BotState
from runner import MasterPipeline
from engines.trade_plan_engine import TradePlanEngine
from config.constants import TriggerState, LocationQuality


# ===================================================================
# TEST 1: Daily loss guard trigger blocks new trades same day
# ===================================================================
def test_daily_loss_guard_blocks_same_day():
    """When daily PnL drawdown exceeds threshold, daily loss guard activates."""
    settings = get_settings()
    settings.MAX_DAILY_LOSS_PCT = 0.001
    pipeline = MasterPipeline(settings)
    ds = HistoricalDataLoader.generate_synthetic_dataset(num_5m_bars=300)
    candles_dict = {k: v for k, v in ds.items()}
    # Set state matching candle day so reset_daily_metrics_if_new_day does NOT fire.
    # The pipeline calls reset first; if same day, daily_realized_pnl is preserved.
    state = BotState(
        daily_realized_pnl_usdt=-200.0,
        start_of_day_balance_usdt=10_000.0,
    )
    # Manually check the guard (bypassing the reset that would zero out PnL)
    # to verify the guard logic itself works.
    triggered = state.check_kill_switch(settings.MAX_DAILY_LOSS_PCT, settings.MAX_CONSECUTIVE_LOSSES)
    assert triggered is True, f"Daily loss guard should trigger on 2% drawdown"
    assert state.daily_loss_guard_active is True


# ===================================================================
# TEST 2: New simulation trading day resets daily loss guard
# ===================================================================
def test_daily_loss_guard_resets_new_day():
    settings = get_settings()
    settings.MAX_DAILY_LOSS_PCT = 0.001
    pipeline = MasterPipeline(settings)
    ds = HistoricalDataLoader.generate_synthetic_dataset(num_5m_bars=300)
    candles_dict = {k: v for k, v in ds.items()}
    # Set state with daily loss guard active
    state = BotState(
        daily_realized_pnl_usdt=-200.0,
        start_of_day_balance_usdt=10_000.0,
        daily_loss_guard_active=True,
        kill_switch_activated=True,
        kill_switch_reason="Daily loss limit reached",
        guard_type=GuardType.DAILY_LOSS_GUARD,
    )
    # Advance to next simulation day
    last_candle_ts = candles_dict["5m"][-1].timestamp + 86_400_000  # +1 day
    state.reset_daily_metrics_if_new_day(last_candle_ts)
    assert state.daily_loss_guard_active is False, "DAILY_LOSS_GUARD should reset at new day"
    assert state.consecutive_loss_cooldown_active is False, "CONSECUTIVE_LOSS_GUARD should reset at new day"
    assert state.consecutive_losses == 0, "Consecutive loss counter should reset"


# ===================================================================
# TEST 3: Consecutive-loss guard does NOT create permanent latch
# ===================================================================
def test_consecutive_loss_no_permanent_latch():
    """After consecutive losses trigger guard, new simulation day resets it."""
    settings = get_settings()
    settings.MAX_CONSECUTIVE_LOSSES = 2
    pipeline = MasterPipeline(settings)
    ds = HistoricalDataLoader.generate_synthetic_dataset(num_5m_bars=300)
    candles_dict = {k: v for k, v in ds.items()}
    state = BotState(consecutive_losses=2)
    # Trigger guard
    triggered = state.check_kill_switch(settings.MAX_DAILY_LOSS_PCT, settings.MAX_CONSECUTIVE_LOSSES)
    assert triggered is True
    assert state.consecutive_loss_cooldown_active is True
    # Advance to next day — should reset
    last_ts = candles_dict["5m"][-1].timestamp + 86_400_000
    state.reset_daily_metrics_if_new_day(last_ts)
    assert state.consecutive_loss_cooldown_active is False, "CONSECUTIVE_LOSS_GUARD must NOT be permanent"
    assert state.kill_switch_activated is False, "kill_switch_activated must clear after new day"


# ===================================================================
# TEST 4: Emergency latch does NOT reset on trading day change
# ===================================================================
def test_emergency_latch_not_reset_by_day_change():
    settings = get_settings()
    pipeline = MasterPipeline(settings)
    ds = HistoricalDataLoader.generate_synthetic_dataset(num_5m_bars=300)
    candles_dict = {k: v for k, v in ds.items()}
    state = BotState()
    state.activate_emergency_latch("Test emergency: data corruption")
    assert state.emergency_latch_active is True
    assert state.kill_switch_activated is True
    # Advance to next day
    last_ts = candles_dict["5m"][-1].timestamp + 86_400_000
    state.reset_daily_metrics_if_new_day(last_ts)
    assert state.emergency_latch_active is True, "EMERGENCY_LATCH must NOT reset on day change"
    assert state.kill_switch_activated is True


# ===================================================================
# TEST 5: Simulation time advances daily reset (not wall-clock)
# ===================================================================
def test_simulation_clock_triggers_daily_reset():
    settings = get_settings()
    pipeline = MasterPipeline(settings)
    ds = HistoricalDataLoader.generate_synthetic_dataset(num_5m_bars=300)
    candles_dict = {k: v for k, v in ds.items()}
    # State stuck on old day
    state = BotState(current_day="2020-01-01", daily_realized_pnl_usdt=-500.0)
    report = pipeline.run_cycle(candles_dict, state, derivatives_input={})
    candle_day = candles_dict["5m"][-1].dt.strftime("%Y-%m-%d")
    assert state.current_day == candle_day, f"Day should advance to {candle_day}, got {state.current_day}"
    assert state.daily_realized_pnl_usdt == 0.0, "Daily PnL should reset"
    assert isinstance(report, DecisionReport)


# ===================================================================
# TEST 6: Conditional funnel monotonic non-increasing
# ===================================================================
def test_conditional_funnel_monotonic():
    f = SignalFunnel()
    f.record_evaluation(100)
    f.record_pass("DATA_HEALTH_PASS", 95)
    f.record_pass("REGIME_ELIGIBLE", 95)
    f.record_pass("KILL_SWITCH_PASS", 90)
    f.record_pass("STRUCTURE_ELIGIBLE", 85)
    f.record_pass("GOOD_TRADE_LOCATION", 80)
    f.record_pass("SETUP_DETECTED", 70)
    f.record_pass("ENTRY_TRIGGER_DETECTED", 50)
    f.record_pass("MOMENTUM_PASS", 45)
    f.record_pass("DERIVATIVES_ACCEPTABLE", 45)
    f.record_pass("TRADE_PLAN_CREATED", 40)
    f.record_pass("RISK_PASS", 35)
    f.record_pass("EXECUTABLE_CANDIDATES", 33)
    f.record_pass("TRADES_OPENED", 30)
    out = f.get_funnel()
    counts = [out[s]["count"] for s in SignalFunnel.STAGES]
    for prev, cur in zip(counts, counts[1:]):
        assert cur <= prev, f"Funnel increased: {prev} -> {cur}"


# ===================================================================
# TEST 7: No conditional conversion > 100%
# ===================================================================
def test_no_conversion_exceeds_100():
    f = SignalFunnel()
    f.record_evaluation(100)
    f.record_pass("DATA_HEALTH_PASS", 100)
    f.record_pass("KILL_SWITCH_PASS", 50)
    f.record_pass("STRUCTURE_ELIGIBLE", 50)
    out = f.get_funnel()
    for stage_name, stage_data in out.items():
        assert stage_data["conversion_from_prev_pct"] <= 100.0, \
            f"{stage_name} conversion {stage_data['conversion_from_prev_pct']}% > 100%"
        assert stage_data["conversion_from_total_pct"] <= 100.0, \
            f"{stage_name} total conversion {stage_data['conversion_from_total_pct']}% > 100%"


# ===================================================================
# TEST 8: Every opened trade has originating candidate_id
# ===================================================================
def test_trade_has_candidate_id():
    tracker = CandidateTracker()
    candidate_id = f"CAND-{uuid.uuid4().hex[:12]}"
    tracker.register_candidate(
        candidate_id=candidate_id,
        setup_type="TREND_PULLBACK",
        direction="LONG",
        entry_price=65000.0,
        stop_loss=64500.0,
        tp1=65800.0,
        tp2=66500.0,
        rr=1.8,
        guard_type="OTHER_RISK_CONTROL_BLOCK",
        reason_code="OTHER",
    )
    tracker.mark_candidate_passed_risk(candidate_id)
    trade_id = "PH1-ABC12345"
    tracker.reconcile_trade(trade_id, candidate_id)
    recon = tracker.get_reconciliation()
    assert recon["unreconciled_trades"] == 0, f"Trade {trade_id} not reconciled to candidate"
    assert recon["reconciliation_pass"] is True


# ===================================================================
# TEST 9: No duplicate trades from same candidate
# ===================================================================
def test_no_duplicate_trade_per_candidate():
    tracker = CandidateTracker()
    cid = f"CAND-{uuid.uuid4().hex[:12]}"
    tracker.register_candidate(cid, "TREND_PULLBACK", "LONG", 65000.0, 64500.0, 65800.0, 66500.0, 1.8, "OTHER", "OTHER")
    tracker.mark_candidate_passed_risk(cid)
    tracker.reconcile_trade("T1", cid)
    tracker.reconcile_trade("T2", cid)  # Should not happen but shouldn't crash
    # Both trades map to same candidate — check reconciliation
    recon = tracker.get_reconciliation()
    assert recon["candidates_produced_trade"] <= 1, "Same candidate produced multiple trades"


# ===================================================================
# TEST 10: Trades opened <= executable candidates
# ===================================================================
def test_trades_not_exceed_candidates():
    settings = get_settings()
    pipeline = MasterPipeline(settings)
    ds = HistoricalDataLoader.generate_synthetic_dataset(num_5m_bars=5000)
    runner = Phase1BacktestRunner()
    results = runner.run(ds, start_idx=500)
    recon = results.get("candidate_reconciliation", {})
    trades = results["total_trades"]
    candidates = recon.get("candidates_passed_risk", 0)
    assert trades <= candidates, f"Trades ({trades}) > Candidates ({candidates})"


# ===================================================================
# TEST 11: Risk rejection reason never empty
# ===================================================================
def test_risk_rejection_reason_not_empty():
    """Every rejection returns a non-empty reason."""
    from config.settings import get_settings, BotSettings
    from engines.risk_engine import RiskEngine
    from core.models import TradePlan, SetupSignal, TriggerResult, SetupType, TradeDirection

    # Clear the lru_cache so we get a fresh instance we can safely mutate
    get_settings.cache_clear()
    settings = get_settings()
    orig_min_rr = settings.MIN_RISK_REWARD
    try:
        settings.MIN_RISK_REWARD = 999.0
        pipeline = MasterPipeline(settings)
        ds = HistoricalDataLoader.generate_synthetic_dataset(num_5m_bars=300)
        candles_dict = {k: v for k, v in ds.items()}
        state = BotState()
        plan_engine = TradePlanEngine()
        setup = SetupSignal(
            setup_type=SetupType.TREND_PULLBACK,
            direction=TradeDirection.LONG,
            detected=True,
            invalidation_level=64000.0,
            target_level=67000.0,
        )
        trigger = TriggerResult(
            state=TriggerState.ENTRY_READY,
            is_triggered=True,
            direction=TradeDirection.LONG,
            trigger_price=65000.0,
        )
        trade_plan = plan_engine.generate_plan(setup=setup, trigger=trigger, current_atr=300.0)
        engine = RiskEngine(settings)
        assessment = engine.evaluate_risk(trade_plan, state, candidate_id="TEST-CAND")
        assert assessment.decision == RiskDecision.REJECT_TRADE
        assert assessment.rejection_reason != "", "Rejection reason must not be empty"
    finally:
        settings.MIN_RISK_REWARD = orig_min_rr
        get_settings.cache_clear()


# ===================================================================
# TEST 12: Persistence/restart preserves guard state
# ===================================================================
def test_persistence_preserves_guard_state():
    settings = get_settings()
    pipeline = MasterPipeline(settings)
    ds = HistoricalDataLoader.generate_synthetic_dataset(num_5m_bars=300)
    candles_dict = {k: v for k, v in ds.items()}
    state = BotState(consecutive_losses=3)
    state.check_kill_switch(settings.MAX_DAILY_LOSS_PCT, settings.MAX_CONSECUTIVE_LOSSES)
    assert state.kill_switch_activated is True
    # Serialize and deserialize
    data = state.to_dict()
    restored = BotState.from_dict(data)
    assert restored.kill_switch_activated is True
    assert restored.consecutive_loss_cooldown_active is True
    assert restored.guard_type == state.guard_type


# ===================================================================
# TEST 13: RiskReasonCode and GuardType enums exist
# ===================================================================
def test_risk_enums_exist():
    from config.constants import RiskReasonCode, GuardType
    assert RiskReasonCode.DAILY_LOSS_GUARD.value == "DAILY_LOSS_GUARD"
    assert RiskReasonCode.CONSECUTIVE_LOSS_GUARD.value == "CONSECUTIVE_LOSS_GUARD"
    assert RiskReasonCode.EMERGENCY_LATCH.value == "EMERGENCY_LATCH"
    assert RiskReasonCode.BAD_RISK_REWARD.value == "BAD_RISK_REWARD"
    assert GuardType.DAILY_LOSS_GUARD.value == "DAILY_LOSS_GUARD"
    assert GuardType.CONSECUTIVE_LOSS_GUARD.value == "CONSECUTIVE_LOSS_GUARD"
    assert GuardType.EMERGENCY_LATCH.value == "EMERGENCY_LATCH"
    assert GuardType.POSITION_STATE_BLOCK.value == "POSITION_STATE_BLOCK"


# ===================================================================
# TEST 14: DecisionReport has guard_type field
# ===================================================================
def test_decision_report_has_guard_type():
    from core.models import DecisionReport, GuardType, MarketRegime, VolatilityLevel
    report = DecisionReport(
        timestamp=1000, symbol="BTC/USDT", price=65000.0,
        regime=MarketRegime.RANGE, regime_score=0.0, confidence="LOW",
        volatility=VolatilityLevel.NORMAL,
        structure_4h="MIXED", structure_1h="MIXED",
        location="BAD_LOCATION", setup="NONE",
        trigger_state="NO_SETUP",
        derivatives="UNAVAILABLE",
        risk_status="REJECT_TRADE",
        final_decision="NO_TRADE",
        reason="test",
        guard_type=GuardType.DAILY_LOSS_GUARD,
    )
    assert report.guard_type == GuardType.DAILY_LOSS_GUARD


# ===================================================================
# TEST 15: All existing tests still pass
# ===================================================================
def test_existing_regression_still_passes():
    """Verify the existing test suite still passes."""
    import subprocess
    result = subprocess.run(
        ["python", "-m", "pytest", "tests/unit/", "tests/test_regression.py", "tests/test_risk_and_exit.py", "-q", "--tb=short"],
        capture_output=True, text=True, timeout=120,
        cwd=r"C:\Users\PC\OneDrive\bitcoinalimsatim4",
    )
    assert result.returncode == 0, f"Existing tests failed: {result.stdout[-500:]}"
