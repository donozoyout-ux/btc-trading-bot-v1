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
        self.settings = settings
        self.pipeline = pipeline or MasterPipeline(settings)
        if management_mode not in {self.STATIC_EXIT_BASELINE, self.ADAPTIVE_MANAGEMENT_V1}:
            raise ValueError("Unsupported management mode")
        self.management_mode = management_mode
        self.management_context_provider = management_context_provider
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

    def _adaptive_decision(self, trade: TradeRecord, candle: Candle):
        """Evaluate the shared manager using only the supplied closed candle."""
        if self.management_mode != self.ADAPTIVE_MANAGEMENT_V1 or self.management_context_provider is None:
            return None
        context = dict(self.management_context_provider(trade, candle) or {})
        return self.position_manager.evaluate(
            direction=trade.direction,
            entry=trade.entry_price,
            initial_stop=trade.stop_loss,
            current_stop=float(context.pop("current_stop", trade.stop_loss)),
            mark=candle.close,
            initial_size=trade.size_btc,
            current_size=float(context.pop("current_size", trade.size_btc)),
            structure=context.pop("structure", StructureType.MIXED),
            last_bos=context.pop("last_bos", None),
            last_choch=context.pop("last_choch", None),
            regime=context.pop("regime", MarketRegime.RANGE),
            volatility=context.pop("volatility", VolatilityLevel.NORMAL),
            momentum_support=bool(context.pop("momentum_support", True)),
            volume_support=bool(context.pop("volume_support", True)),
            data_healthy=bool(context.pop("data_healthy", True)),
            candle_closed=True,
            candle_timestamp=candle.timestamp,
            current_tp2=float(context.pop("current_tp2", trade.tp2)),
            candidate_tp2=context.pop("candidate_tp2", None),
            target_replan_count=int(context.pop("target_replan_count", 0)),
            last_target_replan_at=context.pop("last_target_replan_at", None),
            management_profile=context.pop("management_profile", ManagementProfile.BALANCED),
        )

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
                is_closed, reason, exit_price = self.pipeline.exit_engine.evaluate_exit(trade, curr_5m)
                if is_closed and reason:
                    finalized = self.pipeline.exit_engine.finalize_trade(
                        trade, curr_5m.timestamp, exit_price, reason
                    )
                    state.register_trade_closed(finalized)
                    completed_trades.append(finalized)
                elif self.management_mode == self.ADAPTIVE_MANAGEMENT_V1:
                    management = self._adaptive_decision(trade, curr_5m)
                    if management and management.state == PositionManagementState.EXIT_EARLY:
                        finalized = self.pipeline.exit_engine.finalize_trade(
                            trade, curr_5m.timestamp, curr_5m.close, "ADAPTIVE_EARLY_EXIT"
                        )
                        state.register_trade_closed(finalized)
                        completed_trades.append(finalized)

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

        # Force close any trailing position at final candle close
        if state.active_position is not None:
            last_c = candles_5m[-1]
            trade = state.active_position
            finalized = self.pipeline.exit_engine.finalize_trade(
                trade, last_c.timestamp, last_c.close, "BACKTEST_END"
            )
            state.register_trade_closed(finalized)
            completed_trades.append(finalized)

        # 5. Compute metrics
        results = MetricsCalculator.calculate_metrics(completed_trades, self.settings.INITIAL_CAPITAL_USDT)
        results["final_balance_usdt"] = round(state.account_balance_usdt, 2)
        results["completed_trades_list"] = completed_trades
        results["management_mode"] = self.management_mode

        logger.info(
            f"Backtest Finished. Total Trades: {results['total_trades']} | "
            f"Win Rate: {results['win_rate_pct']}% | Net PnL: ${results['net_pnl_usdt']} | "
            f"Max DD: {results['max_drawdown_pct']}% | Profit Factor: {results['profit_factor']}"
        )

        return results
