"""Structured Telegram event notifications with in-process spam suppression."""

from __future__ import annotations

import hashlib
import json
import time
from typing import Any, Dict, Optional

from notifications.telegram_client import TelegramClient


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

    @staticmethod
    def _number(value: Any, decimals: int = 2, fallback: str = "—") -> str:
        if value is None or value == "":
            return fallback
        try:
            number = float(value)
        except (TypeError, ValueError):
            return str(value)
        if abs(number) >= 1000:
            return f"{number:,.{decimals}f}"
        return f"{number:.{decimals}f}"

    @staticmethod
    def _footer() -> str:
        return "🧪 MODE: BINANCE FUTURES TESTNET\n💵 REAL MONEY: NO"

    @staticmethod
    def _clean_reason(value: Any) -> str:
        text = str(value or "—").replace("_", " ").strip()
        return text if text else "—"

    def _dedupe_key(self, event: str, payload: Dict[str, Any], explicit: Optional[str]) -> str:
        if explicit:
            return explicit
        stable = json.dumps({"event": event, "payload": payload}, sort_keys=True, default=str, separators=(",", ":"))
        return hashlib.sha256(stable.encode("utf-8")).hexdigest()

    def _is_duplicate(self, key: str) -> bool:
        now = time.monotonic()
        self._sent = {item: sent_at for item, sent_at in self._sent.items() if now - sent_at < self.dedupe_ttl_seconds}
        return key in self._sent

    def _format_order_opened(self, p: Dict[str, Any]) -> str:
        side = str(p.get("side") or "UNKNOWN").upper()
        long_side = side in {"LONG", "BUY"}
        icon = "🟢" if long_side else "🔴"
        side_label = "LONG" if long_side else "SHORT"
        lines = [
            f"{icon} BTCUSDT {side_label} AÇILDI",
            "",
            f"Giriş: {self._number(p.get('entry'))} USDT",
            f"Miktar: {self._number(p.get('size'), 6)} BTC",
        ]
        if p.get("leverage") is not None:
            lines.append(f"Kaldıraç: {self._number(p.get('leverage'), 0)}x")
        if p.get("stop") is not None:
            lines.append(f"🛑 Stop: {self._number(p.get('stop'))} USDT")
        if p.get("tp1") is not None:
            lines.append(f"🎯 TP1: {self._number(p.get('tp1'))} USDT")
        if p.get("tp2") is not None:
            lines.append(f"🎯 TP2: {self._number(p.get('tp2'))} USDT")
        if p.get("rr") is not None:
            lines.append(f"R:R: {self._number(p.get('rr'))}")
        lines.extend(["", "Koruma emirleri Binance tarafında doğrulandı.", "", self._footer()])
        return "\n".join(lines)

    def _format_position_closed(self, event: str, p: Dict[str, Any]) -> str:
        icons = {"TAKE_PROFIT": "🎯", "STOP_LOSS": "🛑", "ORDER_CLOSED": "⚪"}
        labels = {"TAKE_PROFIT": "TAKE PROFIT", "STOP_LOSS": "STOP LOSS", "ORDER_CLOSED": "POZİSYON KAPANDI"}
        lines = [f"{icons.get(event, '⚪')} BTCUSDT — {labels.get(event, 'POZİSYON KAPANDI')}", ""]
        if p.get("side"):
            lines.append(f"Yön: {self._value(p.get('side'))}")
        if p.get("entry") is not None:
            lines.append(f"Giriş: {self._number(p.get('entry'))} USDT")
        if p.get("exit") is not None:
            lines.append(f"Çıkış: {self._number(p.get('exit'))} USDT")
        if p.get("size") is not None:
            lines.append(f"Miktar: {self._number(p.get('size'), 6)} BTC")
        if p.get("pnl") is not None:
            pnl = float(p.get("pnl") or 0)
            lines.append(f"PnL: {'+' if pnl > 0 else ''}{self._number(pnl)} USDT")
        detail = p.get("message") or p.get("reason")
        if detail:
            lines.extend(["", self._clean_reason(detail)])
        lines.extend(["", self._footer()])
        return "\n".join(lines)

    def _format(self, event: str, p: Dict[str, Any]) -> str:
        if event == "ORDER_OPENED":
            return self._format_order_opened(p)

        if event in {"ORDER_CLOSED", "TAKE_PROFIT"} or (event == "STOP_LOSS" and p.get("mode") == "TESTNET"):
            return self._format_position_closed(event, p)

        if event == "SYSTEM_STARTED":
            return "\n".join([
                "🤖 BTC BOT AKTİF",
                "",
                "Otomatik işlem döngüsü çalışıyor.",
                "Sinyal gelene kadar işlem açılmaz.",
                "",
                self._footer(),
            ])

        if event == "BINANCE_CONNECTED":
            return "\n".join([
                "🔗 BINANCE TESTNET BAĞLANDI",
                "",
                "Hesap, bakiye, pozisyon ve emir kanalı hazır.",
                "",
                self._footer(),
            ])

        if event == "SMOKE_TEST_PASS":
            return "\n".join([
                "✅ SMOKE TEST BAŞARILI",
                "",
                "Test BUY: PASS",
                "Pozisyon doğrulama: PASS",
                "Test CLOSE: PASS",
                "Final pozisyon: FLAT",
                "",
                "Otomatik strateji döngüsü başlatılabilir.",
                "",
                self._footer(),
            ])

        if event == "SMOKE_TEST_FAIL":
            reason = self._clean_reason(p.get("message") or p.get("reason"))
            return "\n".join([
                "❌ SMOKE TEST BAŞARISIZ",
                "",
                f"Sebep: {reason}",
                "Otomatik işlem döngüsü başlatılmadı.",
                "",
                self._footer(),
            ])

        if event == "PROTECTION_FAILURE":
            return "\n".join([
                "🚨 POZİSYON KORUMASI KURULAMADI",
                "",
                self._clean_reason(p.get("message") or p.get("reason")),
                "Pozisyonu FLAT duruma getirme işlemi başlatıldı.",
                "",
                self._footer(),
            ])

        if event == "ORDER_REJECTED":
            return "\n".join([
                "⚠️ EMİR GÖNDERİLMEDİ",
                "",
                f"Sebep: {self._clean_reason(p.get('message') or p.get('reason'))}",
                "",
                self._footer(),
            ])

        if event == "KILL_SWITCH":
            return "\n".join([
                "🛑 KILL SWITCH AKTİF",
                "",
                self._clean_reason(p.get("reason") or p.get("message")),
                "Yeni pozisyon açılması engellendi.",
                "",
                self._footer(),
            ])

        if event == "ERROR":
            return "\n".join([
                "❌ TESTNET ÇALIŞMA HATASI",
                "",
                self._clean_reason(p.get("message") or p.get("reason")),
                "",
                self._footer(),
            ])

        if event in ("LONG_SETUP", "SHORT_SETUP", "WAIT_TRIGGER"):
            direction = self._value(p.get("direction"), "WAIT").upper()
            icon = "🟢" if direction == "LONG" else "🔴" if direction == "SHORT" else "🟡"
            lines = [
                f"{icon} BTCUSDT SİNYAL ADAYI — {direction}",
                "",
                f"Fiyat: {self._number(p.get('price'))} USDT",
                f"Rejim: {self._value(p.get('regime'))}",
                f"Setup: {self._value(p.get('setup'))}",
                f"Trigger: {self._value(p.get('trigger'))}",
                f"Konum: {self._value(p.get('location'))}",
                f"4H / 1H / 15M / 5M: {self._value(p.get('4h'))} / {self._value(p.get('1h'))} / {self._value(p.get('15m'))} / {self._value(p.get('5m'))}",
            ]
            if p.get("entry") is not None:
                lines.extend([
                    "",
                    f"Giriş: {self._number(p.get('entry'))}",
                    f"Stop: {self._number(p.get('stop'))}",
                    f"TP1: {self._number(p.get('tp1'))}",
                    f"TP2: {self._number(p.get('tp2'))}",
                    f"R:R: {self._number(p.get('rr'))}",
                ])
            lines.extend(["", f"Karar: {self._value(p.get('decision'), 'WAIT')}", "Bu bildirim tek başına emir değildir.", "", self._footer()])
            return "\n".join(lines)

        if event == "HIGH_NEWS_RISK":
            return "\n".join([
                "📰 YÜKSEK HABER RİSKİ",
                "",
                self._clean_reason(p.get("message") or p.get("reason") or "Yeni girişler için haber riski yüksek."),
                "",
                self._footer(),
            ])

        if event == "DATA_SOURCE_ERROR":
            return "\n".join([
                "📡 VERİ KAYNAĞI HATASI",
                "",
                self._clean_reason(p.get("message") or p.get("reason")),
                "Eksik veri sahte nötr değerle doldurulmadı.",
                "",
                self._footer(),
            ])

        labels = {
            "POSITION_OPENED": "SHADOW POZİSYON AÇILDI",
            "POSITION_CLOSED": "SHADOW POZİSYON KAPANDI",
            "TP1": "TP1 GÖRÜLDÜ",
            "TP2": "TP2 GÖRÜLDÜ",
            "STOP_LOSS": "STOP LOSS",
            "DAILY_SUMMARY": "GÜNLÜK ÖZET",
        }
        detail = self._clean_reason(p.get("message") or p.get("reason") or p.get("summary"))
        return f"BTCUSDT — {labels.get(event, event.replace('_', ' '))}\n\n{detail}\n\nMod: SHADOW · Emir gönderimi yok"

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
        binance_status = str(snapshot.get("sources", {}).get("binance", {}).get("status") or "UNKNOWN").upper()
        trigger = str(strategy.get("entry_trigger_state") or "")

        if system.get("kill_switch"):
            event = "KILL_SWITCH"
        elif news.get("news_risk") in ("HIGH", "EXTREME"):
            event = "HIGH_NEWS_RISK"
        elif binance_status in {"UNAVAILABLE", "OFFLINE", "ERROR"}:
            event = "DATA_SOURCE_ERROR"
        elif strategy.get("setup_type") != "NONE" and trigger == "ENTRY_READY":
            event = "LONG_SETUP" if strategy.get("direction") == "LONG" else "SHORT_SETUP" if strategy.get("direction") == "SHORT" else None
            if event is None:
                return {"sent": False, "deduplicated": False, "event": None, "reason": "NO_NOTIFY_EVENT"}
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
            "message": "Binance market data unavailable" if event == "DATA_SOURCE_ERROR" else None,
        }
        decision_id = snapshot.get("decision_id") or decision.get("evaluation_id") or str(decision.get("timestamp"))
        return self.notify(event, payload, dedupe_key=f"{event}:{decision_id}:{strategy.get('entry_trigger_state')}")
