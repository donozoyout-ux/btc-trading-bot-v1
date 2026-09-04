"""CLI Entrypoint for the BTC Trading Bot — Master Specification V1."""

import sys
import time
import argparse
from typing import Dict, List

# Ensure safe UTF-8 output on Windows terminals
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.text import Text
from loguru import logger

from config.settings import get_settings
from config.constants import MarketRegime, DecisionStatus, TradeDirection
from core.models import Candle
from core.state import BotState
from data.binance_client import BinanceFuturesClient
from runner import MasterPipeline
from execution.shadow_executor import ShadowExecutor
from execution.testnet_executor import TestnetExecutor
from backtest.simulator import BacktestSimulator
from backtest.data_loader import HistoricalDataLoader
from journal.journaler import Journaler
from journal.metrics import MetricsCalculator

console = Console(force_terminal=True, legacy_windows=False)


def print_banner():
    banner_text = Text()
    banner_text.append("+-------------------------------------------------------+\n", style="bold cyan")
    banner_text.append("|        BTC TRADING BOT -- MASTER SPECIFICATION V1      |\n", style="bold yellow")
    banner_text.append("|      Binance Futures USDT-M Multi-Timeframe Engine    |\n", style="bold cyan")
    banner_text.append("+-------------------------------------------------------+", style="bold cyan")
    console.print(banner_text)


def display_decision_panel(report):
    """Displays a formatted decision card per Section 42."""
    color = "green" if "ENTRY" in report.final_decision.value else ("yellow" if "WATCH" in report.final_decision.value else "white")
    
    grid = Table.grid(expand=True, padding=(0, 2))
    grid.add_column(style="bold cyan", justify="left")
    grid.add_column(justify="left")

    grid.add_row("ASSET", f"{report.symbol} @ ${report.price:,.2f}")
    grid.add_row("REGIME", f"{report.regime.value} (Score: {report.regime_score:+.0f}, Conf: {report.confidence})")
    grid.add_row("VOLATILITY", report.volatility.value)
    grid.add_row("STRUCTURE", f"4H: {report.structure_4h.value} | 1H: {report.structure_1h.value}")
    grid.add_row("LOCATION", report.location.value)
    grid.add_row("SETUP", report.setup.value)
    grid.add_row("TRIGGER", report.trigger_state.value)
    grid.add_row("DERIVATIVES", report.derivatives.value)
    grid.add_row("RISK CHECK", report.risk_status.value)
    grid.add_row("FINAL DECISION", f"[{color}]{report.final_decision.value}[/{color}]")
    grid.add_row("REASON", report.reason)

    panel = Panel(grid, title="[bold yellow]DECISION OUTPUT (Section 42)[/bold yellow]", border_style=color)
    console.print(panel)


def run_backtest_command(use_synthetic: bool = False, num_bars: int = 1500):
    """Runs a zero-lookahead backtest and prints comprehensive Section 46 metrics."""
    print_banner()
    settings = get_settings()
    loader = HistoricalDataLoader()
    simulator = BacktestSimulator(settings)

    if use_synthetic:
        console.print(f"[bold yellow]Generating {num_bars} synthetic multi-timeframe candles...[/bold yellow]")
        dataset = loader.generate_synthetic_dataset(num_5m_bars=num_bars, initial_price=65000.0)
    else:
        try:
            console.print("[bold cyan]Fetching real historical candles from Binance Futures...[/bold cyan]")
            dataset = loader.fetch_binance_history(limit_4h=250, limit_1h=300, limit_15m=400, limit_5m=600)
        except Exception as e:
            console.print(f"[bold red]Failed to fetch Binance data ({e}). Falling back to synthetic dataset...[/bold red]")
            dataset = loader.generate_synthetic_dataset(num_5m_bars=num_bars, initial_price=65000.0)

    results = simulator.run(dataset, start_idx=60)

    # Print Metrics Table
    table = Table(title="[bold green]BACKTEST PERFORMANCE REPORT (Section 46)[/bold green]", show_header=True)
    table.add_column("Metric", style="bold cyan")
    table.add_column("Value", style="bold white")

    table.add_row("Total Trades", str(results["total_trades"]))
    table.add_row("Win Rate", f"{results['win_rate_pct']:.1f}%")
    table.add_row("Profit Factor", f"{results['profit_factor']:.2f}")
    table.add_row("Expectancy", f"${results['expectancy_usdt']:+,.2f}")
    table.add_row("Average R-Multiple", f"{results['average_r']:+.2f}R")
    table.add_row("Net Realized PnL", f"${results['net_pnl_usdt']:+,.2f}")
    table.add_row("Total Fees Paid", f"${results['total_fees_usdt']:,.2f}")
    table.add_row("Max Drawdown", f"{results['max_drawdown_pct']:.2f}%")
    table.add_row("Max Consecutive Losses", str(results["max_consecutive_losses"]))
    table.add_row("Average Holding Time", f"{results['average_holding_time_mins']:.1f} mins")
    table.add_row("Average MFE / MAE", f"+{results['avg_mfe_pct']:.2f}% / -{results['avg_mae_pct']:.2f}%")
    table.add_row("Final Account Equity", f"${results['final_balance_usdt']:,.2f}")

    console.print(table)

    # Breakdown Table
    breakdowns = results.get("breakdowns", {})
    if breakdowns:
        bd_table = Table(title="[bold yellow]STRATEGY BREAKDOWN REPORT[/bold yellow]", show_header=True)
        bd_table.add_column("Category", style="cyan")
        bd_table.add_column("Trades", style="white")
        bd_table.add_column("Win Rate", style="green")
        bd_table.add_column("Net PnL", style="bold white")
        bd_table.add_column("Profit Factor", style="magenta")

        for cat, data in breakdowns.items():
            bd_table.add_row(
                cat.upper(),
                str(data["trades"]),
                f"{data['win_rate_pct']:.1f}%",
                f"${data['net_pnl']:+,.2f}",
                f"{data['profit_factor']:.2f}",
            )
        console.print(bd_table)


