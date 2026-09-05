"""Structured Telegram event notifications with in-process spam suppression."""

from __future__ import annotations

import hashlib
import json
import time
from typing import Any, Dict, Optional

from notifications.telegram_client import TelegramClient, TelegramError


class TelegramEventNotifier:
    EVENTS = {
        "LONG_SETUP", "SHORT_SETUP", "WAIT_TRIGGER", "POSITION_OPENED", "POSITION_CLOSED",
        "TP1", "TP2", "STOP_LOSS", "HIGH_NEWS_RISK", "KILL_SWITCH", "DATA_SOURCE_ERROR", "DAILY_SUMMARY",
        "SYSTEM_STARTED", "BINANCE_CONNECTED", "ORDER_OPENED", "ORDER_CLOSED",
        "TAKE_PROFIT", "ORDER_REJECTED", "PROTECTION_FAILURE", "SMOKE_TEST_PASS",
        "SMOKE_TEST_FAIL", "ERROR",
    }

    def __init__(self, client: TelegramClient, dedupe_ttl_seconds: int = 3600):
        self.client = client
        self.dedupe_ttl_seconds = dedupe_ttl_seconds
        self._sent: Dict[str, float] = {}

    @staticmethod
    def _value(value: Any, fallback: str = "—") -> str:
        return fallback if value is None or value == "" else str(value)

    def _dedupe_key(self, event: str, payload: Dict[str, Any], explicit: Optional[str]) -> str:
        if explicit:
            return explicit
        stable = json.dumps({"event": event, "payload": payload}, sort_keys=True, default=str, separators=(",", ":"))
        return hashlib.sha256(stable.encode("utf-8")).hexdigest()

    def _is_duplicate(self, key: str) -> bool:
        now = time.monotonic()
        self._sent = {item: sent_at for item, sent_at in self._sent.items() if now - sent_at < self.dedupe_ttl_seconds}
        return key in self._sent

    def _format(self, event: str, p: Dict[str, Any]) -> str:
        if event == "ORDER_OPENED":
            return (
                "BTC TESTNET TRADE OPENED\n\n"
                f"Side: {self._value(p.get('side'))}\n"
                f"Entry: {self._value(p.get('entry'))}\n"
                f"Size: {self._value(p.get('size'))} BTC\n"
                f"Stop: {self._value(p.get('stop'))}\n"
                f"TP1: {self._value(p.get('tp1'))}\n"
                f"TP2: {self._value(p.get('tp2'))}\n\n"
                "MODE: BINANCE FUTURES TESTNET\nREAL MONEY: NO"
            )
        if event in {"SYSTEM_STARTED", "BINANCE_CONNECTED", "ORDER_CLOSED", "TAKE_PROFIT", "ORDER_REJECTED", "PROTECTION_FAILURE", "SMOKE_TEST_PASS", "SMOKE_TEST_FAIL", "ERROR"} or (
            event == "STOP_LOSS" and p.get("mode") == "TESTNET"
        ):
            label = event.replace("_", " ")
            return f"BTC TESTNET — {label}\n\n{self._value(p.get('message') or p.get('reason'))}\n\nMODE: BINANCE FUTURES TESTNET\nREAL MONEY: NO"
        if event in ("LONG_SETUP", "SHORT_SETUP", "WAIT_TRIGGER"):
            direction = self._value(p.get("direction"), "WAIT")
            return (
                f"BTCUSDT — {direction} SETUP\n\n"
                f"Price: {self._value(p.get('price'))}\nRegime: {self._value(p.get('regime'))}\n"
                f"4H: {self._value(p.get('4h'))}\n1H: {self._value(p.get('1h'))}\n"
                f"15M: {self._value(p.get('15m'))}\n5M: {self._value(p.get('5m'))}\n\n"
                f"Setup: {self._value(p.get('setup'))}\nLocation: {self._value(p.get('location'))}\n"
                f"Trigger: {self._value(p.get('trigger'))}\n\n"
                f"Funding: {self._value(p.get('funding'))}\nOI: {self._value(p.get('open_interest'))}\n"
                f"Taker: {self._value(p.get('taker'))}\nNews Risk: {self._value(p.get('news_risk'))}\n"
                f"Sentiment: {self._value(p.get('sentiment'))}\n\n"
                f"Entry: {self._value(p.get('entry'))}\nStop: {self._value(p.get('stop'))}\n"
                f"TP1: {self._value(p.get('tp1'))}\nTP2: {self._value(p.get('tp2'))}\nR:R: {self._value(p.get('rr'))}\n\n"
                f"Decision: {self._value(p.get('decision'), 'WAITING FOR TRIGGER')}\n"
                "Mode: SHADOW · NO ORDER SUBMISSION"
            )
        labels = {
            "POSITION_OPENED": "SHADOW POSITION OPENED", "POSITION_CLOSED": "SHADOW POSITION CLOSED",
            "TP1": "TP1 REACHED", "TP2": "TP2 REACHED", "STOP_LOSS": "STOP LOSS",
            "HIGH_NEWS_RISK": "HIGH NEWS RISK", "KILL_SWITCH": "KILL SWITCH ACTIVE",
            "DATA_SOURCE_ERROR": "DATA SOURCE ERROR", "DAILY_SUMMARY": "DAILY SHADOW SUMMARY",
        }
        detail = self._value(p.get("message") or p.get("reason") or p.get("summary"))
        return f"BTCUSDT — {labels[event]}\n\n{detail}\n\nMode: SHADOW · NO ORDER SUBMISSION"

    def notify(self, event: str, payload: Dict[str, Any], dedupe_key: Optional[str] = None) -> Dict[str, Any]:
        if event not in self.EVENTS:
            raise ValueError("Unsupported Telegram event")
        key = self._dedupe_key(event, payload, dedupe_key)
        if self._is_duplicate(key):
            return {"sent": False, "deduplicated": True, "event": event}
        result = self.client.send_message(self._format(event, payload))
        self._sent[key] = time.monotonic()
        return {"sent": bool(result.get("sent")), "deduplicated": False, "event": event}

    def notify_current_decision(self, snapshot: Dict[str, Any]) -> Dict[str, Any]:
        decision = snapshot.get("decision", {})
        strategy = snapshot.get("strategy", {})
        news = snapshot.get("news", {})
        system = snapshot.get("system_state", {})
        if system.get("kill_switch"):
            event = "KILL_SWITCH"
        elif news.get("news_risk") in ("HIGH", "EXTREME"):
            event = "HIGH_NEWS_RISK"
        elif snapshot.get("sources", {}).get("binance", {}).get("status") != "HEALTHY":
            event = "DATA_SOURCE_ERROR"
        elif strategy.get("setup_type") != "NONE":
            event = "LONG_SETUP" if strategy.get("direction") == "LONG" else "SHORT_SETUP" if strategy.get("direction") == "SHORT" else "WAIT_TRIGGER"
        else:
            return {"sent": False, "deduplicated": False, "event": None, "reason": "NO_NOTIFY_EVENT"}
        frames = snapshot.get("chart_intelligence", {}).get("timeframes", {})
        derivatives = snapshot.get("derivatives", {})
        plan = strategy.get("trade_plan") or {}
        payload = {
            "price": decision.get("price"), "regime": decision.get("regime"),
            "4h": frames.get("4h", {}).get("structure"), "1h": frames.get("1h", {}).get("structure"),
            "15m": frames.get("15m", {}).get("structure"), "5m": frames.get("5m", {}).get("structure"),
            "direction": strategy.get("direction"), "setup": strategy.get("setup_type"),
            "location": decision.get("location"), "trigger": strategy.get("entry_trigger_state"),
            "funding": derivatives.get("funding_rate"), "open_interest": derivatives.get("open_interest"),
            "taker": derivatives.get("taker_buy_sell_ratio"), "news_risk": news.get("news_risk"),
            "sentiment": news.get("sentiment"), "entry": plan.get("entry_price"), "stop": plan.get("stop_loss"),
            "tp1": plan.get("tp1"), "tp2": plan.get("tp2"), "rr": plan.get("risk_reward"),
            "decision": snapshot.get("final_decision"), "reason": decision.get("reason"),
            "message": "Binance market data source is degraded" if event == "DATA_SOURCE_ERROR" else None,
        }
        decision_id = snapshot.get("decision_id") or decision.get("evaluation_id") or str(decision.get("timestamp"))
        return self.notify(event, payload, dedupe_key=f"{event}:{decision_id}:{strategy.get('entry_trigger_state')}")
