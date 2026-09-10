"""Canonical read-only active-trade state and restart recovery."""

from __future__ import annotations

import time
from typing import Any, Dict, Iterable, List, Optional, Tuple


def _num(value: Any) -> Optional[float]:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


class ActiveTradeStateEngine:
    """Field-level merger.  It has no order-capable dependency or method."""

    def __init__(self, symbol: str = "BTCUSDT"):
        self.symbol = symbol

    @staticmethod
    def _pick(*candidates: Any) -> Tuple[Any, str]:
        if len(candidates) % 2:
            raise ValueError("source candidates must be value/source pairs")
        for index in range(0, len(candidates), 2):
            value, source = candidates[index], candidates[index + 1]
            if value is not None and value != "":
                return value, source
        return None, "UNAVAILABLE"

    @staticmethod
    def _order_type(order: Dict[str, Any]) -> str:
        return str(order.get("type") or order.get("orderType") or "").upper()

    @staticmethod
    def _trigger(order: Dict[str, Any]) -> Optional[float]:
        return _num(order.get("stop_price") or order.get("triggerPrice") or order.get("trigger_price") or order.get("price"))

    def _protection(self, orders: Iterable[Dict[str, Any]], side: str, entry: float, mark: float) -> Dict[str, Any]:
        relevant = [x for x in orders if str(x.get("symbol") or self.symbol).upper() == self.symbol]
        stops, targets = [], []
        for row in relevant:
            trigger, kind = self._trigger(row), self._order_type(row)
            if trigger is None:
                continue
            if kind == "STOP_MARKET" and ((side == "LONG" and trigger < mark) or (side == "SHORT" and trigger > mark)):
                stops.append((trigger, row))
            if kind == "TAKE_PROFIT_MARKET" and ((side == "LONG" and trigger > entry) or (side == "SHORT" and trigger < entry)):
                targets.append((trigger, row))
        targets.sort(key=lambda item: abs(item[0] - entry))
        stop = stops[0] if len(stops) == 1 else (stops[0] if stops else None)
        status = "HEALTHY" if stops and targets else "STOP_ONLY" if stops else "TARGET_ONLY" if targets else "UNPROTECTED"
        if len(stops) > 1 or len({x[0] for x in targets}) != len(targets):
            status = "AMBIGUOUS"
        return {
            "stop_price": stop[0] if stop else None,
            "stop_quantity": _num(stop[1].get("quantity") or stop[1].get("origQty")) if stop else None,
            "targets": targets,
            "protection_status": status,
            "protection_order_count": len(stops) + len(targets),
        }

    @staticmethod
    def _r_metrics(side: str, entry: float, mark: float, initial_stop: Optional[float], candles: Iterable[Any], opened_at: Optional[int], persisted_mfe: Any, persisted_mae: Any) -> Dict[str, Any]:
        if initial_stop is None or opened_at is None:
            return {"current_r": None, "mfe_r": None, "mae_r": None, "giveback_r": None}
        risk = abs(entry - initial_stop)
        if risk <= 0:
            return {"current_r": None, "mfe_r": None, "mae_r": None, "giveback_r": None}
        current = ((mark - entry) if side == "LONG" else (entry - mark)) / risk
        favorable = 0.0
        adverse = 0.0
        for candle in candles:
            is_closed = candle.get("is_closed", True) if isinstance(candle, dict) else getattr(candle, "is_closed", True)
            if not is_closed:
                continue
            ts = int(candle.get("timestamp", 0) if isinstance(candle, dict) else getattr(candle, "timestamp", 0))
            if ts < opened_at:
                continue
            high = _num(getattr(candle, "high", candle.get("high") if isinstance(candle, dict) else None))
            low = _num(getattr(candle, "low", candle.get("low") if isinstance(candle, dict) else None))
            if high is None or low is None:
                continue
            favorable = max(favorable, (high - entry) / risk if side == "LONG" else (entry - low) / risk)
            adverse = min(adverse, (low - entry) / risk if side == "LONG" else (entry - high) / risk)
        mfe = max(favorable, _num(persisted_mfe) or 0.0)
        mae = min(adverse, _num(persisted_mae) or 0.0)
        return {"current_r": current, "mfe_r": mfe, "mae_r": mae, "giveback_r": max(0.0, mfe - current)}

    def build(self, *, account: Dict[str, Any], execution: Dict[str, Any], mark_price: Optional[float], closed_5m_candles: Iterable[Any], durable_state: str, features: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        account_position = next((x for x in account.get("positions", []) if str(x.get("symbol")).upper() == self.symbol), {})
        execution_position = execution.get("position") or {}
        amount = _num(account_position.get("position_amount"))
        if amount is None:
            amount = _num(execution_position.get("position_amt"))
        if not amount:
            return {"status": "FLAT", "symbol": self.symbol, "observed_at": int(time.time() * 1000), "durable_state": durable_state}
        side = "LONG" if amount > 0 else "SHORT"
        context = execution.get("entry_context") or {}
        intelligence = execution.get("position_intelligence") or {}
        sources: Dict[str, str] = {}
        def field(name: str, *choices: Tuple[Any, str]):
            value, source = self._pick(*choices); sources[name] = source; return value
        entry = field("entry_price", account_position.get("entry_price"), "BINANCE_SIGNED_POSITION", execution_position.get("entry_price"), "EXECUTOR_POSITION", context.get("actual_entry_price"), "PERSISTED_ENTRY_CONTEXT")
        mark = field("mark_price", mark_price, "BINANCE_MARK", account_position.get("mark_price"), "BINANCE_SIGNED_POSITION", execution_position.get("mark_price"), "EXECUTOR_POSITION")
        quantity = abs(amount)
        recovered = next((x for x in reversed(account.get("daily_trade_ledger", {}).get("lifecycles", [])) if x.get("closed_at") is None), {})
        opened_at = context.get("entry_opened_at") or recovered.get("entry_opened_at")
        initial_qty = context.get("actual_initial_position_size") or recovered.get("initial_quantity")
        baseline_verified = context.get("exchange_baseline_verified") is True and all(context.get(k) is not None for k in ("actual_entry_price", "actual_initial_position_size", "actual_initial_stop", "entry_opened_at"))
        initial_stop = _num(context.get("actual_initial_stop")) if baseline_verified else None
        context_status = "VERIFIED" if baseline_verified else "PARTIAL" if entry is not None else "UNAVAILABLE"
        if entry is None or mark is None:
            return {
                "status": "ACTIVE", "symbol": self.symbol, "side": side,
                "entry_price": entry, "mark_price": mark, "current_quantity": quantity,
                "context_status": "UNAVAILABLE", "position_intelligence_status": "DATA_UNAVAILABLE",
                "reason_codes": ["ENTRY_OR_MARK_UNAVAILABLE"], "durable_state": durable_state,
                "observed_at": int(time.time() * 1000),
            }
        protection = self._protection(account.get("open_orders", []), side, float(entry), float(mark))
        targets = protection.pop("targets")
        sources["stop_price"] = "BINANCE_OPEN_ALGO_ORDER" if protection["stop_price"] is not None else "UNAVAILABLE"
        sources["tp1_price"] = "BINANCE_OPEN_ALGO_ORDER" if targets else "UNAVAILABLE"
        sources["tp2_price"] = "BINANCE_OPEN_ALGO_ORDER" if len(targets) > 1 else "UNAVAILABLE"
        r = self._r_metrics(side, float(entry), float(mark), initial_stop, closed_5m_candles, opened_at, intelligence.get("mfe_r") or execution.get("management_mfe_r"), intelligence.get("mae_r") or execution.get("management_mae_r"))
        current_stop = protection["stop_price"]
        protected_r = None
        if initial_stop is not None and current_stop is not None:
            risk = abs(float(entry) - initial_stop)
            protected_r = ((current_stop - float(entry)) if side == "LONG" else (float(entry) - current_stop)) / risk if risk else None
        pi_status = "AVAILABLE" if baseline_verified and intelligence else "WAITING_FIRST_CLOSED_5M" if baseline_verified else "RECOVERED_CONTEXT_PARTIAL"
        management = intelligence.get("state") or ("İlk kapalı 5D mum bekleniyor" if baseline_verified else "Yeniden başlatma sonrası bağlam kısmi")
        reason_codes = list(intelligence.get("reason_codes") or [])
        if not baseline_verified:
            reason_codes.append("INITIAL_STOP_BASELINE_UNAVAILABLE")
        result = {
            "status": "ACTIVE", "symbol": self.symbol, "side": side,
            "entry_price": entry, "mark_price": mark,
            "break_even_price": field("break_even_price", account_position.get("break_even_price"), "BINANCE_SIGNED_POSITION", execution_position.get("break_even_price"), "EXECUTOR_POSITION"),
            "initial_quantity": _num(initial_qty), "current_quantity": quantity,
            "notional": field("notional", account_position.get("notional"), "BINANCE_SIGNED_POSITION", execution_position.get("notional"), "EXECUTOR_POSITION"),
            "leverage": field("leverage", account_position.get("leverage"), "BINANCE_SIGNED_POSITION", execution_position.get("leverage"), "EXECUTOR_POSITION"),
            "margin_type": field("margin_type", account_position.get("margin_type"), "BINANCE_SIGNED_POSITION", execution_position.get("margin_type"), "EXECUTOR_POSITION"),
            "liquidation_price": field("liquidation_price", account_position.get("liquidation_price"), "BINANCE_SIGNED_POSITION", execution_position.get("liquidation_price"), "EXECUTOR_POSITION"),
            "unrealized_pnl": field("unrealized_pnl", account_position.get("unrealized_pnl"), "BINANCE_SIGNED_POSITION", execution_position.get("unrealized_pnl"), "EXECUTOR_POSITION"),
            "unrealized_pnl_pct": ((float(mark) - float(entry)) / float(entry) * (1 if side == "LONG" else -1) * 100) if entry else None,
            **protection,
            "tp1_price": targets[0][0] if targets else None, "tp1_quantity": _num(targets[0][1].get("quantity")) if targets else None,
            "tp1_role": "TP1" if len(targets) > 1 else "TP_FINAL" if targets else None,
            "tp2_price": targets[1][0] if len(targets) > 1 else None, "tp2_quantity": _num(targets[1][1].get("quantity")) if len(targets) > 1 else None,
            "entry_opened_at": opened_at, "age_minutes": max(0, (int(time.time()*1000) - int(opened_at)) / 60000) if opened_at else None,
            "initial_stop": initial_stop, **r, "protected_r": protected_r,
            "management_state": management, "management_profile": context.get("management_profile") or intelligence.get("management_profile"),
            "thesis_state": "VALID" if intelligence.get("thesis_valid") is True else "INVALID" if intelligence.get("thesis_valid") is False else "UNAVAILABLE",
            "reason_codes": reason_codes, "context_status": context_status, "position_intelligence_status": pi_status,
            "source_health": {"position": "AVAILABLE", "mark": sources.get("mark_price"), "protection": protection["protection_status"]},
            "durable_state": durable_state, "field_sources": sources, "observed_at": int(time.time() * 1000),
        }
        if baseline_verified:
            result["trade_summary"] = {"title": f"{side} pozisyon {r['current_r']:+.2f}R seviyesinde.", "state": management, "explanation": f"MFE {r['mfe_r']:.2f}R, geri verme {r['giveback_r']:.2f}R.", "next_expected_action": "Bir sonraki kapalı 5D mumda yeniden değerlendirilecek."}
        else:
            result["trade_summary"] = {"title": "Binance pozisyonu bulundu.", "state": "RESTART_CONTEXT_PARTIAL", "explanation": "Başlangıç stop bağlamı doğrulanamadı; exchange koruması ayrı olarak izleniyor.", "next_expected_action": "R tabanlı yönetim güvenlik nedeniyle devre dışı."}
        result["trade_state_features"] = {**(features or {}), "side": side, "current_r": r["current_r"], "mfe_r": r["mfe_r"], "giveback_r": r["giveback_r"], "protected_r": protected_r, "position_age": result["age_minutes"], "current_unrealized_pnl": result["unrealized_pnl"], "profit_protection_state": result["protection_status"]}
        return result
