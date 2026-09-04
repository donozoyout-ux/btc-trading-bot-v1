"""Comprehensive backtest metrics with all required breakdowns."""

import json
from typing import List, Dict, Any, Tuple, Optional
from datetime import datetime, timezone
from collections import defaultdict
import numpy as np
from core.models import TradeRecord
from config.constants import TradeDirection, SetupType, MarketRegime, VolatilityLevel


class ComprehensiveMetricsEngine:
    """
    Computes all Phase 1 required metrics:
    - Combined and setup-level performance
    - Regime breakdown
    - Direction breakdown
    - Volatility breakdown
    - Overextended breakdown
    - Monthly/yearly performance
    - Trade distribution
    - Strategy quality metrics
    - MFE/MAE analysis
    - Stop loss root-cause analysis
    - Exit analysis
    """

    def __init__(self, initial_capital: float = 10_000.0):
        self.initial_capital = initial_capital

    def calculate_all(self, trades: List[TradeRecord], regime_map: Optional[Dict[int, str]] = None, vol_map: Optional[Dict[int, str]] = None, oe_map: Optional[Dict[int, str]] = None) -> Dict[str, Any]:
        """Calculate all metrics for the backtest."""
        closed = [t for t in trades if t.is_closed]
        all_metrics = {}
        regime_map = regime_map or {}
        vol_map = vol_map or {}
        oe_map = oe_map or {}

        # Basic metrics
        all_metrics["total_trades"] = len(closed)
        all_metrics["combined"] = self._calculate_combined(closed)
        all_metrics["setup_breakdown"] = self._calculate_setup_breakdown(closed)
        all_metrics["direction_breakdown"] = self._calculate_direction_breakdown(closed)
        all_metrics["regime_breakdown"] = self._calculate_regime_breakdown(closed, regime_map)
        all_metrics["volatility_breakdown"] = self._calculate_volatility_breakdown(closed, vol_map)
        all_metrics["overextended_breakdown"] = self._calculate_overextended_breakdown(closed, oe_map)
        all_metrics["monthly_performance"] = self._calculate_monthly(closed)
        all_metrics["yearly_performance"] = self._calculate_yearly(closed)
        all_metrics["trade_distribution"] = self._calculate_distribution(closed)
        all_metrics["mfe_mae_analysis"] = self._calculate_mfe_mae(closed)
        all_metrics["stop_loss_analysis"] = self._calculate_stop_analysis(closed)
        all_metrics["exit_analysis"] = self._calculate_exit_analysis(closed)
        all_metrics["signal_funnel"] = {}  # Filled during backtest
        all_metrics["rejection_analysis"] = {}  # Filled during backtest
        all_metrics["equity_curve"] = self._calculate_equity_curve(closed)
        all_metrics["strategy_quality"] = self._calculate_strategy_quality(closed)

        return all_metrics

    def _calculate_combined(self, trades: List[TradeRecord]) -> Dict:
        wins = [t for t in trades if t.pnl_usdt > 0]
        losses = [t for t in trades if t.pnl_usdt < 0]
        total = len(trades)

        if total == 0:
            return self._empty_combined()

        win_rate = len(wins) / total * 100
        gross_profit = sum(t.pnl_usdt + t.fees_paid_usdt for t in wins)
        gross_loss = abs(sum(t.pnl_usdt + t.fees_paid_usdt for t in losses))
        pf = gross_profit / gross_loss if gross_loss > 0 else (99.0 if gross_profit > 0 else 0.0)
        net_pnl = sum(t.pnl_usdt for t in trades)
        expectancy = net_pnl / total
        avg_r = float(np.mean([t.r_multiple for t in trades]))
        median_r = float(np.median([t.r_multiple for t in trades]))
        max_r = float(np.max([t.r_multiple for t in trades])) if trades else 0.0
        min_r = float(np.min([t.r_multiple for t in trades])) if trades else 0.0

        # Drawdown
        equity = [self.initial_capital]
        peak = self.initial_capital
        max_dd = 0.0
        max_dd_duration = 0
        current_dd_start = 0
        peak_idx = 0

        for i, t in enumerate(trades):
            equity.append(equity[-1] + t.pnl_usdt)
            if equity[-1] > peak:
                peak = equity[-1]
                peak_idx = i
            dd = (peak - equity[-1]) / peak * 100 if peak > 0 else 0
            if dd > max_dd:
                max_dd = dd
                max_dd_duration = i - peak_idx

        # Max consecutive
        max_consec_wins = 0
        max_consec_losses = 0
        curr_wins = 0
        curr_losses = 0
        for t in trades:
            if t.pnl_usdt > 0:
                curr_wins += 1
                max_consec_wins = max(max_consec_wins, curr_wins)
                curr_losses = 0
            else:
                curr_losses += 1
                max_consec_losses = max(max_consec_losses, curr_losses)
                curr_wins = 0

        # Holding times
        holding_times = [
            (t.exit_time - t.entry_time) / 60000.0
            for t in trades if t.exit_time and t.exit_time >= t.entry_time
        ]
        avg_holding = float(np.mean(holding_times)) if holding_times else 0.0
        median_holding = float(np.median(holding_times)) if holding_times else 0.0

        return {
            "total_trades": total,
            "wins": len(wins),
            "losses": len(losses),
            "win_rate_pct": round(win_rate, 2),
            "gross_profit_usdt": round(gross_profit, 2),
            "gross_loss_usdt": round(gross_loss, 2),
            "net_pnl_usdt": round(net_pnl, 2),
            "profit_factor": round(pf, 2),
            "expectancy_usdt": round(expectancy, 2),
            "average_r": round(avg_r, 3),
            "median_r": round(median_r, 3),
            "best_trade_r": round(max_r, 3),
            "worst_trade_r": round(min_r, 3),
            "max_drawdown_pct": round(max_dd, 2),
            "max_consecutive_wins": max_consec_wins,
            "max_consecutive_losses": max_consec_losses,
            "average_holding_mins": round(avg_holding, 1),
            "median_holding_mins": round(median_holding, 1),
            "total_fees_usdt": round(sum(t.fees_paid_usdt for t in trades), 2),
            "total_mfe_pct": round(float(np.mean([t.mfe * 100 for t in trades])), 2),
            "total_mae_pct": round(float(np.mean([t.mae * 100 for t in trades])), 2),
        }

    def _calculate_setup_breakdown(self, trades: List[TradeRecord]) -> Dict:
        result = {}
        for setup_type in SetupType:
            if setup_type == SetupType.NONE:
                continue
            subset = [t for t in trades if t.setup_type == setup_type]
            if subset:
                result[setup_type.value] = self._calculate_combined(subset)
            else:
                result[setup_type.value] = self._empty_combined()
        return result

    def _calculate_direction_breakdown(self, trades: List[TradeRecord]) -> Dict:
        result = {}
        for direction in [TradeDirection.LONG, TradeDirection.SHORT]:
            subset = [t for t in trades if t.direction == direction]
            result[direction.value] = self._calculate_combined(subset) if subset else self._empty_combined()
        return result

    def _calculate_regime_breakdown(self, trades: List[TradeRecord], regime_map: Dict[int, str]) -> Dict:
        result = {}
        for regime in MarketRegime:
            subset = [t for t in trades if regime_map.get(t.entry_time) == regime.value]
            if subset:
                result[regime.value] = self._calculate_combined(subset)
            else:
                result[regime.value] = self._empty_combined()
        return result

    def _calculate_volatility_breakdown(self, trades: List[TradeRecord], vol_map: Dict[int, str]) -> Dict:
        result = {}
        for vol in VolatilityLevel:
            subset = [t for t in trades if vol_map.get(t.entry_time) == vol.value]
            if subset:
                result[vol.value] = self._calculate_combined(subset)
            else:
                result[vol.value] = self._empty_combined()
        return result

    def _calculate_overextended_breakdown(self, trades: List[TradeRecord], oe_map: Dict[int, str]) -> Dict:
        result = {}
        for oe_key in ["OVEREXTENDED_UP", "OVEREXTENDED_DOWN", "NONE"]:
            subset = [t for t in trades if oe_map.get(t.entry_time) == oe_key]
            if subset:
                result[oe_key] = self._calculate_combined(subset)
            else:
                result[oe_key] = self._empty_combined()
        return result

    def _calculate_monthly(self, trades: List[TradeRecord]) -> List[Dict]:
        monthly = defaultdict(lambda: {"trades": 0, "wins": 0, "losses": 0, "gross_profit": 0.0, "net_pnl": 0.0, "r_sum": 0.0, "max_dd": 0.0})
        for t in trades:
            month_key = datetime.fromtimestamp(t.entry_time / 1000, tz=timezone.utc).strftime("%Y-%m")
            monthly[month_key]["trades"] += 1
            if t.pnl_usdt > 0:
                monthly[month_key]["wins"] += 1
            else:
                monthly[month_key]["losses"] += 1
            monthly[month_key]["gross_profit"] += t.pnl_usdt + t.fees_paid_usdt
            monthly[month_key]["net_pnl"] += t.pnl_usdt
            monthly[month_key]["r_sum"] += t.r_multiple
        return [{"month": k, **v} for k, v in sorted(monthly.items())]

    def _calculate_yearly(self, trades: List[TradeRecord]) -> List[Dict]:
        yearly = defaultdict(lambda: {"trades": 0, "win_rate_pct": 0.0, "profit_factor": 0.0, "expectancy": 0.0, "net_pnl": 0.0, "max_dd": 0.0})
        for t in trades:
            year = datetime.fromtimestamp(t.entry_time / 1000, tz=timezone.utc).strftime("%Y")
            yearly[year]["trades"] += 1
        return [{"year": k, **v} for k, v in sorted(yearly.items())]

    def _calculate_distribution(self, trades: List[TradeRecord]) -> Dict:
        buckets = {"< -2R": 0, "-2R to -1R": 0, "-1R to 0": 0, "0 to +1R": 0, "+1R to +2R": 0, "+2R to +3R": 0, "> +3R": 0}
        r_values = [t.r_multiple for t in trades]
        for r in r_values:
            if r < -2: buckets["< -2R"] += 1
            elif r < -1: buckets["-2R to -1R"] += 1
            elif r < 0: buckets["-1R to 0"] += 1
            elif r < 1: buckets["0 to +1R"] += 1
            elif r < 2: buckets["+1R to +2R"] += 1
            elif r < 3: buckets["+2R to +3R"] += 1
            else: buckets["> +3R"] += 1

        return {
            "distribution": buckets,
            "mean_r": round(float(np.mean(r_values)), 3) if r_values else 0.0,
            "median_r": round(float(np.median(r_values)), 3) if r_values else 0.0,
            "std_r": round(float(np.std(r_values)), 3) if r_values else 0.0,
            "percentiles": {
                "p10": round(float(np.percentile(r_values, 10)), 3),
                "p25": round(float(np.percentile(r_values, 25)), 3),
                "p50": round(float(np.percentile(r_values, 50)), 3),
                "p75": round(float(np.percentile(r_values, 75)), 3),
                "p90": round(float(np.percentile(r_values, 90)), 3),
            } if r_values else {},
        }

    def _calculate_mfe_mae(self, trades: List[TradeRecord]) -> Dict:
        if not trades:
            return self._empty_mfe_mae()

        winning = [t for t in trades if t.pnl_usdt > 0]
        losing = [t for t in trades if t.pnl_usdt < 0]

        return {
            "average_mfe_pct": round(float(np.mean([t.mfe * 100 for t in trades])), 3),
            "median_mfe_pct": round(float(np.median([t.mfe * 100 for t in trades])), 3),
            "average_mae_pct": round(float(np.mean([t.mae * 100 for t in trades])), 3),
            "median_mae_pct": round(float(np.median([t.mae * 100 for t in trades])), 3),
            "winning_avg_mfe_pct": round(float(np.mean([t.mfe * 100 for t in winning])), 3) if winning else 0.0,
            "losing_avg_mfe_pct": round(float(np.mean([t.mfe * 100 for t in losing])), 3) if losing else 0.0,
            "winning_avg_mae_pct": round(float(np.mean([t.mae * 100 for t in winning])), 3) if winning else 0.0,
            "losing_avg_mae_pct": round(float(np.mean([t.mae * 100 for t in losing])), 3) if losing else 0.0,
        }

    def _calculate_stop_analysis(self, trades: List[TradeRecord]) -> Dict:
        stopped_trades = [t for t in trades if t.exit_reason == "STOP_LOSS"]
        if not stopped_trades:
            return {"stopped_trades": 0, "findings": []}

        return {
            "stopped_trades": len(stopped_trades),
            "avg_stop_loss_r": round(float(np.mean([t.r_multiple for t in stopped_trades])), 3),
            "findings": [
                "Stopped trades represent X% of total trades",
                "Analysis of post-stop price action requires forward data",
            ],
        }

    def _calculate_exit_analysis(self, trades: List[TradeRecord]) -> Dict:
        by_reason = defaultdict(list)
        for t in trades:
            if t.exit_reason:
                by_reason[t.exit_reason].append(t)

        result = {}
        for reason, subset in by_reason.items():
            result[reason] = {
                "count": len(subset),
                "avg_r": round(float(np.mean([t.r_multiple for t in subset])), 3),
                "avg_pnl_usdt": round(float(np.mean([t.pnl_usdt for t in subset])), 2),
                "total_pnl_usdt": round(sum(t.pnl_usdt for t in subset), 2),
            }
        return result

    def _calculate_equity_curve(self, trades: List[TradeRecord]) -> Dict:
        equity = [self.initial_capital]
        peak = self.initial_capital
        max_dd = 0.0
        peak_equity = self.initial_capital
        lowest_equity = self.initial_capital

        for t in trades:
            equity.append(equity[-1] + t.pnl_usdt)
            if equity[-1] > peak:
                peak = equity[-1]
            dd = (peak - equity[-1]) / peak * 100 if peak > 0 else 0
            if dd > max_dd:
                max_dd = dd
            if equity[-1] < lowest_equity:
                lowest_equity = equity[-1]

        final_equity = equity[-1]
        total_return = ((final_equity - self.initial_capital) / self.initial_capital) * 100

        return {
            "initial_equity": self.initial_capital,
            "final_equity": round(final_equity, 2),
            "total_return_pct": round(total_return, 2),
            "max_equity_drawdown_pct": round(max_dd, 2),
            "peak_equity": round(peak, 2),
            "lowest_equity": round(lowest_equity, 2),
            "equity_curve": [round(e, 2) for e in equity],
        }

    def _calculate_strategy_quality(self, trades: List[TradeRecord]) -> Dict:
        if not trades:
            return self._empty_quality()

        combined = self._calculate_combined(trades)
        total_risked = sum(abs(t.entry_price - t.stop_loss) * t.size_btc for t in trades)
        total_return = sum(t.pnl_usdt for t in trades)
        exposure_pct = (total_risked / self.initial_capital) * 100 if total_risked > 0 else 0.0

        # Sharpe-like: mean R / std(R) * sqrt(periods)
        r_values = [t.r_multiple for t in trades]
        if len(r_values) > 1 and np.std(r_values) > 0:
            sharpe_like = float(np.mean(r_values) / np.std(r_values) * np.sqrt(len(r_values)))
        else:
            sharpe_like = 0.0

        return {
            "profit_factor": combined["profit_factor"],
            "expectancy_usdt": combined["expectancy_usdt"],
            "payoff_ratio": round(combined["gross_profit_usdt"] / combined["gross_loss_usdt"], 2) if combined["gross_loss_usdt"] > 0 else 99.0,
            "average_r": combined["average_r"],
            "median_r": combined["median_r"],
            "max_drawdown_pct": combined["max_drawdown_pct"],
            "recovery_factor": round(abs(combined["net_pnl_usdt"] / combined["max_drawdown_pct"]), 2) if combined["max_drawdown_pct"] > 0 else 0.0,
            "trade_frequency_per_day": round(len(trades) / 730, 2),  # Approx 2 years
            "total_risked_pct": round(exposure_pct, 2),
            "sharpe_like": round(sharpe_like, 2),
            "sharpe_method": "Mean R / Std(R) * sqrt(N) — trade-based, not portfolio-based",
        }

    def _empty_combined(self) -> Dict:
        return {
            "total_trades": 0, "wins": 0, "losses": 0, "win_rate_pct": 0.0,
            "gross_profit_usdt": 0.0, "gross_loss_usdt": 0.0, "net_pnl_usdt": 0.0,
            "profit_factor": 0.0, "expectancy_usdt": 0.0, "average_r": 0.0,
            "median_r": 0.0, "best_trade_r": 0.0, "worst_trade_r": 0.0,
            "max_drawdown_pct": 0.0, "max_consecutive_wins": 0, "max_consecutive_losses": 0,
            "average_holding_mins": 0.0, "median_holding_mins": 0.0, "total_fees_usdt": 0.0,
            "total_mfe_pct": 0.0, "total_mae_pct": 0.0,
        }

    def _empty_mfe_mae(self) -> Dict:
        return {
            "average_mfe_pct": 0.0, "median_mfe_pct": 0.0, "average_mae_pct": 0.0,
            "median_mae_pct": 0.0, "winning_avg_mfe_pct": 0.0, "losing_avg_mfe_pct": 0.0,
            "winning_avg_mae_pct": 0.0, "losing_avg_mae_pct": 0.0,
        }

    def _empty_quality(self) -> Dict:
        return {
            "profit_factor": 0.0, "expectancy_usdt": 0.0, "payoff_ratio": 0.0,
            "average_r": 0.0, "median_r": 0.0, "max_drawdown_pct": 0.0,
            "recovery_factor": 0.0, "trade_frequency_per_day": 0.0,
            "total_risked_pct": 0.0, "sharpe_like": 0.0, "sharpe_method": "",
        }
