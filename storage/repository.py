"""State and Trade persistence repository implementing crash recovery across restarts."""

import json
import time
from typing import Optional, List, Dict, Any
from core.models import TradeRecord, DecisionReport
from core.state import BotState
from config.constants import TradeDirection, SetupType
from storage.db import DatabaseManager


class Repository:
    """Repository pattern managing persistent state, open positions, trades, and recovery."""

    def __init__(self, db_manager: DatabaseManager):
        self.db = db_manager

    def save_state(self, state: BotState) -> None:
        """Saves current bot runtime state to system_state and positions table."""
        now_ts = int(time.time() * 1000)
        state_data = {
            "account_balance_usdt": state.account_balance_usdt,
            "start_of_day_balance_usdt": state.start_of_day_balance_usdt,
            "current_day": state.current_day,
            "consecutive_losses": state.consecutive_losses,
            "daily_realized_pnl_usdt": state.daily_realized_pnl_usdt,
            "kill_switch_activated": state.kill_switch_activated,
            "kill_switch_reason": state.kill_switch_reason,
            "trigger_state": state.trigger_state.value,
        }

        with self.db.get_connection() as conn:
            conn.execute(
                "INSERT INTO system_state (key, value, updated_at) VALUES ('bot_state', ?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at;",
                (json.dumps(state_data), now_ts),
            )

            # Persist or update active position
            if state.active_position is not None:
                p = state.active_position
                conn.execute(
                    """
                    INSERT INTO positions (
                        id, symbol, direction, setup_type, size_btc, entry_price,
                        current_stop, tp1, tp2, is_open, opened_at, mfe, mae
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        current_stop=excluded.current_stop,
                        tp1=excluded.tp1,
                        tp2=excluded.tp2,
                        mfe=excluded.mfe,
                        mae=excluded.mae;
                    """,
                    (
                        p.trade_id, p.symbol, p.direction.value, p.setup_type.value,
                        p.size_btc, p.entry_price, p.stop_loss, p.tp1, p.tp2,
                        p.entry_time, p.mfe, p.mae
                    ),
                )
            conn.commit()

    def restore_state(self) -> Optional[BotState]:
        """Restores BotState and any active open position from persistent storage."""
        with self.db.get_connection() as conn:
            row = conn.execute("SELECT value FROM system_state WHERE key = 'bot_state';").fetchone()
            if not row:
                return None

            data = json.loads(row["value"])
            state = BotState(
                account_balance_usdt=data["account_balance_usdt"],
                start_of_day_balance_usdt=data["start_of_day_balance_usdt"],
                current_day=data["current_day"],
                consecutive_losses=data["consecutive_losses"],
                daily_realized_pnl_usdt=data["daily_realized_pnl_usdt"],
                kill_switch_activated=data["kill_switch_activated"],
                kill_switch_reason=data["kill_switch_reason"],
            )

            # Restore active open position
            pos_row = conn.execute("SELECT * FROM positions WHERE is_open = 1 LIMIT 1;").fetchone()
            if pos_row:
                trade = TradeRecord(
                    trade_id=pos_row["id"],
                    symbol=pos_row["symbol"],
                    setup_type=SetupType(pos_row["setup_type"]),
                    direction=TradeDirection(pos_row["direction"]),
                    entry_time=pos_row["opened_at"],
                    entry_price=pos_row["entry_price"],
                    stop_loss=pos_row["current_stop"],
                    tp1=pos_row["tp1"],
                    tp2=pos_row["tp2"],
                    size_btc=pos_row["size_btc"],
                    size_usdt=pos_row["size_btc"] * pos_row["entry_price"],
                    mfe=pos_row["mfe"] or 0.0,
                    mae=pos_row["mae"] or 0.0,
                    is_closed=False,
                )
                state.active_position = trade

            return state

    def save_completed_trade(self, trade: TradeRecord) -> None:
        """Persists closed trade and marks position closed."""
        with self.db.get_connection() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO trades (
                    trade_id, position_id, symbol, setup_type, direction, entry_time,
                    exit_time, entry_price, exit_price, exit_reason, size_btc, size_usdt,
                    gross_pnl, total_fees, net_pnl, r_multiple, mfe, mae
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    trade.trade_id, trade.trade_id, trade.symbol, trade.setup_type.value,
                    trade.direction.value, trade.entry_time, trade.exit_time or trade.entry_time,
                    trade.entry_price, trade.exit_price or trade.entry_price, trade.exit_reason or "MANUAL",
                    trade.size_btc, trade.size_usdt, trade.pnl_usdt + trade.fees_paid_usdt,
                    trade.fees_paid_usdt, trade.pnl_usdt, trade.r_multiple, trade.mfe, trade.mae
                ),
            )
            conn.execute("UPDATE positions SET is_open = 0, closed_at = ? WHERE id = ?;", (trade.exit_time, trade.trade_id))
            conn.commit()
