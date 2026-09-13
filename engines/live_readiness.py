"""Live-money readiness scoring from authoritative TESTNET evidence.

The engine is deliberately advisory: it never enables production trading and it
never changes strategy/risk parameters. Exchange fills are reconstructed into
flat -> position -> flat lifecycles so readiness is based on completed TESTNET
trades rather than dashboard counters.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional

EPSILON = 1e-12
DAY_MS = 86_400_000


def _number(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _fill_time(fill: Dict[str, Any]) -> int:
    return int(fill.get("time") or fill.get("timestamp") or 0)


def reconstruct_trade_cycles(fills: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Rebuild completed position lifecycles from Binance USER_DATA fills.

    One-way mode (positionSide=BOTH) and hedge-mode LONG/SHORT fills are both
    supported. A lifecycle is emitted only when the reconstructed position
    returns to flat, so open positions never inflate the completed-trade count.
    """

    rows = sorted((dict(row) for row in fills), key=_fill_time)
    states: Dict[str, Dict[str, Any]] = {}
    cycles: List[Dict[str, Any]] = []

    def state_for(key: str) -> Dict[str, Any]:
        if key not in states:
            states[key] = {
                "quantity": 0.0,
                "entry_time": None,
                "direction": None,
                "realized_pnl": 0.0,
                "commission": 0.0,
                "fills": 0,
            }
        return states[key]

    for row in rows:
        qty = abs(_number(row.get("qty") or row.get("quantity")))
        if qty <= EPSILON:
            continue
        side = str(row.get("side") or "").upper()
        position_side = str(row.get("positionSide") or "BOTH").upper()
        if position_side not in {"BOTH", "LONG", "SHORT"}:
            position_side = "BOTH"

        if position_side == "LONG":
            delta = qty if side == "BUY" else -qty
        elif position_side == "SHORT":
            delta = qty if side == "SELL" else -qty
        else:
            delta = qty if side == "BUY" else -qty

        state = state_for(position_side)
        old_qty = float(state["quantity"])
        new_qty = old_qty + delta
        ts = _fill_time(row)
        commission = _number(row.get("commission"))
        realized = _number(row.get("realizedPnl") or row.get("realized_pnl"))

        if abs(old_qty) <= EPSILON and abs(new_qty) > EPSILON:
            state["entry_time"] = ts
            state["direction"] = (
                position_side
                if position_side in {"LONG", "SHORT"}
                else ("LONG" if new_qty > 0 else "SHORT")
            )
            state["realized_pnl"] = 0.0
            state["commission"] = 0.0
            state["fills"] = 0

        # Opening and closing fees both belong to the active lifecycle.
        if abs(old_qty) > EPSILON or abs(new_qty) > EPSILON:
            state["realized_pnl"] += realized
            state["commission"] += commission
            state["fills"] += 1

        returned_flat = abs(old_qty) > EPSILON and abs(new_qty) <= EPSILON
        crossed = (
            position_side == "BOTH"
            and abs(old_qty) > EPSILON
            and abs(new_qty) > EPSILON
            and ((old_qty > 0) != (new_qty > 0))
        )
        if returned_flat or crossed:
            net = float(state["realized_pnl"]) - float(state["commission"])
            cycles.append(
                {
                    "entry_time": int(state["entry_time"] or ts),
                    "exit_time": ts,
                    "direction": state["direction"],
                    "realized_pnl_usdt": round(float(state["realized_pnl"]), 8),
                    "commission_usdt": round(float(state["commission"]), 8),
                    "net_pnl_usdt": round(net, 8),
                    "fills": int(state["fills"]),
                }
            )

            if crossed:
                state.update(
                    {
                        "quantity": new_qty,
                        "entry_time": ts,
                        "direction": "LONG" if new_qty > 0 else "SHORT",
                        "realized_pnl": 0.0,
                        "commission": 0.0,
                        "fills": 0,
                    }
                )
                continue

            state.update(
                {
                    "quantity": 0.0,
                    "entry_time": None,
                    "direction": None,
                    "realized_pnl": 0.0,
                    "commission": 0.0,
                    "fills": 0,
                }
            )
            continue

        state["quantity"] = new_qty

    return cycles


