"""Comprehensive Performance Metrics Engine computing Section 46 mandatory statistics."""

from typing import List, Dict, Any
import numpy as np
from core.models import TradeRecord
from config.constants import TradeDirection, SetupType


class MetricsCalculator:
    """
    Computes standard institutional performance metrics:
    Total trades, Win rate, Profit factor, Expectancy, Average R, Net/Gross PnL,
    Fees, Max Drawdown, Consecutive losses, MFE, MAE, and breakdowns by setup/direction.
    """

    @staticmethod
    def calculate_metrics(trades: List[TradeRecord], initial_capital: float = 10_000.0) -> Dict[str, Any]:
        """Calculates comprehensive trading performance metrics."""
        closed_trades = [t for t in trades if t.is_closed]
        total_trades = len(closed_trades)

        if total_trades == 0:
            return {
                "total_trades": 0,
                "win_rate_pct": 0.0,
                "profit_factor": 0.0,
                "expectancy_usdt": 0.0,
                "average_r": 0.0,
                "net_pnl_usdt": 0.0,
                "gross_pnl_usdt": 0.0,
                "total_fees_usdt": 0.0,
                "max_drawdown_pct": 0.0,
                "max_consecutive_losses": 0,
                "average_holding_time_mins": 0.0,
                "avg_mfe_pct": 0.0,
                "avg_mae_pct": 0.0,
                "breakdowns": {},
            }

        wins = [t for t in closed_trades if t.pnl_usdt > 0]
        losses = [t for t in closed_trades if t.pnl_usdt < 0]

        win_count = len(wins)
        loss_count = len(losses)
        win_rate = (win_count / total_trades) * 100.0

        gross_profit = sum(t.pnl_usdt + t.fees_paid_usdt for t in wins)
        gross_loss = abs(sum(t.pnl_usdt + t.fees_paid_usdt for t in losses))
        profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else (99.0 if gross_profit > 0 else 0.0)

        total_fees = sum(t.fees_paid_usdt for t in closed_trades)
        net_pnl = sum(t.pnl_usdt for t in closed_trades)
        gross_pnl = gross_profit - gross_loss

        expectancy = net_pnl / total_trades
        avg_r = float(np.mean([t.r_multiple for t in closed_trades])) if closed_trades else 0.0

        # Drawdown calculation
        equity_curve = [initial_capital]
        peak = initial_capital
        max_dd_pct = 0.0

        for t in closed_trades:
            new_equity = equity_curve[-1] + t.pnl_usdt
            equity_curve.append(new_equity)
            if new_equity > peak:
                peak = new_equity
            dd_pct = ((peak - new_equity) / peak) * 100.0 if peak > 0 else 0.0
            if dd_pct > max_dd_pct:
                max_dd_pct = dd_pct

        # Consecutive losses
        max_consec_losses = 0
        curr_consec = 0
        for t in closed_trades:
            if t.pnl_usdt < 0:
                curr_consec += 1
                max_consec_losses = max(max_consec_losses, curr_consec)
            else:
                curr_consec = 0

        # Holding times (milliseconds to minutes)
        holding_times_min = [
            ((t.exit_time - t.entry_time) / 60000.0)
            for t in closed_trades
            if t.exit_time and t.exit_time >= t.entry_time
        ]
        avg_holding = float(np.mean(holding_times_min)) if holding_times_min else 0.0

        avg_mfe = float(np.mean([t.mfe * 100.0 for t in closed_trades])) if closed_trades else 0.0
        avg_mae = float(np.mean([t.mae * 100.0 for t in closed_trades])) if closed_trades else 0.0

        # Breakdowns
        breakdowns = {
            "long": MetricsCalculator._calculate_subset(
                [t for t in closed_trades if t.direction == TradeDirection.LONG]
            ),
            "short": MetricsCalculator._calculate_subset(
                [t for t in closed_trades if t.direction == TradeDirection.SHORT]
            ),
            "trend_pullback": MetricsCalculator._calculate_subset(
                [t for t in closed_trades if t.setup_type == SetupType.TREND_PULLBACK]
            ),
            "breakout_retest": MetricsCalculator._calculate_subset(
                [t for t in closed_trades if t.setup_type == SetupType.BREAKOUT_RETEST]
            ),
            "counter_trend": MetricsCalculator._calculate_subset(
                [t for t in closed_trades if t.setup_type == SetupType.COUNTER_TREND_REACTION]
            ),
        }

        return {
            "total_trades": total_trades,
            "win_rate_pct": round(win_rate, 2),
            "profit_factor": round(profit_factor, 2),
            "expectancy_usdt": round(expectancy, 2),
            "average_r": round(avg_r, 2),
            "net_pnl_usdt": round(net_pnl, 2),
            "gross_pnl_usdt": round(gross_pnl, 2),
            "total_fees_usdt": round(total_fees, 2),
            "max_drawdown_pct": round(max_dd_pct, 2),
            "max_consecutive_losses": max_consec_losses,
            "average_holding_time_mins": round(avg_holding, 1),
            "avg_mfe_pct": round(avg_mfe, 2),
            "avg_mae_pct": round(avg_mae, 2),
            "breakdowns": breakdowns,
        }

    @staticmethod
    def _calculate_subset(subset: List[TradeRecord]) -> Dict[str, Any]:
        """Helper for breakdown subsets."""
        n = len(subset)
        if n == 0:
            return {"trades": 0, "win_rate_pct": 0.0, "net_pnl": 0.0, "profit_factor": 0.0}
        wins = [t for t in subset if t.pnl_usdt > 0]
        losses = [t for t in subset if t.pnl_usdt < 0]
        net_pnl = sum(t.pnl_usdt for t in subset)
        gp = sum(t.pnl_usdt for t in wins)
        gl = abs(sum(t.pnl_usdt for t in losses))
        pf = (gp / gl) if gl > 0 else (99.0 if gp > 0 else 0.0)

        return {
            "trades": n,
            "win_rate_pct": round((len(wins) / n) * 100.0, 2),
            "net_pnl": round(net_pnl, 2),
            "profit_factor": round(pf, 2),
        }
