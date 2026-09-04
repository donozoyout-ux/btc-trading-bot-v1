"""Database connection manager for SQLite with WAL mode and schema initialization."""

import sqlite3
from pathlib import Path
from typing import Optional
from loguru import logger


class DatabaseManager:
    """Manages SQLite connection in WAL mode for persistent concurrent access."""

    def __init__(self, db_path: str = "trading_bot.db"):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.init_db()

    def get_connection(self) -> sqlite3.Connection:
        """Returns a connection with WAL mode and foreign keys enabled."""
        conn = sqlite3.connect(str(self.db_path), timeout=15.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA foreign_keys=ON;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        return conn

    def init_db(self) -> None:
        """Initializes all 19 relational tables if they do not already exist."""
        schema_sql = """
        -- 1. Candles
        CREATE TABLE IF NOT EXISTS candles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            timeframe TEXT NOT NULL,
            open_time INTEGER NOT NULL,
            close_time INTEGER NOT NULL,
            open REAL NOT NULL,
            high REAL NOT NULL,
            low REAL NOT NULL,
            close REAL NOT NULL,
            volume REAL NOT NULL,
            is_closed INTEGER NOT NULL DEFAULT 1,
            UNIQUE(symbol, timeframe, open_time)
        );

        -- 2. Market Snapshots
        CREATE TABLE IF NOT EXISTS market_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp INTEGER NOT NULL,
            symbol TEXT NOT NULL,
            mark_price REAL NOT NULL,
            btc_dominance REAL,
            open_interest REAL,
            funding_rate REAL,
            long_short_ratio REAL
        );

        -- 3. Structure States
        CREATE TABLE IF NOT EXISTS structure_states (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp INTEGER NOT NULL,
            timeframe TEXT NOT NULL,
            structure_type TEXT NOT NULL,
            recent_hh INTEGER,
            recent_hl INTEGER,
            recent_lh INTEGER,
            recent_ll INTEGER,
            last_bos TEXT,
            last_choch TEXT
        );

        -- 4. Swing Points with Dual Timestamps
        CREATE TABLE IF NOT EXISTS swing_points (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            timeframe TEXT NOT NULL,
            swing_time INTEGER NOT NULL,
            confirmed_at INTEGER NOT NULL,
            price REAL NOT NULL,
            is_high INTEGER NOT NULL,
            candle_index INTEGER NOT NULL
        );

        -- 5. Support / Resistance Zones
        CREATE TABLE IF NOT EXISTS zones (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp INTEGER NOT NULL,
            level_type TEXT NOT NULL,
            price_min REAL NOT NULL,
            price_max REAL NOT NULL,
            center REAL NOT NULL,
            strength INTEGER NOT NULL,
            sources_json TEXT NOT NULL
        );

        -- 6. Regime States
        CREATE TABLE IF NOT EXISTS regime_states (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp INTEGER NOT NULL,
            regime TEXT NOT NULL,
            score REAL NOT NULL,
            confidence TEXT NOT NULL,
            volatility TEXT NOT NULL,
            overextended_up INTEGER NOT NULL,
            overextended_down INTEGER NOT NULL,
            range_override INTEGER NOT NULL,
            stability_confirmed INTEGER NOT NULL,
            details_json TEXT NOT NULL
        );

        -- 7. Setups
        CREATE TABLE IF NOT EXISTS setups (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp INTEGER NOT NULL,
            setup_type TEXT NOT NULL,
            direction TEXT NOT NULL,
            timeframe TEXT NOT NULL,
            invalidation_level REAL NOT NULL,
            target_level REAL NOT NULL,
            zone_id INTEGER,
            detected INTEGER NOT NULL,
            reason TEXT NOT NULL
        );

        -- 8. Signals
        CREATE TABLE IF NOT EXISTS signals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp INTEGER NOT NULL,
            setup_id INTEGER,
            trigger_pattern TEXT NOT NULL,
            direction TEXT NOT NULL,
            trigger_price REAL NOT NULL,
            status TEXT NOT NULL
        );

        -- 9. Rejected Signals
        CREATE TABLE IF NOT EXISTS rejected_signals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp INTEGER NOT NULL,
            setup_type TEXT NOT NULL,
            direction TEXT NOT NULL,
            rejection_stage TEXT NOT NULL,
            reason TEXT NOT NULL,
            context_json TEXT NOT NULL
        );

        -- 10. Decisions
        CREATE TABLE IF NOT EXISTS decisions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp INTEGER NOT NULL,
            symbol TEXT NOT NULL,
            price REAL NOT NULL,
            regime TEXT NOT NULL,
            structure_4h TEXT NOT NULL,
            structure_1h TEXT NOT NULL,
            location TEXT NOT NULL,
            setup TEXT NOT NULL,
            trigger_state TEXT NOT NULL,
            derivatives TEXT NOT NULL,
            risk_status TEXT NOT NULL,
            final_decision TEXT NOT NULL,
            reason TEXT NOT NULL
        );

        -- 11. Pre-Trade Plans
        CREATE TABLE IF NOT EXISTS trade_plans (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp INTEGER NOT NULL,
            setup_id INTEGER,
            direction TEXT NOT NULL,
            entry_price REAL NOT NULL,
            stop_loss REAL NOT NULL,
            tp1 REAL NOT NULL,
            tp2 REAL NOT NULL,
            invalidation REAL NOT NULL,
            risk_reward REAL NOT NULL,
            status TEXT NOT NULL
        );

        -- 12. Positions
        CREATE TABLE IF NOT EXISTS positions (
            id TEXT PRIMARY KEY,
            symbol TEXT NOT NULL,
            direction TEXT NOT NULL,
            setup_type TEXT NOT NULL,
            size_btc REAL NOT NULL,
            entry_price REAL NOT NULL,
            current_stop REAL NOT NULL,
            tp1 REAL NOT NULL,
            tp2 REAL NOT NULL,
            is_open INTEGER NOT NULL DEFAULT 1,
            opened_at INTEGER NOT NULL,
            closed_at INTEGER,
            mfe REAL DEFAULT 0.0,
            mae REAL DEFAULT 0.0
        );

        -- 13. Orders
        CREATE TABLE IF NOT EXISTS orders (
            order_id TEXT PRIMARY KEY,
            position_id TEXT,
            symbol TEXT NOT NULL,
            side TEXT NOT NULL,
            order_type TEXT NOT NULL,
            price REAL,
            stop_price REAL,
            quantity REAL NOT NULL,
            status TEXT NOT NULL,
            created_at INTEGER NOT NULL
        );

        -- 14. Fills
        CREATE TABLE IF NOT EXISTS fills (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id TEXT,
            fill_time INTEGER NOT NULL,
            fill_price REAL NOT NULL,
            fill_quantity REAL NOT NULL,
            fee_paid REAL NOT NULL,
            fee_asset TEXT NOT NULL
        );

        -- 15. Trades
        CREATE TABLE IF NOT EXISTS trades (
            trade_id TEXT PRIMARY KEY,
            position_id TEXT,
            symbol TEXT NOT NULL,
            setup_type TEXT NOT NULL,
            direction TEXT NOT NULL,
            entry_time INTEGER NOT NULL,
            exit_time INTEGER NOT NULL,
            entry_price REAL NOT NULL,
            exit_price REAL NOT NULL,
            exit_reason TEXT NOT NULL,
            size_btc REAL NOT NULL,
            size_usdt REAL NOT NULL,
            gross_pnl REAL NOT NULL,
            total_fees REAL NOT NULL,
            net_pnl REAL NOT NULL,
            r_multiple REAL NOT NULL,
            mfe REAL NOT NULL,
            mae REAL NOT NULL
        );

        -- 16. Risk Events
        CREATE TABLE IF NOT EXISTS risk_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp INTEGER NOT NULL,
            event_type TEXT NOT NULL,
            account_balance REAL NOT NULL,
            realized_pnl_today REAL NOT NULL,
            consecutive_losses INTEGER NOT NULL,
            action_taken TEXT NOT NULL,
            details TEXT
        );

        -- 17. Kill Switch Events
        CREATE TABLE IF NOT EXISTS kill_switch_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp INTEGER NOT NULL,
            trigger_source TEXT NOT NULL,
            threshold_value REAL NOT NULL,
            observed_value REAL NOT NULL,
            is_active INTEGER NOT NULL,
            resolved_at INTEGER
        );

        -- 18. API Health Telemetry
        CREATE TABLE IF NOT EXISTS api_health (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp INTEGER NOT NULL,
            source TEXT NOT NULL,
            status TEXT NOT NULL,
            latency_ms INTEGER NOT NULL,
            error_message TEXT
        );

        -- 19. Backtest Runs & Trades
        CREATE TABLE IF NOT EXISTS backtest_runs (
            run_id TEXT PRIMARY KEY,
            strategy_version TEXT NOT NULL,
            config_version TEXT NOT NULL,
            dataset_version TEXT NOT NULL,
            start_time INTEGER NOT NULL,
            end_time INTEGER NOT NULL,
            initial_equity REAL NOT NULL,
            fee_model TEXT NOT NULL,
            slippage_model TEXT NOT NULL,
            funding_model TEXT NOT NULL,
            total_trades INTEGER NOT NULL,
            win_rate REAL NOT NULL,
            net_pnl REAL NOT NULL,
            profit_factor REAL NOT NULL,
            max_drawdown REAL NOT NULL,
            created_at INTEGER NOT NULL
        );

        CREATE TABLE IF NOT EXISTS backtest_trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT NOT NULL REFERENCES backtest_runs(run_id),
            trade_id TEXT NOT NULL,
            direction TEXT NOT NULL,
            setup_type TEXT NOT NULL,
            entry_time INTEGER NOT NULL,
            exit_time INTEGER NOT NULL,
            entry_price REAL NOT NULL,
            exit_price REAL NOT NULL,
            exit_reason TEXT NOT NULL,
            pnl REAL NOT NULL,
            r_multiple REAL NOT NULL,
            mfe REAL NOT NULL,
            mae REAL NOT NULL
        );

        -- 20. System Restart Recovery State
        CREATE TABLE IF NOT EXISTS system_state (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at INTEGER NOT NULL
        );
        """
        with self.get_connection() as conn:
            conn.executescript(schema_sql)
            conn.commit()