def _max_drawdown_pct(cycles: List[Dict[str, Any]], starting_equity: float) -> float:
    equity = max(float(starting_equity), 1.0)
    peak = equity
    max_dd = 0.0
    for trade in cycles:
        equity += _number(trade.get("net_pnl_usdt"))
        peak = max(peak, equity)
        if peak > 0:
            max_dd = max(max_dd, (peak - equity) / peak * 100.0)
    return max_dd


def summarize_performance(
    cycles: List[Dict[str, Any]],
    *,
    wallet_balance_usdt: Optional[float] = None,
    now_ms: Optional[int] = None,
) -> Dict[str, Any]:
    now_ms = int(now_ms or 0)
    net = sum(_number(row.get("net_pnl_usdt")) for row in cycles)
    wins = [row for row in cycles if _number(row.get("net_pnl_usdt")) > 0]
    losses = [row for row in cycles if _number(row.get("net_pnl_usdt")) < 0]
    gross_profit = sum(_number(row.get("net_pnl_usdt")) for row in wins)
    gross_loss = abs(sum(_number(row.get("net_pnl_usdt")) for row in losses))
    profit_factor = (
        gross_profit / gross_loss
        if gross_loss > EPSILON
        else (99.0 if gross_profit > EPSILON else 0.0)
    )
    total = len(cycles)
    first_entry = min((int(row["entry_time"]) for row in cycles), default=None)
    observation_days = (
        max(0.0, (now_ms - first_entry) / DAY_MS)
        if first_entry is not None and now_ms > 0
        else 0.0
    )

    wallet = float(wallet_balance_usdt) if wallet_balance_usdt is not None else None
    estimated_start = (
        wallet - net
        if wallet is not None and wallet - net > 0
        else max(wallet or 0.0, 1.0)
    )
    max_dd = _max_drawdown_pct(cycles, estimated_start)

    return {
        "total_trades": total,
        "winning_trades": len(wins),
        "losing_trades": len(losses),
        "win_rate_pct": round((len(wins) / total * 100.0) if total else 0.0, 2),
        "profit_factor": round(profit_factor, 2),
        "net_pnl_usdt": round(net, 2),
        "gross_profit_usdt": round(gross_profit, 2),
        "gross_loss_usdt": round(gross_loss, 2),
        "max_drawdown_pct": round(max_dd, 2),
        "observation_days": round(observation_days, 1),
        "first_trade_at": first_entry,
        "last_trade_at": max((int(row["exit_time"]) for row in cycles), default=None),
    }


def critical_execution_incidents(events: Iterable[Dict[str, Any]]) -> int:
    critical_actions = {
        "RECONCILIATION_FAILURE",
        "UNEXPECTED_OPEN_ORDERS",
        "PROTECTION_FAILURE",
        "PROTECTION_RECONCILIATION_REQUIRED",
        "UNPROTECTED_POSITION",
        "EMERGENCY_FLATTEN_FAILURE",
    }
    count = 0
    for event in events:
        action = str(event.get("action") or "").upper()
        status = str(event.get("status") or "").upper()
        if action in critical_actions or status == "KILL_SWITCH":
            count += 1
    return count


