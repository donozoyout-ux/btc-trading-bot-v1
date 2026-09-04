"""Phase 1 Historical Backtest entry point.

Modes:
  --smoke        : synthetic dataset, no network (validates pipeline end-to-end)
  default        : fetch real Binance USDT-M klines (prod), cache to disk, run backtest

Derivatives are ALWAYS UNAVAILABLE (Mode A — Technical Baseline). No optimization.
"""
import argparse
import json
import sys
import time
from pathlib import Path
from loguru import logger

from backtest.data_loader import HistoricalDataLoader
from backtest.historical_fetcher import HistoricalDataFetcher
from backtest.phase1_runner import Phase1BacktestRunner
from core.models import Candle


def save_cache(dataset, cache_dir: Path) -> None:
    cache_dir.mkdir(parents=True, exist_ok=True)
    for tf, candles in dataset.items():
        path = cache_dir / f"klines_{tf}.json"
        with open(path, "w", encoding="utf-8") as f:
            json.dump([c.model_dump() for c in candles], f)
        logger.info(f"Cached {tf}: {len(candles)} candles -> {path}")


def load_cache(cache_dir: Path):
    dataset = {}
    for tf in ["5m", "15m", "1h", "4h"]:
        path = cache_dir / f"klines_{tf}.json"
        if not path.exists():
            return None
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
        dataset[tf] = [Candle.model_validate(c) for c in raw]
    return dataset


def main() -> int:
    parser = argparse.ArgumentParser(description="Phase 1 Historical Backtest (Mode A)")
    parser.add_argument("--smoke", action="store_true", help="Synthetic data, no network")
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--years", type=float, default=3.0, help="History length for real fetch")
    parser.add_argument("--cache-dir", default="data_cache/phase1")
    parser.add_argument("--use-cache", action="store_true", help="Load from cache, skip fetch")
    parser.add_argument("--start-idx", type=int, default=500)
    parser.add_argument("--synthetic-bars", type=int, default=5000)
    args = parser.parse_args()

    if args.smoke:
        logger.info("SMOKE MODE: synthetic dataset")
        dataset = HistoricalDataLoader.generate_synthetic_dataset(num_5m_bars=args.synthetic_bars)
        fetcher = HistoricalDataFetcher(testnet=False)
        stats = fetcher.get_dataset_stats(dataset)
    else:
        cache_dir = Path(args.cache_dir)
        if args.use_cache:
            dataset = load_cache(cache_dir)
            if dataset is None:
                logger.error(f"Cache miss in {cache_dir}, run without --use-cache first")
                return 1
            logger.info(f"Loaded dataset from cache: { {k: len(v) for k, v in dataset.items()} }")
        else:
            fetcher = HistoricalDataFetcher(testnet=False)  # prod: testnet has no history
            now_ms = int(time.time() * 1000)
            start_ms = now_ms - int(args.years * 365.25 * 24 * 60 * 60 * 1000)
            logger.info(f"Fetching {args.years}y of {args.symbol} from Binance prod...")
            dataset = fetcher.fetch_all_timeframes(args.symbol, start_ms, now_ms)
            save_cache(dataset, cache_dir)
        fetcher = HistoricalDataFetcher(testnet=False)
        stats = fetcher.get_dataset_stats(dataset)

    logger.info(f"Dataset stats: {json.dumps(stats, indent=1, default=str)}")
    # Hard fail on empty timeframes
    for tf in ["5m", "15m", "1h", "4h"]:
        if not dataset.get(tf):
            logger.error(f"Empty dataset for {tf}, aborting")
            return 1

    runner = Phase1BacktestRunner()
    results = runner.run(dataset, start_idx=args.start_idx)
    generated = runner.generate_reports(results, stats)

    logger.info(f"Done. Reports: {sorted(generated.keys())}")
    logger.info(
        f"Trades={results['total_trades']} Net=${results['combined']['net_pnl_usdt']} "
        f"WR={results['combined']['win_rate_pct']}% PF={results['combined']['profit_factor']}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
