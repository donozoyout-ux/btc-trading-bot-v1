"""Database persistence layer with SQLite, table creation, and migrations."""

import sqlite3
import os
import json
from typing import Optional, List, Dict, Any
from datetime import datetime, timezone
from loguru import logger

from config.settings import get_settings
from core.models import TradeRecord, DecisionReport


class DatabaseManager:
    """Manages SQLite database, table creation, and migrations."""

    MIGRATIONS: List[str] = [
        """CREATE TABLE IF NOT EXISTS trades (
            trade_id TEXT PRIMARY KEY,
            symbol TEXT NOT NULL,
            setup_type TEXT NOT NULL,
            direction TEXT NOT NULL,
            entry_time INTEGER NOT NULL,
            entry_price REAL NOT NULL,
            stop_loss REAL NOT NULL,
            tp1 REAL NOT NULL,
            tp2 REAL NOT NULL,
            exit_time INTEGER,
            exit_price REAL,
            exit_reason TEXT,
            size_btc REAL NOT NULL,
            size_usdt REAL NOT NULL,
            pnl_usdt REAL DEFAULT 0.0,
            pnl_pct REAL DEFAULT 0.0,
            r_multiple REAL DEFAULT 0.0,
            mfe REAL DEFAULT 0.0,
            mae REAL DEFAULT 0.0,
            is_closed INTEGER DEFAULT 0,
            fees_paid_usdt REAL DEFAULT 0.0,
            created_at INTEGER NOT NULL
        )""",
        """CREATE TABLE IF NOT EXISTS decisions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp INTEGER NOT NULL,
            symbol TEXT NOT NULL,
            price REAL NOT NULL,
            regime TEXT NOT NULL,
            regime_score REAL NOT NULL,
            confidence TEXT NOT NULL,
            volatility TEXT NOT NULL,
            structure_4h TEXT NOT NULL,
            structure_1h TEXT NOT NULL,
            location TEXT NOT NULL,
            setup TEXT NOT NULL,
            trigger_state TEXT NOT NULL,
            derivatives TEXT NOT NULL,
            global_context TEXT NOT NULL,
            risk_status TEXT NOT NULL,
            final_decision TEXT NOT NULL,
            reason TEXT NOT NULL,
            trade_plan TEXT,
            risk_assessment TEXT,
            created_at INTEGER NOT NULL
        )""",
        """CREATE TABLE IF NOT EXISTS bot_state (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            snapshot_ts INTEGER NOT NULL,
            account_balance_usdt REAL NOT NULL,
            start_of_day_balance_usdt REAL NOT NULL,
            current_day TEXT NOT NULL,
            active_position TEXT,
            consecutive_losses INTEGER NOT NULL DEFAULT 0,
            daily_realized_pnl_usdt REAL NOT NULL DEFAULT 0.0,
            kill_switch_activated INTEGER NOT NULL DEFAULT 0,
            kill_switch_reason TEXT DEFAULT '',
            active_regime TEXT,
            trigger_state TEXT NOT NULL DEFAULT 'NO_SETUP',
            recent_regimes_4h TEXT NOT NULL DEFAULT '[]',
            created_at INTEGER NOT NULL
        )""",
    ]

    def __init__(self, db_url: str):
        self.db_url = db_url
        self.conn: Optional[sqlite3.Connection] = None
        self._connect()
        self._run_migrations()

    def _connect(self) -> None:
        """Connect to SQLite database."""
        # Strip sqlite:/// prefix for sqlite3
        db_path = self.db_url.replace("sqlite:///", "")
        os.makedirs(os.path.dirname(os.path.abspath(db_path)) or ".", exist_ok=True)
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row
        logger.info(f"Database connected: {db_path}")

    def _run_migrations(self) -> None:
        """Execute all pending migrations."""
        if not self.conn:
            return
        try:
            cursor = self.conn.cursor()
            for migration_sql in self.MIGRATIONS:
                cursor.execute(migration_sql)
            self.conn.commit()
            logger.info("All database migrations applied successfully")
        except Exception as e:
            logger.error(f"Migration failed: {e}")
            raise

    def save_trade(self, trade: TradeRecord) -> None:
        """Persist a trade record to the database."""
        if not self.conn:
            return
        try:
            cursor = self.conn.cursor()
            cursor.execute("""
                INSERT OR REPLACE INTO trades
                (trade_id, symbol, setup_type, direction, entry_time, entry_price,
                 stop_loss, tp1, tp2, exit_time, exit_price, exit_reason,
                 size_btc, size_usdt, pnl_usdt, pnl_pct, r_multiple, mfe, mae,
                 is_closed, fees_paid_usdt, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                trade.trade_id, trade.symbol, trade.setup_type.value, trade.direction.value,
                trade.entry_time, trade.entry_price, trade.stop_loss, trade.tp1, trade.tp2,
                trade.exit_time, trade.exit_price, trade.exit_reason,
                trade.size_btc, trade.size_usdt, trade.pnl_usdt, trade.pnl_pct,
                trade.r_multiple, trade.mfe, trade.mae, int(trade.is_closed),
                trade.fees_paid_usdt, int(datetime.now(timezone.utc).timestamp() * 1000),
            ))
            self.conn.commit()
        except Exception as e:
            logger.error(f"Failed to save trade {trade.trade_id}: {e}")

    def load_trades(self) -> List[TradeRecord]:
        """Load all trade records from the database."""
        if not self.conn:
            return []
        try:
            cursor = self.conn.cursor()
            cursor.execute("SELECT * FROM trades ORDER BY entry_time")
            rows = cursor.fetchall()
            trades = []
            for row in rows:
                trades.append(TradeRecord(
                    trade_id=row["trade_id"],
                    symbol=row["symbol"],
                    setup_type=__import__("config.constants", fromlist=["SetupType"]).SetupType(row["setup_type"]),
                    direction=__import__("config.constants", fromlist=["TradeDirection"]).TradeDirection(row["direction"]),
                    entry_time=row["entry_time"],
                    entry_price=row["entry_price"],
                    stop_loss=row["stop_loss"],
                    tp1=row["tp1"],
                    tp2=row["tp2"],
                    exit_time=row["exit_time"],
                    exit_price=row["exit_price"],
                    exit_reason=row["exit_reason"],
                    size_btc=row["size_btc"],
                    size_usdt=row["size_usdt"],
                    pnl_usdt=row["pnl_usdt"],
                    pnl_pct=row["pnl_pct"],
                    r_multiple=row["r_multiple"],
                    mfe=row["mfe"],
                    mae=row["mae"],
                    is_closed=bool(row["is_closed"]),
                    fees_paid_usdt=row["fees_paid_usdt"],
                ))
            return trades
        except Exception as e:
            logger.error(f"Failed to load trades: {e}")
            return []

    def save_decision(self, report: DecisionReport) -> None:
        """Persist a decision report."""
        if not self.conn:
            return
        try:
            cursor = self.conn.cursor()
            cursor.execute("""
                INSERT INTO decisions
                (timestamp, symbol, price, regime, regime_score, confidence, volatility,
                 structure_4h, structure_1h, location, setup, trigger_state, derivatives,
                 global_context, risk_status, final_decision, reason, trade_plan, risk_assessment, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                report.timestamp, report.symbol, report.price, report.regime.value,
                report.regime_score, report.confidence, report.volatility.value,
                report.structure_4h.value, report.structure_1h.value,
                report.location.value, report.setup.value, report.trigger_state.value,
                report.derivatives.value, report.global_context.value,
                report.risk_status.value, report.final_decision.value, report.reason,
                report.trade_plan.model_dump_json() if report.trade_plan else None,
                report.risk_assessment.model_dump_json() if report.risk_assessment else None,
                int(datetime.now(timezone.utc).timestamp() * 1000),
            ))
            self.conn.commit()
        except Exception as e:
            logger.error(f"Failed to save decision: {e}")

    def save_state_snapshot(self, state_dict: Dict[str, Any]) -> None:
        """Save a BotState snapshot for restart recovery."""
        if not self.conn:
            return
        try:
            cursor = self.conn.cursor()
            cursor.execute("""
                INSERT INTO bot_state
                (snapshot_ts, account_balance_usdt, start_of_day_balance_usdt, current_day,
                 active_position, consecutive_losses, daily_realized_pnl_usdt,
                 kill_switch_activated, kill_switch_reason, active_regime, trigger_state,
                 recent_regimes_4h, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                state_dict["snapshot_ts"], state_dict["account_balance_usdt"],
                state_dict["start_of_day_balance_usdt"], state_dict["current_day"],
                state_dict.get("active_position"), state_dict["consecutive_losses"],
                state_dict["daily_realized_pnl_usdt"], state_dict["kill_switch_activated"],
                state_dict.get("kill_switch_reason", ""), state_dict.get("active_regime"),
                state_dict["trigger_state"], state_dict.get("recent_regimes_4h", "[]"),
                int(datetime.now(timezone.utc).timestamp() * 1000),
            ))
            self.conn.commit()
        except Exception as e:
            logger.error(f"Failed to save state snapshot: {e}")

    def load_latest_state(self) -> Optional[Dict[str, Any]]:
        """Load the latest BotState snapshot for restart recovery."""
        if not self.conn:
            return None
        try:
            cursor = self.conn.cursor()
            cursor.execute("SELECT * FROM bot_state ORDER BY snapshot_ts DESC LIMIT 1")
            row = cursor.fetchone()
            if row:
                return dict(row)
            return None
        except Exception as e:
            logger.error(f"Failed to load state snapshot: {e}")
            return None

    def close(self) -> None:
        """Close the database connection."""
        if self.conn:
            self.conn.close()
            logger.info("Database connection closed")
