"""Master Pipeline orchestrating the sequential execution per Master Specification V2.1 FINAL."""

import time
from typing import Dict, List, Optional
from loguru import logger

from config.settings import BotSettings
from config.constants import (
    DataSafetyStatus,
    MarketRegime,
    StructureType,
    TradeDirection,
    DecisionStatus,
    SetupType,
    LocationQuality,
    TriggerState,
    DerivativesStatus,
    RiskDecision,
    DataSource,
    GuardType,
    RiskReasonCode,
)
from core.models import (
    Candle,
    DecisionReport,
    RiskAssessment,
    TradeRecord,
    DerivativesField,
)
from core.state import BotState
from engines.data_health import DataHealthEngine
from engines.structure_engine import MarketStructureEngine
from engines.volatility_engine import VolatilityEngine
from engines.regime_engine import MarketRegimeEngine
from engines.sr_engine import SupportResistanceEngine
from engines.location_engine import TradeLocationEngine
from engines.volume_engine import VolumeEngine
from engines.derivatives_engine import DerivativesEngine
from engines.setup_engine import SetupEngine
from engines.trigger_engine import EntryTriggerEngine
from engines.trade_plan_engine import TradePlanEngine
from engines.risk_engine import RiskEngine
from engines.exit_engine import ExitEngine
from journal.journaler import Journaler


