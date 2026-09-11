"""Authenticated read-only Telegram command surface for the Render bot.

Only the configured TELEGRAM_CHAT_ID can use commands.  The command service
never submits, cancels, closes, pauses, or otherwise mutates Binance orders or
positions; it only reads the current TESTNET account/runtime state.
"""

from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, Optional
from zoneinfo import ZoneInfo

from loguru import logger

from data.binance_execution_client import BinanceFuturesExecutionClient, ExecutionError
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
        ("risk", "Risk ve koruma durumunu göster"),
        ("kaynaklar", "Veri kaynaklarının durumunu göster"),
        ("piyasa", "Piyasa ve türev bağlamını göster"),
        ("rapor", "Bugünün performans raporunu gönder"),
        ("ping", "Telegram bağlantısını test et"),
    )

    COMMAND_ALIASES = {
        "start": "yardim", "help": "yardim",
        "status": "durum", "account": "hesap", "position": "pozisyon",
        "orders": "emirler", "signal": "sinyal", "sources": "kaynaklar",
        "market": "piyasa", "report": "rapor", "daily": "rapor", "gunluk": "rapor",
    }

    MUTATING_COMMANDS = {
        "buy", "sell", "long", "short", "close", "closeall", "cancel", "cancelall",
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
        self.daily_report_state = create_state_repository(report_path)

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
            "⚠️ Telegram üzerinden al/sat/kapat komutları kapalıdır.",
        ])
        return "\n".join(lines)

    def _status(self) -> str:
        status = self.execution_status_provider() or {}
        market = self._market_status()
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
            "",
            "🧪 Binance Futures TESTNET",
            "💵 Gerçek para: KAPALI",
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
            "🧪 TESTNET · REAL MONEY: NO",
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
        lines.extend(["", "🧪 TESTNET · REAL MONEY: NO"])
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
            return "Unavailable" if raw is None else self._num(raw, digits)
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
            "🌍 MARKET CONTEXT", "",
            f"BTC Dominance: {market_num(macro.get('btc_dominance'))}%",
            f"Total Market Cap: ${market_num(macro.get('total_market_cap_usd'), 0)}",
            f"24h Volume: ${market_num(macro.get('total_volume_24h_usd'), 0)}",
            f"Open Interest: {oi_text}",
            f"Funding: {funding_text}",
            f"Long/Short: {market_num(value('long_short_ratio'), 3)}",
            f"Taker Buy/Sell: {market_num(value('taker_buy_ratio'), 3)}",
            f"CoinGlass Liquidations: ${market_num(value('liquidations_24h'), 0)}",
            "", "🔒 READ ONLY · TESTNET execution controls unchanged",
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
        if not text.startswith("/"):
            return False
        command = text.split()[0][1:].split("@", 1)[0].lower()
        command = self.COMMAND_ALIASES.get(command, command)
        try:
            if command in self.MUTATING_COMMANDS:
                response = "🔒 Bu komut kapalı. Telegram komutları yalnızca okuma amaçlıdır; BUY/SELL/CLOSE ve bot kontrolü yapmaz."
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
            elif command == "risk":
                response = self._risk()
            elif command == "kaynaklar":
                response = self._sources()
            elif command == "piyasa":
                response = self._market()
            elif command == "rapor":
                response = self._daily_report()
            elif command == "ping":
                response = "🏓 PONG\n\nTelegram komut kanalı aktif."
            else:
                response = "Bilinmeyen komut. /yardim yazarak komut listesini görebilirsin."
            self._send(response)
        except (ExecutionError, TelegramError) as exc:
            category = getattr(exc, "category", type(exc).__name__)
            try:
                self._send(f"⚠️ Komut tamamlanamadı: {category}")
            except Exception:
                pass
        except Exception:
            try:
                self._send("⚠️ Komut tamamlanamadı: INTERNAL_ERROR")
            except Exception:
                pass
        return True

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
            self._register_commands()
            self._prime_offset()
            logger.info("TELEGRAM COMMANDS: READY | AUTHORIZED CHAT ONLY | READ ONLY")
        except TelegramError as exc:
            logger.warning("TELEGRAM COMMANDS: STARTUP DEGRADED | {}", exc.category)

        while True:
            try:
                self.maybe_send_daily_report()
                updates = self._get_updates(timeout=4)
                for update in updates:
                    update_id = int(update.get("update_id", 0))
                    self._offset = max(self._offset or 0, update_id + 1)
                    message = update.get("message") or {}
                    self.handle_message(message)
            except TelegramError as exc:
                logger.warning("TELEGRAM COMMANDS: POLL DEGRADED | {}", exc.category)
                self.sleep_fn(5)
            except Exception:
                logger.warning("TELEGRAM COMMANDS: POLL DEGRADED | INTERNAL_ERROR")
                self.sleep_fn(5)
