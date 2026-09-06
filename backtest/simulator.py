"""Backtest Simulator implementing strict zero-lookahead chronological event simulation."""

import uuid
from typing import Any, Callable, Dict, List, Optional
from loguru import logger

from core.models import Candle, TradeRecord
from config.settings import BotSettings
from config.constants import (
    DecisionStatus,
    ManagementProfile,
    MarketRegime,
    PositionManagementState,
    StructureType,
    TradeDirection,
    VolatilityLevel,
)
from core.state import BotState
from runner import MasterPipeline
from journal.metrics import MetricsCalculator
from engines.position_manager import PositionManager


class BacktestSimulator:
    """
    Simulates multi-timeframe trading chronologically with zero lookahead bias.
    Models trading friction (0.04% taker fee, 0.02% slippage).
    """

    STATIC_EXIT_BASELINE = "STATIC_EXIT_BASELINE"
    ADAPTIVE_MANAGEMENT_V1 = "ADAPTIVE_MANAGEMENT_V1"

    def __init__(self, settings: BotSettings, pipeline: Optional[MasterPipeline] = None,
                 *, management_mode: str = STATIC_EXIT_BASELINE,
                 management_context_provider: Optional[Callable[[TradeRecord, Candle], Dict[str, Any]]] = None):
        if management_mode not in {self.STATIC_EXIT_BASELINE, self.ADAPTIVE_MANAGEMENT_V1}:
            raise ValueError("Unsupported management mode")
        self.management_mode = management_mode
        dynamic_targets = management_mode == self.ADAPTIVE_MANAGEMENT_V1
        self.settings = settings.model_copy(update={"DYNAMIC_TARGETS_ENABLED": dynamic_targets})
        if pipeline is not None:
            actual = getattr(getattr(pipeline, "trade_plan_engine", None), "dynamic_targets_enabled", None)
            if actual is None or bool(actual) != dynamic_targets:
                raise ValueError("Injected pipeline is incompatible with requested management mode")
            self.pipeline = pipeline
        else:
            self.pipeline = MasterPipeline(self.settings)
        self.management_context_provider = management_context_provider
        self._management_state: Dict[str, Dict[str, Any]] = {}
        self._management_events: List[Dict[str, Any]] = []
        self.position_manager = PositionManager(
            recovery_wait_enabled=settings.RECOVERY_WAIT_ENABLED,
            early_exit_enabled=settings.EARLY_EXIT_ENABLED,
            breakeven_min_r=settings.BREAKEVEN_MIN_R,
            stop_tighten_min_r=settings.STOP_TIGHTEN_MIN_R,
            stop_lock_r=settings.STOP_LOCK_R,
            target_replan_enabled=settings.TARGET_REPLAN_ENABLED,
            target_replan_min_r=settings.TARGET_REPLAN_MIN_R,
            target_replan_cooldown_bars=settings.TARGET_REPLAN_COOLDOWN_BARS,
            max_target_replans=settings.MAX_TARGET_REPLANS,
        )

    def _state_for(self, trade: TradeRecord) -> Dict[str, Any]:
        return self._management_state.setdefault(trade.trade_id, {
            "initial_stop": trade.stop_loss,
            "current_stop": trade.stop_loss,
            "current_tp2": trade.tp2,
            "target_replan_count": 0,
            "last_target_replan_at": None,
            "mfe_r": 0.0,
            "mae_r": 0.0,
        })

    def _adaptive_decision(self, trade: TradeRecord, candle: Candle):
        """Evaluate the shared manager using only the supplied closed candle."""
        if self.management_mode != self.ADAPTIVE_MANAGEMENT_V1 or self.management_context_provider is None:
            return None
        context = dict(self.management_context_provider(trade, candle) or {})
        state = self._state_for(trade)
        risk = abs(trade.entry_price - state["initial_stop"])
        current_r = ((candle.close - trade.entry_price) if trade.direction == TradeDirection.LONG else (trade.entry_price - candle.close)) / risk if risk > 0 else 0.0
        state["mfe_r"] = max(float(state["mfe_r"]), current_r)
        state["mae_r"] = min(float(state["mae_r"]), current_r)
        if "trend" in context:
            momentum_support, momentum_opposing, momentum_available = PositionManager.normalize_momentum(
                trade.direction, context.pop("trend")
            )
        else:
            momentum_support = bool(context.pop("momentum_support", False))
            momentum_opposing = bool(context.pop("momentum_opposing", False))
            momentum_available = bool(context.pop("momentum_available", False))
        if "volume_state" in context:
            volume_support, volume_available = PositionManager.normalize_volume(context.pop("volume_state"))
        else:
            volume_support = bool(context.pop("volume_support", False))
            volume_available = bool(context.pop("volume_available", False))
        analysis_ready = (
            candle.is_closed
            and str(context.pop("frame_status", "UNAVAILABLE")).upper() == "AVAILABLE"
            and bool(context.pop("data_healthy", True))
        )
        return self.position_manager.evaluate(
            direction=trade.direction,
            entry=trade.entry_price,
            initial_stop=float(state["initial_stop"]),
            current_stop=float(state["current_stop"]),
            mark=candle.close,
            initial_size=trade.size_btc,
            current_size=float(context.pop("current_size", trade.size_btc)),
            structure=context.pop("structure", StructureType.MIXED),
            last_bos=context.pop("last_bos", None),
            last_choch=context.pop("last_choch", None),
            regime=context.pop("regime", MarketRegime.RANGE),
            volatility=context.pop("volatility", VolatilityLevel.NORMAL),
            momentum_support=momentum_support,
            momentum_opposing=momentum_opposing,
            momentum_available=momentum_available,
            volume_support=volume_support,
            volume_available=volume_available,
            data_healthy=analysis_ready,
            candle_closed=candle.is_closed,
            candle_timestamp=candle.timestamp,
            current_tp2=float(state["current_tp2"]),
            candidate_tp2=context.pop("candidate_tp2", None),
            target_replan_count=int(state["target_replan_count"]),
            last_target_replan_at=state["last_target_replan_at"],
            mfe_r=float(state["mfe_r"]),
            mae_r=float(state["mae_r"]),
            management_profile=context.pop("management_profile", ManagementProfile.BALANCED),
        )

    def _apply_adaptive_transition(self, trade: TradeRecord, decision) -> None:
        """Apply pure-manager protection changes to future backtest candles."""
        state = self._state_for(trade)
        action = {"trade_id": trade.trade_id, "state": decision.state.value, "reason_codes": list(decision.reason_codes)}
        if decision.stop_action.get("action") == "TIGHTEN_STOP":
            new_stop = float(decision.stop_action["new_stop"])
            safe = new_stop >= state["current_stop"] and new_stop >= state["initial_stop"] if trade.direction == TradeDirection.LONG else new_stop <= state["current_stop"] and new_stop <= state["initial_stop"]
            if not safe:
                raise ValueError("Adaptive backtest attempted to widen stop")
            trade.stop_loss = new_stop
            state["current_stop"] = new_stop
            action["stop_loss"] = new_stop
        if decision.target_action.get("action") == "REPLACE_TP2":
            new_tp2 = float(decision.target_action["new_tp2"])
            valid = new_tp2 >= state["current_tp2"] if trade.direction == TradeDirection.LONG else new_tp2 <= state["current_tp2"]
            if not valid:
                raise ValueError("Adaptive backtest attempted invalid TP2 replan")
            trade.tp2 = new_tp2
            state["current_tp2"] = new_tp2
            state["target_replan_count"] = decision.target_replan_count
            state["last_target_replan_at"] = decision.last_target_replan_at
            action["tp2"] = new_tp2
        self._management_events.append(action)

    def _finalize_trade(self, trade: TradeRecord, timestamp: int, price: float, reason: str) -> TradeRecord:
        initial_stop = float(self._state_for(trade)["initial_stop"])
        finalized = self.pipeline.exit_engine.finalize_trade(trade, timestamp, price, reason)
        initial_risk = abs(finalized.entry_price - initial_stop) * finalized.size_btc
        finalized.r_multiple = round(finalized.pnl_usdt / initial_risk, 2) if initial_risk > 0 else 0.0
        return finalized

    def run(
        self,
        dataset: Dict[str, List[Candle]],
        start_idx: int = 100,
    ) -> Dict:
        """
        Executes chronological backtest over the dataset.
        start_idx ensures warmup for 4H indicators (EMA200, ATR14, etc.).
        """
        candles_5m = dataset["5m"]
        candles_15m = dataset["15m"]
        candles_1h = dataset["1h"]
        candles_4h = dataset["4h"]

        state = BotState(
            account_balance_usdt=self.settings.INITIAL_CAPITAL_USDT,
            start_of_day_balance_usdt=self.settings.INITIAL_CAPITAL_USDT,
        )

        completed_trades: List[TradeRecord] = []
        n_5m = len(candles_5m)

        logger.info(f"Starting backtest across {n_5m} 5M candles (Warmup: {start_idx} bars)...")

        for i in range(start_idx, n_5m):
            curr_5m = candles_5m[i]
            current_ts = curr_5m.timestamp

            # 1. Update active position if one exists
            if state.active_position is not None:
                trade = state.active_position
                stop_before_exit_check = trade.stop_loss
                is_closed, reason, exit_price = self.pipeline.exit_engine.evaluate_exit(trade, curr_5m)
                if self.management_mode == self.ADAPTIVE_MANAGEMENT_V1 and trade.stop_loss != stop_before_exit_check:
                    management_state = self._state_for(trade)
                    management_state["current_stop"] = trade.stop_loss
                    self._management_events.append({
                        "trade_id": trade.trade_id,
                        "state": "STOP_TIGHTENED",
                        "reason_codes": ["LEGACY_TP1_BREAKEVEN_POLICY"],
                        "stop_loss": trade.stop_loss,
                    })
                if is_closed and reason:
                    finalized = self._finalize_trade(trade, curr_5m.timestamp, exit_price, reason)
                    state.register_trade_closed(finalized)
                    completed_trades.append(finalized)
                elif self.management_mode == self.ADAPTIVE_MANAGEMENT_V1:
                    management = self._adaptive_decision(trade, curr_5m)
                    if management:
                        if management.state == PositionManagementState.EXIT_EARLY:
                            finalized = self._finalize_trade(trade, curr_5m.timestamp, curr_5m.close, "ADAPTIVE_EARLY_EXIT")
                            state.register_trade_closed(finalized)
                            completed_trades.append(finalized)
                        else:
                            self._apply_adaptive_transition(trade, management)

            # 2. Slice strictly past closed candles up to current_ts (Zero Lookahead Guarantee)
            sub_5m = candles_5m[: i + 1]
            sub_15m = [c for c in candles_15m if (c.timestamp + 15 * 60 * 1000) <= (current_ts + 5 * 60 * 1000)]
            sub_1h = [c for c in candles_1h if (c.timestamp + 60 * 60 * 1000) <= (current_ts + 5 * 60 * 1000)]
            sub_4h = [c for c in candles_4h if (c.timestamp + 4 * 60 * 60 * 1000) <= (current_ts + 5 * 60 * 1000)]

            # Check minimum requirements for indicators (warmup check)
            if len(sub_4h) < 45 or len(sub_1h) < 35 or len(sub_15m) < 35 or len(sub_5m) < 35:
                continue

            candles_dict = {
                "5m": sub_5m[-150:],
                "15m": sub_15m[-150:],
                "1h": sub_1h[-150:],
                "4h": sub_4h[-250:],
            }

            # Derivatives ALWAYS UNAVAILABLE for Phase 1 baseline
            derivatives_input = {}

            # Run master decision cycle
            report = self.pipeline.run_cycle(candles_dict, state, derivatives_input=derivatives_input)

            # 4. Process new entry if triggered and no active position
            if state.active_position is None and report.risk_assessment:
                if report.final_decision in [DecisionStatus.LONG_ENTRY, DecisionStatus.SHORT_ENTRY]:
                    risk = report.risk_assessment
                    direction = (
                        TradeDirection.LONG
                        if report.final_decision == DecisionStatus.LONG_ENTRY
                        else TradeDirection.SHORT
                    )

                    new_trade = TradeRecord(
                        trade_id=f"BT-{uuid.uuid4().hex[:6]}",
                        symbol=report.symbol,
                        setup_type=report.setup,
                        direction=direction,
                        entry_time=curr_5m.timestamp,
                        entry_price=curr_5m.close,
                        stop_loss=risk.stop_loss,
                        tp1=risk.tp1,
                        tp2=risk.tp2,
                        size_btc=risk.position_size_btc,
                        size_usdt=risk.position_size_usdt,
                        is_closed=False,
                    )
                    state.active_position = new_trade
                    self._state_for(new_trade)

        # Force close any trailing position at final candle close
        if state.active_position is not None:
            last_c = candles_5m[-1]
            trade = state.active_position
            finalized = self._finalize_trade(trade, last_c.timestamp, last_c.close, "BACKTEST_END")
            state.register_trade_closed(finalized)
            completed_trades.append(finalized)

        # 5. Compute metrics
        results = MetricsCalculator.calculate_metrics(completed_trades, self.settings.INITIAL_CAPITAL_USDT)
        results["final_balance_usdt"] = round(state.account_balance_usdt, 2)
        results["completed_trades_list"] = completed_trades
        results["management_mode"] = self.management_mode
        results["management_events"] = list(self._management_events)
        results["partial_take_profit"] = "NOT_ACTIVE_IN_POSITION_MANAGER_V1"

        logger.info(
            f"Backtest Finished. Total Trades: {results['total_trades']} | "
            f"Win Rate: {results['win_rate_pct']}% | Net PnL: ${results['net_pnl_usdt']} | "
            f"Max DD: {results['max_drawdown_pct']}% | Profit Factor: {results['profit_factor']}"
        )

        return results
