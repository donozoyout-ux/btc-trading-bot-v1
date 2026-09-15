"""Authenticated Telegram operator surface for the Render TESTNET bot.

Only the configured TELEGRAM_CHAT_ID can use commands. Read-only commands are
always safe. Manual mutation is limited to TESTNET position close, an operator
entry lock, and a tightly bounded smoke BUY -> reduce-only close. It can never
open a discretionary strategy position, reverse direction, change leverage, or
trade MAINNET.
"""

from __future__ import annotations

import hmac
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, Optional
from zoneinfo import ZoneInfo

from loguru import logger

from data.binance_execution_client import BinanceFuturesExecutionClient, ExecutionError
from execution.operator_control import OPERATOR_EXECUTION_MUTEX, OperatorControlState
from notifications.telegram_client import TelegramClient, TelegramError
from storage.state_repository import create_state_repository


class TelegramCommandService:
    COMMANDS = (
        ("yardim", "Komut listesini göster"),
        ("durum", "Botun çalışma durumunu göster"),
        ("hesap", "TESTNET bakiye ve günlük özeti göster"),
        ("pozisyon", "Açık BTCUSDT pozisyonunu göster"),
        ("emirler", "Açık STOP ve hedef emirlerini göster"),
        ("sinyal", "Güncel strateji kararını göster"),
        ("neden", "Neden işlem açılmadığını göster"),
        ("ai", "AI gölge piyasa analizini göster"),
        ("haber", "Güncel haber risk analizini göster"),
        ("risk", "Risk ve koruma durumunu göster"),
        ("kaynaklar", "Veri kaynaklarının durumunu göster"),
        ("piyasa", "Piyasa ve türev bağlamını göster"),
        ("rapor", "Bugünün performans raporunu gönder"),
        ("hazirlik", "Canlıya hazırlık skorunu göster"),
        ("manuel", "Otomatik yeni girişleri kilitle"),
        ("devam", "Otomatik yeni girişleri tekrar aç"),
        ("sat", "Açık TESTNET pozisyonunu marketten kapat"),
        ("kapat", "Açık TESTNET pozisyonunu marketten kapat"),
        ("smoke", "Kontrollü TESTNET aç-kapat testini çalıştır"),
        ("ping", "Telegram bağlantısını test et"),
    )

    COMMAND_ALIASES = {
        "start": "yardim", "help": "yardim",
        "status": "durum", "account": "hesap", "position": "pozisyon",
        "orders": "emirler", "signal": "sinyal", "why": "neden", "sources": "kaynaklar",
        "analysis": "ai", "analiz": "ai", "news": "haber",
        "market": "piyasa", "report": "rapor", "daily": "rapor", "gunluk": "rapor",
        "readiness": "hazirlik", "live": "hazirlik",
        "pause": "manuel", "resume": "devam", "manual": "manuel",
        "close": "kapat", "sell": "sat",
    }

    MUTATING_COMMANDS = {
        "buy", "long", "short", "closeall", "cancel", "cancelall",
        "pause", "resume", "stopbot", "startbot", "kill", "leverage",
    }

    def __init__(
        self,
        settings,
        *,
        dashboard_provider: Callable[[], Any],
        execution_status_provider: Callable[[], Dict[str, Any]],
        telegram_client: Optional[TelegramClient] = None,
        execution_client: Optional[BinanceFuturesExecutionClient] = None,
        daily_report_state=None,
        operator_control_state=None,
        smoke_test_runner: Optional[Callable[[], Dict[str, Any]]] = None,
        sleep_fn=time.sleep,
    ) -> None:
        self.settings = settings
        self.dashboard_provider = dashboard_provider
        self.execution_status_provider = execution_status_provider
        self.telegram = telegram_client or TelegramClient(
            settings.TELEGRAM_BOT_TOKEN,
            settings.TELEGRAM_CHAT_ID,
            enabled=settings.TELEGRAM_ENABLED,
        )
        self.sleep_fn = sleep_fn
        self.authorized_chat_id = str(settings.TELEGRAM_CHAT_ID or "").strip()
        self.execution = execution_client
        if self.execution is None and settings.BINANCE_TESTNET and settings.BINANCE_API_KEY and settings.BINANCE_API_SECRET:
            self.execution = BinanceFuturesExecutionClient(
                settings.BINANCE_API_KEY,
                settings.BINANCE_API_SECRET,
                testnet=True,
                recv_window=settings.BINANCE_RECV_WINDOW,
            )
        self._offset: Optional[int] = None
        self.daily_report_enabled = bool(getattr(settings, "TELEGRAM_DAILY_REPORT_ENABLED", True))
        self.daily_report_hour = int(getattr(settings, "TELEGRAM_DAILY_REPORT_HOUR", 23))
        self.daily_report_minute = int(getattr(settings, "TELEGRAM_DAILY_REPORT_MINUTE", 55))
        self.daily_report_timezone = ZoneInfo("Europe/Istanbul")
        report_path = Path(getattr(settings, "JOURNAL_DIR", "journal_logs")) / "telegram_daily_report_state.json"
        self.daily_report_state = daily_report_state or create_state_repository(report_path)
        self.manual_trading_enabled = bool(getattr(settings, "TELEGRAM_MANUAL_TRADING_ENABLED", False))
        self.operator_control = operator_control_state or OperatorControlState(getattr(settings, "JOURNAL_DIR", "journal_logs"))
        self.smoke_test_runner = smoke_test_runner
        self._last_smoke_at = 0.0
        self._smoke_cooldown_seconds = 300.0
        self._update_lock = threading.Lock()
        self._last_update_id = -1

    @property
    def enabled(self) -> bool:
        return bool(self.settings.TELEGRAM_ENABLED and self.telegram.configured and self.authorized_chat_id)

    @staticmethod
    def _num(value: Any, digits: int = 2) -> str:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return "—"
        return f"{number:,.{digits}f}"

    @staticmethod
    def _text(value: Any, fallback: str = "—") -> str:
        text = str(value or "").strip()
        return text if text else fallback

    def _send(self, text: str) -> None:
        self.telegram.send_message(text[:4096])

    @staticmethod
    def _signed_num(value: Any, digits: int = 2) -> str:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return "—"
        prefix = "+" if number > 0 else ""
        return f"{prefix}{number:,.{digits}f}"

    def _dashboard(self):
        return self.dashboard_provider()

    def _snapshot(self) -> Dict[str, Any]:
        runtime = self._dashboard()
        if runtime is None:
            return {}
        try:
            return runtime.snapshot(force=False) or {}
        except Exception:
            return {}

    def _market_status(self) -> Dict[str, Any]:
        runtime = self._dashboard()
        status_fn = getattr(getattr(runtime, "binance", None), "status", None)
        if not callable(status_fn):
            return {}
        try:
            return dict(status_fn())
        except Exception:
            return {}

    def _help(self) -> str:
        lines = ["🤖 BTC BOT — KOMUTLAR", ""]
        for command, description in self.COMMANDS:
            lines.append(f"/{command} — {description}")
        lines.extend([
            "",
            "🔒 Komutlar yalnızca tanımlı Telegram hesabında çalışır.",
            "🧪 Mod: Binance Futures TESTNET",
            "💵 Gerçek para: KAPALI",
            "🕹️ /sat veya /kapat: mevcut TESTNET pozisyonunu kapatır.",
            "🧪 /smoke: yalnızca FLAT hesapta kontrollü TESTNET BUY → reduce-only close testi yapar.",
            "🔒 /manuel: yeni otomatik girişleri kilitler. /devam: tekrar açar.",
            "⚠️ Telegram kalıcı pozisyon AÇMAZ veya yön tersine çevirmez; /smoke test pozisyonunu kapatır.",
        ])
        return "\n".join(lines)

    def _status(self) -> str:
        status = self.execution_status_provider() or {}
        market = self._market_status()
        operator = self.operator_control.read()
        return "\n".join([
            "🤖 BTC BOT DURUMU",
            "",
            f"Bot: {self._text(status.get('bot_status'), 'BİLİNMİYOR')}",
            f"İşlem motoru: {self._text(status.get('execution_thread'), 'BİLİNMİYOR')}",
            f"Son sonuç: {self._text(status.get('last_execution_result'))}",
            f"Başlangıç testi: {self._text(status.get('smoke_test'), 'ÇALIŞTIRILMADI')}",
            f"Hata: {self._text(status.get('execution_error'), 'YOK')}",
            f"Piyasa veri kaynağı: {self._text(market.get('market_data_source'), 'BİLİNMİYOR')}",
            f"İşleme uygun veri: {'EVET' if market.get('market_data_trading_safe') else 'HAYIR'}",
            f"Operatör giriş kilidi: {'AKTİF' if operator.get('manual_entry_lock') else 'KAPALI'}",
            "",
            "🧪 Binance Futures TESTNET",
            "💵 Gerçek para: KAPALI",
        ])

    def _assert_manual_boundary(self) -> None:
        if not self.manual_trading_enabled:
            raise ExecutionError("TELEGRAM_MANUAL_TRADING_DISABLED")
        if str(getattr(self.settings, "ENV", "")).strip().lower() != "testnet":
            raise ExecutionError("MAINNET_EXECUTION_BLOCKED")
        if not bool(getattr(self.settings, "BINANCE_TESTNET", False)):
            raise ExecutionError("MAINNET_EXECUTION_BLOCKED")
        if not bool(getattr(self.settings, "ORDER_SUBMISSION_ENABLED", False)):
            raise ExecutionError("ORDER_SUBMISSION_DISABLED")
        if bool(getattr(self.settings, "ACCOUNT_READ_ONLY", True)):
            raise ExecutionError("ACCOUNT_READ_ONLY")
        if bool(getattr(self.settings, "SHADOW_MODE", True)):
            raise ExecutionError("SHADOW_MODE_ACTIVE")
        if self.execution is None or not bool(getattr(self.execution, "testnet", True)):
            raise ExecutionError("TESTNET_CLIENT_REQUIRED")

    def _manual_lock(self) -> str:
        self.operator_control.lock_entries(locked_by="TELEGRAM", reason="OPERATOR_MANUAL_MODE")
        return "\n".join([
            "🔒 MANUEL OPERATÖR MODU", "",
            "Yeni otomatik girişler kilitlendi.",
            "Açık pozisyon varsa bot STOP/TP ve kâr korumasını yönetmeye devam eder.",
            "Tekrar otomatik giriş için /devam yaz.",
        ])

    def _manual_resume(self) -> str:
        self.operator_control.unlock_entries(unlocked_by="TELEGRAM")
        return "\n".join([
            "▶️ OTOMATİK GİRİŞLER AÇILDI", "",
            "Bot yeni uygun sinyallerde tekrar işlem açabilir.",
            "Mevcut TESTNET güvenlik kuralları aynen devam eder.",
        ])

    def _manual_smoke(self) -> str:
        self._assert_manual_boundary()
        if self.smoke_test_runner is None:
            raise ExecutionError("SMOKE_TEST_UNAVAILABLE")

        now = time.time()
        if self._last_smoke_at and now - self._last_smoke_at < self._smoke_cooldown_seconds:
            raise ExecutionError("SMOKE_TEST_COOLDOWN")

        # Refuse immediately when an existing strategy position is open.
        # The smoke runtime also re-checks this under the shared execution mutex.
        if self.execution is not None:
            position = self.execution.get_position("BTCUSDT")
            if float(position.get("position_amt") or 0) != 0:
                raise ExecutionError("POSITION_ALREADY_OPEN")

        with OPERATOR_EXECUTION_MUTEX:
            result = self.smoke_test_runner()

        if result.get("status") != "PASS" or result.get("final_position") != "FLAT":
            raise ExecutionError("SMOKE_TEST_FAILED")

        self._last_smoke_at = now
        return "\n".join([
            "✅ TESTNET SMOKE TEST PASS", "",
            "Test BUY: PASS",
            "Pozisyon doğrulama: PASS",
            "Reduce-only close: PASS",
            "Final pozisyon: FLAT",
            "",
            "💵 Gerçek para: KAPALI",
        ])

    def _manual_close(self) -> str:
        self._assert_manual_boundary()
        # Lock first so the automatic loop cannot reopen immediately after the
        # operator flattens the account. A failed close intentionally leaves the
        # lock active (fail-closed for new entries).
        self.operator_control.lock_entries(locked_by="TELEGRAM", reason="OPERATOR_MANUAL_CLOSE")
        with OPERATOR_EXECUTION_MUTEX:
            before = self.execution.get_position("BTCUSDT")
            amount = float(before.get("position_amt") or 0)
            if amount == 0:
                return "\n".join([
                    "⚪ BTCUSDT zaten FLAT.",
                    "🔒 Yeni otomatik girişler kilitli.",
                    "Devam etmek için /devam yaz.",
                ])

            side = "LONG" if amount > 0 else "SHORT"
            quantity = abs(amount)
            order = self.execution.close_position_market("BTCUSDT")
            after = self.execution.get_position("BTCUSDT")
            if float(after.get("position_amt") or 0) != 0:
                raise ExecutionError("MANUAL_CLOSE_POSITION_NOT_FLAT")

            # Flat account must not keep stale protective orders. Cleanup is
            # best-effort here; the normal execution loop also reconciles FLAT state.
            try:
                self.execution.cancel_all_algo_open_orders("BTCUSDT")
            except Exception:
                pass
            try:
                for row in list(self.execution.get_open_orders("BTCUSDT")):
                    if bool(row.get("reduceOnly")) and row.get("orderId") is not None:
                        self.execution.cancel_order("BTCUSDT", int(row["orderId"]))
            except Exception:
                pass

            fill = (order or {}).get("average_fill_price")
            return "\n".join([
                "✅ TESTNET POZİSYON KAPATILDI", "",
                f"Yön: {side}",
                f"Kapatılan miktar: {self._num(quantity, 6)} BTC",
                f"Market fill: {self._num(fill) if fill else '—'} USDT",
                "Durum: FLAT",
                "",
                "🔒 Yeni otomatik girişler kilitlendi.",
                "Tekrar otomatik giriş için /devam yaz.",
                "🧪 Binance Futures TESTNET · Gerçek para KAPALI",
            ])

    def _account(self) -> str:
        if self.execution is None:
            return "⚠️ Binance TESTNET hesap bağlantısı kullanılamıyor."
        account = self.execution.get_account_summary()
        return "\n".join([
            "💼 BINANCE TESTNET HESAP",
            "",
            f"Cüzdan: {self._num(account.get('wallet_balance'))} USDT",
            f"Kullanılabilir: {self._num(account.get('available_balance'))} USDT",
            f"Marjin bakiyesi: {self._num(account.get('margin_balance'))} USDT",
            f"Açık K/Z: {self._signed_num(account.get('unrealized_pnl'))} USDT",
            f"Açık pozisyon: {len(account.get('positions') or [])}",
            f"Açık emir: {len(account.get('open_orders') or [])}",
            "",
            "💵 Gerçek para: KAPALI",
        ])

    @staticmethod
    def _trigger_price(order: Dict[str, Any]) -> Optional[float]:
        for key in ("triggerPrice", "stopPrice", "stop_price", "price"):
            try:
                value = float(order.get(key) or 0)
            except (TypeError, ValueError):
                continue
            if value > 0:
                return value
        return None

    def _position(self) -> str:
        if self.execution is None:
            return "⚠️ Binance TESTNET hesap bağlantısı kullanılamıyor."
        position = self.execution.get_position("BTCUSDT")
        amount = float(position.get("position_amt") or 0)
        if amount == 0:
            return "⚪ BTCUSDT POZİSYON: FLAT\n\nAçık pozisyon yok."

        side = self._text(position.get("side"), "UNKNOWN")
        entry = float(position.get("entry_price") or 0)
        mark = float(position.get("mark_price") or 0)
        pnl = float(position.get("unrealized_pnl") or 0)
        leverage = position.get("leverage")
        algo_orders = self.execution.get_open_algo_orders("BTCUSDT")
        stop = None
        targets = []
        for order in algo_orders:
            order_type = str(order.get("orderType") or order.get("type") or "").upper()
            trigger = self._trigger_price(order)
            if trigger is None:
                continue
            if "TAKE_PROFIT" in order_type:
                targets.append(trigger)
            elif "STOP" in order_type:
                stop = trigger
        targets.sort(key=lambda value: abs(value - entry) if entry else value)
        tp1 = targets[0] if targets else None
        tp2 = targets[1] if len(targets) > 1 else None
        pnl_icon = "🟢" if pnl > 0 else "🔴" if pnl < 0 else "⚪"
        return "\n".join([
            f"📊 BTCUSDT {side}",
            "",
            f"Giriş: {self._num(entry)} USDT",
            f"Mark: {self._num(mark)} USDT",
            f"Miktar: {self._num(abs(amount), 6)} BTC",
            f"Kaldıraç: {self._text(leverage)}x",
            f"{pnl_icon} Canlı PnL: {self._num(pnl)} USDT",
            f"🛑 Stop: {self._num(stop) if stop is not None else '—'} USDT",
            f"🎯 TP1: {self._num(tp1) if tp1 is not None else '—'} USDT",
            f"🎯 TP2: {self._num(tp2) if tp2 is not None else '—'} USDT",
            "",
            "🧪 TESTNET · GERÇEK PARA: KAPALI",
        ])

    def _orders(self) -> str:
        if self.execution is None:
            return "⚠️ Binance TESTNET hesap bağlantısı kullanılamıyor."
        orders = list(self.execution.get_open_orders("BTCUSDT")) + list(self.execution.get_open_algo_orders("BTCUSDT"))
        if not orders:
            return "📋 AÇIK EMİRLER\n\nAçık BTCUSDT emri yok."
        lines = ["📋 AÇIK BTCUSDT EMİRLERİ", ""]
        for index, order in enumerate(orders[:10], start=1):
            order_type = self._text(order.get("orderType") or order.get("type"), "UNKNOWN")
            side = self._text(order.get("side"), "—")
            status = self._text(order.get("algoStatus") or order.get("status"), "—")
            trigger = self._trigger_price(order)
            order_id = order.get("algoId") or order.get("orderId") or "—"
            lines.append(f"{index}. {side} · {order_type} · {status} · Trigger {self._num(trigger) if trigger is not None else '—'} · ID {order_id}")
        if len(orders) > 10:
            lines.append(f"… +{len(orders) - 10} emir")
        lines.extend(["", "🧪 TESTNET · GERÇEK PARA: KAPALI"])
        return "\n".join(lines)

    def _signal(self) -> str:
        snapshot = self._snapshot()
        if not snapshot:
            return "⚠️ Güncel strateji snapshot'ı alınamadı."
        decision = snapshot.get("decision") or {}
        strategy = snapshot.get("strategy") or {}
        blockers = strategy.get("blocking_reasons") or strategy.get("blockers") or []
        if isinstance(blockers, (list, tuple)):
            blockers_text = ", ".join(str(item) for item in blockers[:4]) or "YOK"
        else:
            blockers_text = self._text(blockers, "YOK")
        return "\n".join([
            "🧠 GÜNCEL STRATEJİ KARARI",
            "",
            f"Son karar: {self._text(snapshot.get('final_decision'), 'BEKLE')}",
            f"Fiyat: {self._num(decision.get('price'))} USDT",
            f"Rejim: {self._text(decision.get('regime'))}",
            f"Güven: {self._text(decision.get('confidence'))}",
            f"Kurulum: {self._text(strategy.get('setup_type'), 'YOK')}",
            f"Yön: {self._text(strategy.get('direction'), 'NONE')}",
            f"Tetik: {self._text(strategy.get('entry_trigger_state'), 'BEKLE')}",
            f"İşleme uygun: {'EVET' if strategy.get('eligible') else 'HAYIR'}",
            f"Engel: {blockers_text}",
        ])

    def _why(self) -> str:
        snapshot = self._snapshot()
        if not snapshot:
            return "⚠️ Güncel strateji snapshot'ı alınamadı."
        decision = snapshot.get("decision") or {}
        strategy = snapshot.get("strategy") or {}
        quality = strategy.get("entry_quality_assessment") or decision.get("entry_quality_assessment") or {}
        blockers = strategy.get("hard_blockers") or strategy.get("blocking_reasons") or []
        reasons = strategy.get("reasons") or []
        if isinstance(blockers, (list, tuple)):
            blocker_text = ", ".join(str(x) for x in blockers) or "YOK"
        else:
            blocker_text = self._text(blockers, "YOK")
        if isinstance(reasons, (list, tuple)):
            reason_text = " | ".join(str(x) for x in reasons[:3]) or self._text(decision.get("reason"), "Aktif setup yok")
        else:
            reason_text = self._text(reasons, self._text(decision.get("reason"), "Aktif setup yok"))
        assessment = decision.get("risk_assessment") or {}
        return "\n".join([
            "🔎 NEDEN İŞLEM AÇILMADI?",
            "",
            f"Final karar: {self._text(snapshot.get('final_decision'), 'NO_TRADE')}",
            f"Rejim: {self._text(decision.get('regime'))}",
            f"Setup: {self._text(strategy.get('setup_type'), 'NONE')}",
            f"Yön adayı: {self._text(strategy.get('direction'), 'WAIT')}",
            f"5M tetik: {self._text(strategy.get('entry_trigger_state'), 'NO_SETUP')}",
            f"Entry quality: {self._text(quality.get('decision'), 'BEKLE')}",
            f"Risk: {self._text(decision.get('risk_status'), 'BEKLE')}",
            f"Pozisyon boyutu: {self._num(assessment.get('position_size_btc'), 6)} BTC",
            f"Engel: {blocker_text}",
            f"Sebep: {reason_text}",
            "",
            f"Kaldıraç tavanı: {int(getattr(self.settings, 'MAX_ACCOUNT_LEVERAGE', 1))}x",
            (
                "Execution whitelist: "
                f"{self._text(getattr(self.settings, 'EVIDENCE_ALLOWED_REGIME', '—'))} + "
                f"{self._text(getattr(self.settings, 'EVIDENCE_ALLOWED_SETUP', '—'))} + "
                f"{self._text(getattr(self.settings, 'EVIDENCE_ALLOWED_DIRECTION', '—'))}"
                if getattr(self.settings, "EVIDENCE_EXECUTION_GATE_ENABLED", False)
                else "Execution whitelist: KAPALI"
            ),
            (
                "Futures-native veri zorunlu: EVET"
                if getattr(self.settings, "EVIDENCE_REQUIRE_FUTURES_NATIVE", False)
                else "Futures-native veri zorunlu: HAYIR"
            ),
            "Not: Analiz motoru diğer yön/setup sinyallerini göstermeye devam eder; whitelist dışı sinyaller emir açamaz.",
        ])

    def _ai(self) -> str:
        snapshot = self._snapshot()
        if not snapshot:
            return "⚠️ Güncel AI snapshot'ı alınamadı."
        ai = snapshot.get("ai_analyst") or {}
        source = (snapshot.get("sources") or {}).get("ai") or {}
        if ai.get("status") != "AVAILABLE":
            return "\n".join([
                "🤖 AI MARKET ANALYST",
                "",
                f"Durum: {self._text(ai.get('status') or source.get('status'), 'UNAVAILABLE')}",
                f"Yapılandırılmış: {'EVET' if source.get('configured') else 'HAYIR'}",
                f"Model: {self._text(source.get('model'), '—')}",
                "Execution authority: YOK",
                "",
                "AI kullanılamasa da deterministik strateji ve risk motoru çalışmaya devam eder.",
            ])
        confirmations = ai.get("confirmations") or []
        conflicts = ai.get("conflicts") or []
        risk_notes = ai.get("risk_notes") or []
        invalidation = ai.get("invalidation_watch") or []
        return "\n".join([
            "🤖 AI MARKET ANALYST · SHADOW",
            "",
            f"Bias: {self._text(ai.get('market_bias'))}",
            f"Setup quality: {self._text(ai.get('setup_quality'))}/100",
            f"Görüş: {self._text(ai.get('trade_opinion'))}",
            f"Güven: %{self._text(ai.get('confidence'))}",
            f"En iyi setup: {self._text(ai.get('best_setup'))}",
            "",
            f"📌 Market: {self._text(ai.get('market_view'))}",
            f"✅ Onaylar: {' | '.join(str(x) for x in confirmations[:4]) or 'YOK'}",
            f"⚠️ Çatışmalar: {' | '.join(str(x) for x in conflicts[:4]) or 'YOK'}",
            f"🛡️ Risk: {' | '.join(str(x) for x in risk_notes[:4]) or 'YOK'}",
            f"📰 Haber: {self._text(ai.get('news_summary'))}",
            f"📊 Türevler: {self._text(ai.get('derivatives_summary'))}",
            f"👀 İzlenecek invalidation: {' | '.join(str(x) for x in invalidation[:3]) or 'YOK'}",
            "",
            f"Yorum: {self._text(ai.get('decision_explanation'))}",
            "🔒 Execution authority: YOK",
        ])

    def _news(self) -> str:
        snapshot = self._snapshot()
        if not snapshot:
            return "⚠️ Güncel haber snapshot'ı alınamadı."
        news = snapshot.get("news") or {}
        events = news.get("important_events") or []
        clusters = news.get("event_clusters") or []
        lines = [
            "📰 BTC HABER ANALİZİ",
            "",
            f"Durum: {self._text(news.get('status'), 'UNAVAILABLE')}",
            f"Risk: {self._text(news.get('news_risk'))} · Skor {self._text(news.get('news_risk_score'))}/100",
            f"Trade riski: {self._text(news.get('trade_risk'))}",
            f"Sentiment: {self._text(news.get('sentiment'))} · {self._text(news.get('sentiment_score'))}",
        ]
        if clusters:
            lines.extend(["", "📚 Olay kümeleri:"])
            for row in clusters[:4]:
                lines.append(
                    f"• {self._text(row.get('category'))}: {row.get('count', 0)} haber · "
                    f"impact {self._num(row.get('max_impact_score'), 1)}"
                )
        if events:
            lines.extend(["", "🔥 Önemli haberler:"])
            for row in events[:5]:
                age = row.get("age_hours")
                age_text = f"{self._num(age, 1)}s" if age is not None else "?"
                lines.append(
                    f"• [{self._text(row.get('category'))}] "
                    f"{self._text(row.get('title'))} · impact {self._num(row.get('impact_score'), 0)} · {age_text}"
                )
        else:
            lines.extend(["", "Önemli güncel haber olayı yok."])
        lines.extend(["", "ℹ️ Haber motoru tek başına emir açamaz."])
        return "\n".join(lines)

    def _risk(self) -> str:
        snapshot = self._snapshot()
        if not snapshot:
            return "⚠️ Güncel risk snapshot'ı alınamadı."
        decision = snapshot.get("decision") or {}
        assessment = decision.get("risk_assessment") or {}
        strategy = snapshot.get("strategy") or {}
        plan = strategy.get("trade_plan") or decision.get("trade_plan") or {}
        system = snapshot.get("system_state") or {}
        return "\n".join([
            "🛡️ RISK DURUMU",
            "",
            f"Risk durumu: {self._text(decision.get('risk_status'), 'BEKLE')}",
            f"R:R: {self._text(plan.get('risk_reward'), '—')}",
            f"Pozisyon boyutu: {self._num(assessment.get('position_size_btc'), 6)} BTC",
            f"Acil durdurma: {'AKTİF' if system.get('kill_switch') else 'GÜVENLİ'}",
            f"Günlük zarar koruması: {self._text(system.get('daily_loss_guard'), '—')}",
            f"Kayıp serisi koruması: {self._text(system.get('loss_streak_guard'), '—')}",
            "",
            "Yeni emir yetkisi bu komutta yoktur.",
        ])

    def _sources(self) -> str:
        market = self._market_status()
        snapshot = self._snapshot()
        news = snapshot.get("news") or {}
        ai = snapshot.get("ai_analyst") or {}
        derivatives = snapshot.get("derivatives") or {}
        sources = snapshot.get("sources") or {}
        derivatives_status = derivatives.get("display_status") or derivatives.get("status") or market.get("derivatives_status") or "UNKNOWN"
        binance_status = "FALLBACK" if market.get("fallback_active") or "FALLBACK" in str(market.get("market_data_source", "")) else "CONNECTED"
        trading_safe = bool(market.get("market_data_trading_safe", (sources.get("binance") or {}).get("market_data_trading_safe", False)))
        cmc_status = (sources.get("coinmarketcap") or {}).get("status") or "UNAVAILABLE"
        if cmc_status == "HEALTHY":
            cmc_status = "CONNECTED"
        return "\n".join([
            "📡 VERİ KAYNAKLARI",
            "",
            f"Binance: {binance_status}",
            f"İşleme uygun piyasa verisi: {'EVET' if trading_safe else 'HAYIR'}",
            f"CoinGlass: {self._text((sources.get('coinglass') or {}).get('status'), 'UNAVAILABLE')}",
            f"CoinMarketCap: {self._text(cmc_status, 'UNAVAILABLE')}",
            f"Türev verileri: {self._text(derivatives_status, 'BİLİNMİYOR')}",
            f"Haberler: {self._text(news.get('status'), 'BİLİNMİYOR')}",
            f"AI: {self._text(ai.get('status'), 'DISABLED')}",
        ])

    def _market(self) -> str:
        snapshot = self._snapshot()
        macro = snapshot.get("macro_context") or {}
        derivatives = snapshot.get("derivatives") or {}
        def value(name):
            field = derivatives.get(name)
            return field.get("value") if isinstance(field, dict) else field
        def source(name):
            field = derivatives.get(name)
            return field.get("source") if isinstance(field, dict) else None
        def market_num(raw, digits=2):
            return "VERİ YOK" if raw is None else self._num(raw, digits)
        oi_value = value("open_interest")
        oi_source = source("open_interest")
        if oi_source == "COINGLASS":
            oi_text = f"${market_num(oi_value, 0)} [COINGLASS]"
        elif oi_source == "BINANCE_TESTNET_FALLBACK":
            oi_text = f"{market_num(oi_value, 2)} BTC [TESTNET FALLBACK · DISPLAY ONLY]"
        else:
            oi_text = f"{market_num(oi_value, 2)} BTC"
        funding_text = market_num(value("funding_rate"), 6)
        if source("funding_rate") == "BINANCE_TESTNET_FALLBACK":
            funding_text += " [TESTNET FALLBACK · DISPLAY ONLY]"
        return "\n".join([
            "🌍 PİYASA ÖZETİ", "",
            f"BTC dominansı: {market_num(macro.get('btc_dominance'))}%",
            f"Toplam piyasa değeri: ${market_num(macro.get('total_market_cap_usd'), 0)}",
            f"24s hacim: ${market_num(macro.get('total_volume_24h_usd'), 0)}",
            f"Açık pozisyon hacmi: {oi_text}",
            f"Fonlama: {funding_text}",
            f"Long/Short oranı: {market_num(value('long_short_ratio'), 3)}",
            f"Alıcı/Satıcı akışı: {market_num(value('taker_buy_ratio'), 3)}",
            f"CoinGlass likidasyon: ${market_num(value('liquidations_24h'), 0)}",
            "", "🔒 Salt okunur · TESTNET işlem güvenliği değişmedi",
        ])

    def _daily_report(self) -> str:
        runtime = self._dashboard()
        snapshot = self._snapshot()
        account = {}
        if runtime is not None:
            account_fn = getattr(runtime, "account", None)
            if callable(account_fn):
                try:
                    account = account_fn(force=True) or {}
                except Exception:
                    account = {}
        if not account and self.execution is not None:
            try:
                raw = self.execution.get_account_summary() or {}
                account = {
                    "wallet_balance_usdt": raw.get("wallet_balance"),
                    "available_balance_usdt": raw.get("available_balance"),
                    "unrealized_pnl_usdt": raw.get("unrealized_pnl"),
                }
            except Exception:
                account = {}

        daily = account.get("daily_performance") or {}
        ledger = account.get("daily_trade_ledger") or snapshot.get("daily_trade_ledger") or {}
        report_fn = getattr(runtime, "trade_report", None) if runtime is not None else None
        if callable(report_fn):
            try:
                detailed_report = report_fn(force=True) or {}
                daily = detailed_report.get("daily_performance") or daily
                ledger = detailed_report.get("daily_trade_ledger") or ledger
            except Exception:
                pass
        active = snapshot.get("active_trade") or {}
        target = snapshot.get("daily_profit_target") or {}
        now = datetime.now(self.daily_report_timezone)

        lines = [f"📊 GÜNLÜK BTC RAPORU — {now.strftime('%d.%m.%Y')}", ""]
        wallet = account.get("wallet_balance_usdt")
        if wallet is not None:
            lines.append(f"💼 Bakiye: {self._num(wallet)} USDT")
        net = daily.get("net_pnl_usdt")
        if daily.get("status") == "AVAILABLE" and net is not None:
            lines.extend([
                f"💰 Bugünkü net: {self._signed_num(net)} USDT",
                f"Gerçekleşen K/Z: {self._signed_num(daily.get('realized_pnl_usdt'))} USDT",
                f"Komisyon: {self._signed_num(daily.get('commission_usdt'))} USDT",
                f"Fonlama: {self._signed_num(daily.get('funding_usdt'))} USDT",
            ])
        else:
            lines.append("💰 Bugünkü net: VERİ YOK")

        if ledger.get("status") == "AVAILABLE":
            lines.extend([
                "",
                f"📈 Bugün açılan işlem: {ledger.get('opened_trades_today', 0)}",
                f"✅ Kapanan: {ledger.get('closed_trades_today', 0)}",
                f"🟢 Kazanan: {ledger.get('winning_trades_today', 0)}",
                f"🔴 Kaybeden: {ledger.get('losing_trades_today', 0)}",
            ])
            trades = list(ledger.get("trades") or [])
            if trades:
                reason_labels = {
                    "STOP_LOSS": "STOP",
                    "TAKE_PROFIT": "TP",
                    "FAST_PROFIT_EXIT": "KÂR KORUMA",
                    "EARLY_EXIT": "ERKEN ÇIKIŞ",
                    "PROFIT_PARTIAL": "KISMİ KÂR",
                    "MANUAL_CLOSE": "MANUEL",
                    "MARKET_EXIT_PROFIT": "MARKET/KÂR",
                    "MARKET_EXIT_LOSS": "MARKET/ZARAR",
                    "MARKET_EXIT": "MARKET",
                    "SMOKE_TEST": "SMOKE",
                }
                lines.extend(["", "🧾 BUGÜNKÜ KAPANAN İŞLEMLER"])
                for trade in trades[-8:]:
                    direction = self._text(trade.get("direction"), "?")
                    reason = reason_labels.get(
                        self._text(trade.get("exit_reason"), "MARKET_EXIT"),
                        self._text(trade.get("exit_reason"), "MARKET"),
                    )
                    lines.append(
                        f"#{trade.get('trade_no', '?')} {direction} · "
                        f"{self._num(trade.get('entry_price'))} → {self._num(trade.get('exit_price'))} · "
                        f"{self._signed_num(trade.get('net_pnl_usdt'))} USDT · {reason}"
                    )

        if active.get("status") == "ACTIVE":
            lines.extend([
                "", "📍 AÇIK POZİSYON",
                f"Yön: {self._text(active.get('side'))}",
                f"Giriş: {self._num(active.get('entry_price'))} USDT",
                f"Anlık: {self._num(active.get('mark_price'))} USDT",
                f"Açık K/Z: {self._signed_num(active.get('unrealized_pnl'))} USDT",
                f"Stop: {self._num(active.get('stop_price'))} USDT",
                f"Hedef: {self._num(active.get('tp1_price'))} USDT",
            ])
            if active.get("current_r") is not None:
                lines.append(f"Mevcut R: {self._signed_num(active.get('current_r'))}R")
        else:
            lines.extend(["", "📍 Açık pozisyon: YOK"])

        if target:
            target_pct = target.get("target_pct")
            progress = target.get("progress_pct")
            lines.extend(["", "🎯 GÜNLÜK HEDEF"])
            if target_pct is not None:
                lines.append(f"Hedef: %{float(target_pct) * 100:.2f}")
            if progress is not None:
                lines.append(f"İlerleme: %{float(progress):.1f}")
            lines.append(f"Durum: {self._text(target.get('status'))}")

        status = self.execution_status_provider() or {}
        lines.extend([
            "",
            f"🤖 Bot: {self._text(status.get('bot_status'), 'BİLİNMİYOR')}",
            f"Son sonuç: {self._text(status.get('last_execution_result'))}",
            "",
            "🧪 Binance Futures TESTNET · Gerçek para KAPALI",
        ])
        return "\n".join(lines)

    def _readiness(self) -> str:
        runtime = self._dashboard()
        readiness_fn = getattr(runtime, "live_readiness", None)
        if not callable(readiness_fn):
            return "⚠️ Canlıya hazırlık verisi kullanılamıyor."
        try:
            payload = readiness_fn(force=False) or {}
        except Exception:
            return "⚠️ Canlıya hazırlık verisi alınamadı."

        perf = payload.get("performance") or {}
        criteria = payload.get("criteria") or []
        failed = [row for row in criteria if not row.get("passed")]
        lines = [
            "🚦 CANLIYA HAZIRLIK",
            "",
            f"Durum: {self._text(payload.get('status'), 'NOT_READY')}",
            f"Skor: {payload.get('passed', 0)}/{payload.get('total', len(criteria) or 9)} PASS",
            f"Kapalı işlem: {perf.get('total_trades', 0)}/50",
            f"TESTNET süresi: {self._num(perf.get('observation_days'), 1)}/30 gün",
            f"Net PnL: {self._signed_num(perf.get('net_pnl_usdt'))} USDT",
            f"Profit factor: {self._num(perf.get('profit_factor'))}",
            f"Max drawdown: %{self._num(perf.get('max_drawdown_pct'))}",
            f"Win rate: %{self._num(perf.get('win_rate_pct'))}",
        ]
        if failed:
            lines.extend(["", "❌ Kalan kriterler:"])
            for row in failed[:6]:
                lines.append(f"• {self._text(row.get('label'))}: {self._text(row.get('value'))}")
        lines.extend([
            "",
            "ℹ️ READY sonucu production trading'i otomatik açmaz.",
        ])
        return "\n".join(lines)

    def maybe_send_daily_report(self, now: Optional[datetime] = None) -> bool:
        if not self.daily_report_enabled:
            return False
        current = now.astimezone(self.daily_report_timezone) if now is not None else datetime.now(self.daily_report_timezone)
        scheduled = current.replace(hour=self.daily_report_hour, minute=self.daily_report_minute, second=0, microsecond=0)
        if current < scheduled:
            return False
        date_key = current.date().isoformat()
        try:
            state = self.daily_report_state.load("telegram_daily_report") or {}
        except Exception:
            state = {}
        if state.get("last_sent_date") == date_key:
            return False
        self._send(self._daily_report())
        try:
            self.daily_report_state.save("telegram_daily_report", {
                "last_sent_date": date_key,
                "sent_at": int(current.timestamp() * 1000),
            })
        except Exception:
            pass
        return True

    def handle_message(self, message: Dict[str, Any]) -> bool:
        chat_id = str((message.get("chat") or {}).get("id") or "").strip()
        if not chat_id or chat_id != self.authorized_chat_id:
            return False
        text = str(message.get("text") or "").strip()
        plain = text.lower()
        if text.startswith("/"):
            command = text.split()[0][1:].split("@", 1)[0].lower()
        elif plain in {"sat", "kapat", "manuel", "devam", "pause", "resume"}:
            command = plain
        else:
            return False
        command = self.COMMAND_ALIASES.get(command, command)
        try:
            if command in self.MUTATING_COMMANDS:
                response = "🔒 Bu komut kapalı. Telegram yeni pozisyon açmaz, yön tersine çevirmez, kaldıraç veya emir iptal komutu çalıştırmaz."
            elif command == "yardim":
                response = self._help()
            elif command == "durum":
                response = self._status()
            elif command == "hesap":
                response = self._account()
            elif command == "pozisyon":
                response = self._position()
            elif command == "emirler":
                response = self._orders()
            elif command == "sinyal":
                response = self._signal()
            elif command == "neden":
                response = self._why()
            elif command == "ai":
                response = self._ai()
            elif command == "haber":
                response = self._news()
            elif command == "risk":
                response = self._risk()
            elif command == "kaynaklar":
                response = self._sources()
            elif command == "piyasa":
                response = self._market()
            elif command == "rapor":
                response = self._daily_report()
            elif command == "hazirlik":
                response = self._readiness()
            elif command == "manuel":
                response = self._manual_lock()
            elif command == "devam":
                response = self._manual_resume()
            elif command in {"sat", "kapat"}:
                response = self._manual_close()
            elif command == "smoke":
                response = self._manual_smoke()
            elif command == "ping":
                response = "🏓 PONG\n\nTelegram komut kanalı aktif."
            else:
                response = "Bilinmeyen komut. /yardim yazarak komut listesini görebilirsin."
            self._send(response)
            logger.info("TELEGRAM COMMAND: {} | OK", command)
        except (ExecutionError, TelegramError) as exc:
            category = getattr(exc, "category", type(exc).__name__)
            logger.warning("TELEGRAM COMMAND: {} | FAIL | {}", command, category)
            try:
                self._send(f"⚠️ Komut tamamlanamadı: {category}")
            except Exception:
                pass
        except Exception as exc:
            logger.warning("TELEGRAM COMMAND: {} | FAIL | {}", command, type(exc).__name__)
            try:
                self._send("⚠️ Komut tamamlanamadı: INTERNAL_ERROR")
            except Exception:
                pass
        return True

    @property
    def webhook_secret(self) -> Optional[str]:
        return getattr(self.telegram, "webhook_secret", None)

    def webhook_secret_matches(self, provided: Optional[str]) -> bool:
        expected = self.webhook_secret
        return bool(expected and provided and hmac.compare_digest(str(expected), str(provided)))

    def activate_webhook(self, url: str) -> None:
        self._register_commands()
        self.telegram.set_webhook(url)
        logger.info("TELEGRAM COMMANDS: WEBHOOK READY | AUTHORIZED CHAT ONLY | TESTNET MANUAL CLOSE={}", self.manual_trading_enabled)

    def webhook_maintenance_forever(self) -> None:
        while True:
            try:
                self.maybe_send_daily_report()
            except TelegramError as exc:
                logger.warning("TELEGRAM DAILY REPORT: DEGRADED | {}", exc.category)
            except Exception:
                logger.warning("TELEGRAM DAILY REPORT: DEGRADED | INTERNAL_ERROR")
            self.sleep_fn(30)

    def handle_update(self, update: Dict[str, Any]) -> bool:
        update_id = int(update.get("update_id", -1))
        with self._update_lock:
            if update_id >= 0 and update_id <= self._last_update_id:
                return False
            if update_id >= 0:
                self._last_update_id = update_id
        return self.handle_message(update.get("message") or {})

    def _register_commands(self) -> None:
        self.telegram._post(
            "setMyCommands",
            {"commands": [{"command": command, "description": description} for command, description in self.COMMANDS]},
        )

    def _get_updates(self, *, timeout: int) -> list[Dict[str, Any]]:
        payload: Dict[str, Any] = {"timeout": timeout, "allowed_updates": ["message"]}
        if self._offset is not None:
            payload["offset"] = self._offset
        data = self.telegram._post("getUpdates", payload).get("result", [])
        return data if isinstance(data, list) else []

    def _prime_offset(self) -> None:
        updates = self._get_updates(timeout=0)
        if updates:
            self._offset = max(int(update.get("update_id", 0)) for update in updates) + 1

    def serve_forever(self) -> None:
        if not self.enabled:
            logger.info("TELEGRAM COMMANDS: DISABLED")
            return
        try:
            try:
                self.telegram.delete_webhook(drop_pending_updates=False)
            except TelegramError:
                pass
            self._register_commands()
            self._prime_offset()
            logger.info("TELEGRAM COMMANDS: POLLING READY | AUTHORIZED CHAT ONLY | TESTNET MANUAL CLOSE={}", self.manual_trading_enabled)
        except TelegramError as exc:
            logger.warning("TELEGRAM COMMANDS: STARTUP DEGRADED | {}", exc.category)

        while True:
            try:
                self.maybe_send_daily_report()
                updates = self._get_updates(timeout=4)
                for update in updates:
                    update_id = int(update.get("update_id", 0))
                    self._offset = max(self._offset or 0, update_id + 1)
                    self.handle_update(update)
            except TelegramError as exc:
                logger.warning("TELEGRAM COMMANDS: POLL DEGRADED | {}", exc.category)
                self.sleep_fn(5)
            except Exception:
                logger.warning("TELEGRAM COMMANDS: POLL DEGRADED | INTERNAL_ERROR")
                self.sleep_fn(5)
