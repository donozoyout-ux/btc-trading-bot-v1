"""Integration tests for MasterPipeline and BacktestSimulator."""

import pytest
from config.settings import BotSettings
from core.state import BotState
from runner import MasterPipeline
from backtest.simulator import BacktestSimulator
from backtest.data_loader import HistoricalDataLoader


def test_master_pipeline_full_cycle():
    settings = BotSettings()
    pipeline = MasterPipeline(settings)
    loader = HistoricalDataLoader()

    dataset = loader.generate_synthetic_dataset(num_5m_bars=400, seed=123)
    state = BotState()

    report = pipeline.run_cycle(dataset, state)

    assert report is not None
    assert report.symbol == "BTC/USDT"
    assert report.final_decision is not None
    assert report.regime is not None
    assert report.reason != ""


def test_backtest_simulator_execution():
    settings = BotSettings()
    loader = HistoricalDataLoader()
    dataset = loader.generate_synthetic_dataset(num_5m_bars=600, seed=42)

    simulator = BacktestSimulator(settings)
    results = simulator.run(dataset, start_idx=80)

    assert "total_trades" in results
    assert "win_rate_pct" in results
    assert "profit_factor" in results
    assert "net_pnl_usdt" in results
    assert "max_drawdown_pct" in results
    assert "breakdowns" in results