def evaluate_live_readiness(
    *,
    fills: Iterable[Dict[str, Any]],
    wallet_balance_usdt: Optional[float],
    account_connected: bool,
    market_basis: str,
    execution_thread: str,
    execution_error: Optional[str],
    critical_events: Iterable[Dict[str, Any]] = (),
    trade_history_available: bool = True,
    now_ms: int,
    fill_limit: int = 1000,
) -> Dict[str, Any]:
    fills_list = list(fills)
    cycles = reconstruct_trade_cycles(fills_list)
    performance = summarize_performance(
        cycles,
        wallet_balance_usdt=wallet_balance_usdt,
        now_ms=now_ms,
    )
    incidents = critical_execution_incidents(critical_events)

    criteria = [
        {
            "id": "observation_days",
            "label": "TESTNET süresi",
            "value": f'{performance["observation_days"]:.1f} / 30 gün',
            "passed": performance["observation_days"] >= 30.0,
            "hard_blocker": False,
        },
        {
            "id": "completed_trades",
            "label": "Kapalı işlem",
            "value": f'{performance["total_trades"]} / 50',
            "passed": performance["total_trades"] >= 50,
            "hard_blocker": False,
        },
        {
            "id": "net_pnl",
            "label": "Net PnL",
            "value": f'{performance["net_pnl_usdt"]:+.2f} USDT',
            "passed": performance["total_trades"] > 0 and performance["net_pnl_usdt"] > 0,
            "hard_blocker": False,
        },
        {
            "id": "profit_factor",
            "label": "Profit factor",
            "value": f'{performance["profit_factor"]:.2f} / 1.30',
            "passed": performance["total_trades"] > 0 and performance["profit_factor"] >= 1.30,
            "hard_blocker": False,
        },
        {
            "id": "max_drawdown",
            "label": "Maks. drawdown",
            "value": f'{performance["max_drawdown_pct"]:.2f}% / ≤10%',
            "passed": performance["total_trades"] > 0 and performance["max_drawdown_pct"] <= 10.0,
            "hard_blocker": False,
        },
        {
            "id": "critical_errors",
            "label": "Kritik execution hatası",
            "value": str(incidents),
            "passed": incidents == 0,
            "hard_blocker": True,
        },
        {
            "id": "execution_loop",
            "label": "Execution loop",
            "value": str(execution_thread or "UNKNOWN"),
            "passed": str(execution_thread).upper() == "RUNNING" and not execution_error,
            "hard_blocker": True,
        },
        {
            "id": "futures_native",
            "label": "Futures-native veri",
            "value": str(market_basis or "UNKNOWN"),
            "passed": str(market_basis).upper() == "FUTURES_NATIVE",
            "hard_blocker": True,
        },
        {
            "id": "account_connected",
            "label": "TESTNET hesap + geçmiş",
            "value": (
                "CONNECTED"
                if account_connected and trade_history_available
                else "HISTORY_UNAVAILABLE"
                if account_connected
                else "DISCONNECTED"
            ),
            "passed": bool(account_connected and trade_history_available),
            "hard_blocker": True,
        },
    ]

    passed = sum(1 for row in criteria if row["passed"])
    hard_failures = [row["id"] for row in criteria if row["hard_blocker"] and not row["passed"]]
    if passed == len(criteria):
        status = "READY"
    elif not hard_failures and passed >= 7:
        status = "ALMOST_READY"
    else:
        status = "NOT_READY"

    warnings: List[str] = []
    if len(fills_list) >= fill_limit:
        warnings.append("BINANCE_FILL_WINDOW_AT_LIMIT")
    if str(market_basis).upper() != "FUTURES_NATIVE":
        warnings.append("FUTURES_NATIVE_MARKET_DATA_REQUIRED")
    if incidents == 0:
        warnings.append("CRITICAL_ERROR_COUNT_COVERS_CURRENT_RUNTIME_JOURNAL")

    return {
        "status": status,
        "passed": passed,
        "total": len(criteria),
        "hard_failures": hard_failures,
        "criteria": criteria,
        "performance": performance,
        "critical_execution_incidents": incidents,
        "fill_records_observed": len(fills_list),
        "completed_cycles_observed": len(cycles),
        "source": "BINANCE_TESTNET_USER_TRADES",
        "journal_scope": "CURRENT_RUNTIME_JOURNAL",
        "warnings": warnings,
        "generated_at": int(now_ms),
    }