class MasterPipeline:
    """
    Executes the strict sequential pipeline:
    DATA HEALTH
    → MARKET STRUCTURE (Dual Timestamps)
    → INDICATORS / VOLATILITY
    → MARKET REGIME (BTC Market Inputs Only)
    → SUPPORT / RESISTANCE
    → TRADE LOCATION
    → SETUP DETECTION
    → ENTRY TRIGGER
    → VOLUME & MOMENTUM CONFIRMATION
    → DERIVATIVES CONFIRMATION
    → TRADE PLAN ENGINE
    → RISK ENGINE
    → DECISION REPORT
    """

    def __init__(self, settings: BotSettings, journaler: Optional[Journaler] = None):
        self.settings = settings
        self.journaler = journaler or Journaler(settings.JOURNAL_DIR)
        self._candidate_counter = 0
        self._evaluation_counter = 0

        self.data_health_engine = DataHealthEngine()
        self.structure_engine = MarketStructureEngine(left_bars=2, right_bars=2)
        self.volatility_engine = VolatilityEngine()
        self.regime_engine = MarketRegimeEngine(self.volatility_engine)
        self.sr_engine = SupportResistanceEngine(cluster_tolerance_pct=self.settings.SR_CLUSTERING_TOLERANCE_PCT)
        self.location_engine = TradeLocationEngine(proximity_threshold_pct=self.settings.LOCATION_PROXIMITY_PCT)
        self.volume_engine = VolumeEngine(sma_period=20)
        self.derivatives_engine = DerivativesEngine(
            oi_material_change_pct=settings.DERIVATIVES_OI_MATERIAL_CHANGE_PCT,
            bearish_taker_ratio=settings.DERIVATIVES_BEARISH_TAKER_RATIO,
            bullish_taker_ratio=settings.DERIVATIVES_BULLISH_TAKER_RATIO,
        )
        self.setup_engine = SetupEngine(
            self.volume_engine,
            location_proximity_pct=settings.LOCATION_PROXIMITY_PCT,
            counter_trend_rsi_oversold=settings.COUNTER_TREND_RSI_OVERSOLD,
            counter_trend_rsi_overbought=settings.COUNTER_TREND_RSI_OVERBOUGHT,
            counter_trend_adx_veto=settings.COUNTER_TREND_ADX_VETO,
            bollinger_period=settings.BOLLINGER_BAND_PERIOD,
            bollinger_std_dev=settings.BOLLINGER_BAND_STD_DEV,
            enable_setup_b_short=settings.ENABLE_SETUP_B_SHORT,
            enable_setup_c_short=settings.ENABLE_SETUP_C_SHORT,
        )
        self.trigger_engine = EntryTriggerEngine(
            min_wick_ratio=self.settings.WICK_REJECTION_RATIO,
            min_body_ratio=self.settings.DIRECTIONAL_BODY_RATIO,
        )
        self.trade_plan_engine = TradePlanEngine(atr_buffer_factor=0.20)
        self.risk_engine = RiskEngine(settings)
        self.exit_engine = ExitEngine(
            taker_fee_pct=settings.TAKER_FEE_PCT,
            slippage_pct=settings.SLIPPAGE_PCT,
            auto_breakeven=settings.EXIT_POLICY_AUTO_BREAKEVEN,
        )

    def _next_candidate_id(self) -> str:
        self._candidate_counter += 1
        return f"CAND-{self._candidate_counter:08d}"

    def run_cycle(
        self,
        candles_dict: Dict[str, List[Candle]],
        state: BotState,
        derivatives_input: Optional[Dict] = None,
        source_health: Optional[Dict[str, str]] = None,
    ) -> DecisionReport:
        """Runs a single 5M evaluation cycle across all buffered timeframes."""
        # Simulation clock = last closed 5M candle. Wall time would freeze daily
        # metrics in backtests (day never changes) and skew live day boundaries.
        candles_5m_in = candles_dict.get("5m", [])
        now_ts = candles_5m_in[-1].timestamp if candles_5m_in else int(time.time() * 1000)
        state.reset_daily_metrics_if_new_day(now_ts)
        # Phase 2A: canonical evaluation id, minted once per run_cycle call.
        # Monotonic per pipeline lifetime; unique within a backtest run.
        self._evaluation_counter += 1
        evaluation_id = f"EV-{self._evaluation_counter:08d}"

        # STEP 1: DATA HEALTH ENGINE
        source_health = source_health or {}
        derivative_fields = derivatives_input or {}
        coinglass_has_data = any(
            isinstance(field, dict)
            and field.get("value") is not None
            and str(field.get("source", "")).upper() == "COINGLASS"
            for field in derivative_fields.values()
        )
        health_result = self.data_health_engine.evaluate_health(
            candle_dict=candles_dict,
            coinglass_available=source_health.get("coinglass") == "CONNECTED" or coinglass_has_data,
            cmc_available=source_health.get("coinmarketcap") in {"CONNECTED", "HEALTHY"},
            binance_latency_ms=150,
        )
        latest_5m = candles_dict.get("5m", [])
        current_price = latest_5m[-1].close if latest_5m else 0.0

        if health_result.overall_safety == DataSafetyStatus.UNSAFE:
            report = DecisionReport(
                timestamp=now_ts,
                evaluation_id=evaluation_id,
                price=current_price,
                regime=MarketRegime.RANGE,
                regime_score=0.0,
                confidence="LOW",
                volatility=self.volatility_engine.evaluate_volatility(candles_dict.get("4h", []))[0],
                structure_4h=StructureType.MIXED,
                structure_1h=StructureType.MIXED,
                location=LocationQuality.BAD_LOCATION,
                setup=SetupType.NONE,
                trigger_state=TriggerState.NO_SETUP,
                derivatives=DerivativesStatus.REJECT,
                risk_status=RiskDecision.REJECT_TRADE,
                final_decision=DecisionStatus.NO_TRADE,
                reason=f"DATA UNSAFE: {health_result.reason}",
                kill_switch_active=state.kill_switch_activated,
            )
            self.journaler.log_decision(report)
            return report

        candles_4h = candles_dict["4h"]
        candles_1h = candles_dict["1h"]
        candles_15m = candles_dict["15m"]
        candles_5m = candles_dict["5m"]

        # STEP 2: MARKET STRUCTURE (Dual Timestamps)
        struct_4h = self.structure_engine.analyze_structure("4h", candles_4h)
        struct_1h = self.structure_engine.analyze_structure("1h", candles_1h)
        struct_15m = self.structure_engine.analyze_structure("15m", candles_15m)

        # STEP 3: MARKET REGIME (BTC Market Inputs Only)
        regime_result = self.regime_engine.evaluate_regime(
            candles_4h=candles_4h,
            structure_4h=struct_4h,
            structure_1h=struct_1h,
            recent_regimes_4h=state.recent_regimes_4h,
        )

        # Update state regime history
        if not state.recent_regimes_4h or state.recent_regimes_4h[-1] != regime_result.regime:
            state.recent_regimes_4h.append(regime_result.regime)
            if len(state.recent_regimes_4h) > 10:
                state.recent_regimes_4h.pop(0)
        state.active_regime = regime_result.regime

        # STEP 4: SUPPORT / RESISTANCE & CONFLUENCE
        swings_4h = (struct_4h.swing_highs, struct_4h.swing_lows)
        swings_1h = (struct_1h.swing_highs, struct_1h.swing_lows)
        swings_15m = (struct_15m.swing_highs, struct_15m.swing_lows)

        zones = self.sr_engine.evaluate_sr_zones(
            current_price=current_price,
            swings_4h=swings_4h,
            swings_1h=swings_1h,
            swings_15m=swings_15m,
            candles_1h=candles_1h,
        )

        # STEP 5: TRADE LOCATION ENGINE
        location_result = self.location_engine.evaluate_location(
            current_price=current_price,
            zones=zones,
            regime_result=regime_result,
        )

        # STEP 6: SETUP DETECTION ENGINE
        setup_signal = self.setup_engine.evaluate_setups(
            regime=regime_result,
            struct_1h=struct_1h,
            candles_15m=candles_15m,
            candles_5m=candles_5m,
            location=location_result,
            zones=zones,
        )

        # STEP 7: 5M ENTRY TRIGGER ENGINE
        trigger_result = self.trigger_engine.process_trigger(
            current_state=state.trigger_state,
            setup=setup_signal,
            candles_5m=candles_5m,
            is_in_position=(state.active_position is not None),
        )
        state.trigger_state = trigger_result.state

        # STEP 8: DERIVATIVES CONFIRMATION ENGINE (With Provenance)
        deriv_data = derivatives_input or {}
        price_change_pct = (
            (candles_5m[-1].close - candles_5m[-2].close) / candles_5m[-2].close
            if len(candles_5m) >= 2
            else 0.0
        )

        # Parse field containers
        def derivative_field(name, default_source):
            raw = deriv_data.get(name)
            if isinstance(raw, dict):
                return DerivativesField(**raw)
            return DerivativesField(
                value=raw,
                source=default_source if raw is not None else DataSource.UNAVAILABLE,
            )

        derivatives_state = self.derivatives_engine.evaluate_derivatives(
            candidate_direction=setup_signal.direction,
            setup_type=setup_signal.setup_type,
            price_change_pct=price_change_pct,
            oi_field=derivative_field("open_interest", DataSource.BINANCE),
            oi_change_field=derivative_field("oi_change_pct", DataSource.BINANCE),
            funding_field=derivative_field("funding_rate", DataSource.BINANCE),
            ls_field=derivative_field("long_short_ratio", DataSource.BINANCE),
            taker_field=derivative_field("taker_buy_ratio", DataSource.BINANCE),
            liquidation_field=derivative_field("liquidations_24h", DataSource.COINGLASS),
        )
        binance_derivatives_available = any(
            isinstance(field, dict)
            and field.get("value") is not None
            and str(field.get("source", "")).upper() == "BINANCE"
            for field in deriv_data.values()
        )
        if (
            source_health.get("coinglass")
            and source_health.get("coinglass") != "CONNECTED"
            and binance_derivatives_available
            and derivatives_state.status != DerivativesStatus.REJECT
        ):
            derivatives_state.status = DerivativesStatus.DEGRADED
            derivatives_state.reason = f"{derivatives_state.reason}; CoinGlass supplemental context unavailable"

        # STEP 9: PRE-TRADE PLAN ENGINE & RISK ENGINE
        current_atr = regime_result.details.get("current_atr", current_price * 0.01)
        trade_plan = None
        risk_assessment = None
        guard_type = GuardType.OTHER_RISK_CONTROL_BLOCK
        candidate_id = ""

        if trigger_result.is_triggered and derivatives_state.status != DerivativesStatus.REJECT:
            trade_plan = self.trade_plan_engine.generate_plan(
                setup=setup_signal,
                trigger=trigger_result,
                current_atr=current_atr,
            )
            candidate_id = self._next_candidate_id()
            risk_assessment = self.risk_engine.evaluate_risk(
                trade_plan=trade_plan,
                state=state,
                candidate_id=candidate_id,
            )
            guard_type = risk_assessment.guard_type

        # STEP 10: FINAL DECISION SYNTHESIS
        final_decision = DecisionStatus.NO_TRADE
        decision_reason = ""
        effective_guard_type = guard_type

        if state.kill_switch_activated:
            final_decision = DecisionStatus.NO_TRADE
            decision_reason = f"Kill Switch Activated: {state.kill_switch_reason}"
            effective_guard_type = state.guard_type
        elif derivatives_state.status == DerivativesStatus.REJECT:
            final_decision = DecisionStatus.NO_TRADE
            decision_reason = f"Derivatives Veto: {derivatives_state.reason}"
        elif trigger_result.is_triggered and risk_assessment and risk_assessment.decision == RiskDecision.ACCEPT_TRADE:
            if setup_signal.direction == TradeDirection.LONG:
                final_decision = DecisionStatus.LONG_ENTRY
                decision_reason = f"All conditions met for LONG via {setup_signal.setup_type.value}: {trigger_result.pattern}"
            else:
                final_decision = DecisionStatus.SHORT_ENTRY
                decision_reason = f"All conditions met for SHORT via {setup_signal.setup_type.value}: {trigger_result.pattern}"
        elif setup_signal.detected:
            if setup_signal.direction == TradeDirection.LONG:
                final_decision = DecisionStatus.LONG_WATCH
                decision_reason = f"{setup_signal.setup_type.value} setup detected; waiting 5M trigger ({trigger_result.reason})"
            else:
                final_decision = DecisionStatus.SHORT_WATCH
                decision_reason = f"{setup_signal.setup_type.value} setup detected; waiting 5M trigger ({trigger_result.reason})"
        else:
            final_decision = DecisionStatus.NO_TRADE
            decision_reason = setup_signal.reason or "No valid setup found"

        report = DecisionReport(
            timestamp=candles_5m[-1].timestamp if candles_5m else now_ts,
            evaluation_id=evaluation_id,
            price=current_price,
            regime=regime_result.regime,
            regime_score=regime_result.score,
            confidence=regime_result.confidence,
            volatility=regime_result.volatility,
            vol_percentile=float(regime_result.details.get("vol_percentile", 50.0)),
            atr_distance_atrs=float(regime_result.details.get("atr_distance_atrs", 0.0)),
            current_rsi=float(regime_result.details.get("current_rsi", 50.0)),
            structure_4h=struct_4h.structure,
            structure_1h=struct_1h.structure,
            location=location_result.quality,
            setup=setup_signal.setup_type,
            setup_direction=setup_signal.direction,
            trigger_state=trigger_result.state,
            derivatives=derivatives_state.status,
            overextended_up=regime_result.overextended_up,
            overextended_down=regime_result.overextended_down,
            kill_switch_active=state.kill_switch_activated,
            guard_type=effective_guard_type if state.kill_switch_activated else guard_type,
            risk_status=risk_assessment.decision if risk_assessment else RiskDecision.REJECT_TRADE,
            final_decision=final_decision,
            reason=decision_reason,
            trade_plan=trade_plan,
            risk_assessment=risk_assessment,
        )

        self.journaler.log_decision(report)
        return report