def run_shadow_command(poll_interval_sec: int = 15, max_iterations: int = 3):
    """Runs real-time shadow forward testing on Binance live data."""
    print_banner()
    settings = get_settings()
    client = BinanceFuturesClient()
    journaler = Journaler(settings.JOURNAL_DIR)
    pipeline = MasterPipeline(settings, journaler)
    executor = ShadowExecutor(pipeline.exit_engine, journaler)
    state = BotState(account_balance_usdt=settings.INITIAL_CAPITAL_USDT)

    console.print("[bold green]Starting Live Shadow Mode (Section 49)...[/bold green]")
    console.print("[italic yellow]Listening to Binance Futures public market feeds with virtual fills.[/italic yellow]\n")

    iteration = 0
    try:
        while iteration < max_iterations:
            iteration += 1
            console.print(f"[bold cyan]--- Cycle {iteration} @ {time.strftime('%Y-%m-%d %H:%M:%S')} ---[/bold cyan]")

            # Fetch live closed klines
            candles_4h = client.get_klines("BTCUSDT", "4h", limit=100)
            candles_1h = client.get_klines("BTCUSDT", "1h", limit=100)
            candles_15m = client.get_klines("BTCUSDT", "15m", limit=100)
            candles_5m = client.get_klines("BTCUSDT", "5m", limit=100)

            candles_dict = {
                "4h": candles_4h,
                "1h": candles_1h,
                "15m": candles_15m,
                "5m": candles_5m,
            }

            # Fetch live derivatives context
            try:
                oi = client.get_open_interest("BTCUSDT")
                funding = client.get_funding_rate("BTCUSDT")
                ls_ratio = client.get_long_short_ratio("BTCUSDT", "5m", 1)
                taker_ratio = client.get_taker_volume_ratio("BTCUSDT", "5m", 1)
                deriv_input = {
                    "open_interest": oi,
                    "oi_change_pct": 0.001,
                    "funding_rate": funding,
                    "long_short_ratio": ls_ratio,
                    "taker_buy_ratio": taker_ratio,
                }
            except Exception as e:
                logger.warning(f"Failed to fetch live derivatives: {e}")
                deriv_input = {}

            # Evaluate active shadow position
            if state.active_position is not None and candles_5m:
                executor.update_position_candle(state, candles_5m[-1])

            # Run cycle
            report = pipeline.run_cycle(candles_dict, state, deriv_input)
            display_decision_panel(report)

            # Process order
            executor.process_decision(report, state)

            if iteration < max_iterations:
                time.sleep(poll_interval_sec)

    except KeyboardInterrupt:
        console.print("\n[bold red]Shadow mode stopped by user.[/bold red]")


def main():
    parser = argparse.ArgumentParser(description="BTC Trading Bot — Master Specification V1")
    subparsers = parser.add_subparsers(dest="command", help="Operational mode")

    # Backtest subcommand
    bt_parser = subparsers.add_parser("backtest", help="Run historical zero-lookahead backtest")
    bt_parser.add_argument("--synthetic", action="store_true", help="Use synthetic multi-timeframe dataset")
    bt_parser.add_argument("--bars", type=int, default=1500, help="Number of 5M bars for test")

    # Shadow subcommand
    shadow_parser = subparsers.add_parser("shadow", help="Run live Shadow Mode with virtual fills")
    shadow_parser.add_argument("--poll", type=int, default=10, help="Polling interval in seconds")
    shadow_parser.add_argument("--cycles", type=int, default=3, help="Max test cycles")

    args = parser.parse_args()

    if args.command == "backtest":
        run_backtest_command(use_synthetic=args.synthetic, num_bars=args.bars)
    elif args.command == "shadow":
        run_shadow_command(poll_interval_sec=args.poll, max_iterations=args.cycles)
    else:
        # Default run backtest with synthetic data for instant verification
        run_backtest_command(use_synthetic=True, num_bars=1200)


if __name__ == "__main__":
    main()
