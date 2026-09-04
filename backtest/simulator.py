"""Backtest Simulator implementing strict zero-lookahead chronological event simulation."""

import uuid
from typing import Dict, List, Optional
from loguru import logger

from core.models import Candle, TradeRecord
from config.settings import BotSettings
from config.constants import DecisionStatus, TradeDirection
from core.state import BotState
from runner import MasterPipeline
from journal.metrics import MetricsCalculator


class BacktestSimulator:
    """
    Simulates multi-timeframe trading chronologically with zero lookahead bias.
    Models trading friction (0.04% taker fee, 0.02% slippage).
    """

    def __init__(self, settings: BotSettings, pipeline: Optional[MasterPipeline] = None):
        self.settings = settings
        self.pipeline = pipeline or MasterPipeline(settings)

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

        logger.info(
            f"Backtest Finished. Total Trades: {results['total_trades']} | "
            f"Win Rate: {results['win_rate_pct']}% | Net PnL: ${results['net_pnl_usdt']} | "
            f"Max DD: {results['max_drawdown_pct']}% | Profit Factor: {results['profit_factor']}"
        )

        return results
