"""Render-specific wrapper for the BTC Intelligence Console.

The core dashboard remains in :mod:`dashboard_server`. This wrapper adds a
lightweight bootstrap endpoint, an always-visible Render status panel, and a
background snapshot warm-up so a cold Render instance does not look empty while
market data is loading.

Automatic execution remains opt-in and starts only when the core settings pass
the strict TESTNET-only execution boundary. The unattended Render dashboard has
no interactive admin-token login wall.
"""

from __future__ import annotations

import json
import os
import threading
import time
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from loguru import logger

import dashboard_server as base
from data.render_market_client import (
    BinanceSpotPublicMarketClient,
    RenderResilientBinanceFuturesMarketClient,
    StrictPublicBinanceFuturesClient,
)
from engines.mistake_learning_engine import MistakeLearningEngine
from engines.live_readiness import evaluate_live_readiness


ROOT = Path(__file__).resolve().parent
DASHBOARD_DIR = ROOT / "dashboard"
_EXECUTION_STATUS_LOCK = threading.Lock()
_TELEGRAM_COMMAND_SERVICE = None
_EXECUTION_STATUS = {
    "execution_mode": "TESTNET",
    "execution_thread": "DISABLED",
    "bot_status": "STOPPED",
    "smoke_test": "NOT_RUN",
    "last_execution_result": None,
    "execution_error": None,
    "loop_started_at": None,
    "last_cycle_at": None,
    "cycle_count": 0,
}


def configure_telegram_command_service(service) -> None:
    """Attach the authenticated Telegram command service to this web runtime."""
    global _TELEGRAM_COMMAND_SERVICE
    _TELEGRAM_COMMAND_SERVICE = service


class RenderDashboardRuntime(base.DashboardRuntime):
    """Dashboard runtime with Render-safe real market-data fallback."""

    def __init__(self) -> None:
        market_client = RenderResilientBinanceFuturesMarketClient(
            primary=StrictPublicBinanceFuturesClient(
                api_key=None,
                api_secret=None,
                testnet=False,
            ),
            spot_proxy=BinanceSpotPublicMarketClient(),
            fallback=StrictPublicBinanceFuturesClient(
                api_key=None,
                api_secret=None,
                testnet=True,
            ),
        )
        super().__init__(market_client=market_client)
        self.learning_engine = MistakeLearningEngine(self.settings.JOURNAL_DIR)
        self._readiness_lock = threading.Lock()
        self._readiness_cached_at = 0.0
        self._readiness_cache = None

    def _read_execution_events(self) -> list[dict]:
        path = self.execution_journal.events_file
        if not path.is_file():
            return []
        events = []
        try:
            with path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    if not line.strip():
                        continue
                    try:
                        events.append(json.loads(line))
                    except (TypeError, ValueError):
                        continue
        except OSError:
            return []
        return events[-5000:]

    def live_readiness(self, force: bool = False) -> dict:
        """Compute advisory production-readiness from TESTNET exchange evidence."""
        with self._readiness_lock:
            if (
                not force
                and self._readiness_cache is not None
                and time.time() - self._readiness_cached_at < 30
            ):
                return dict(self._readiness_cache)

            account = self.account(force=force)
            market = self.binance.status()
            status = execution_status()
            data_error = None
            fills = []
            if self.account_client is not None and self.account_client.configured:
                try:
                    fills = self.account_client.get_user_trades("BTCUSDT", limit=1000)
                except Exception as exc:
                    data_error = getattr(exc, "category", type(exc).__name__)

            fill_position_sides = {
                str(row.get("positionSide") or "BOTH").upper()
                for row in fills
            }
            positions = account.get("positions") or []
            if fill_position_sides & {"LONG", "SHORT"}:
                ending_quantities = {
                    "LONG": sum(
                        max(0.0, float(row.get("position_amount") or 0.0))
                        for row in positions
                        if row.get("symbol") == "BTCUSDT"
                    ),
                    "SHORT": sum(
                        abs(min(0.0, float(row.get("position_amount") or 0.0)))
                        for row in positions
                        if row.get("symbol") == "BTCUSDT"
                    ),
                    "BOTH": 0.0,
                }
            else:
                ending_quantities = {
                    "BOTH": sum(
                        float(row.get("position_amount") or 0.0)
                        for row in positions
                        if row.get("symbol") == "BTCUSDT"
                    )
                }

            payload = evaluate_live_readiness(
                fills=fills,
                wallet_balance_usdt=account.get("wallet_balance_usdt"),
                account_connected=bool(account.get("connected")),
                market_basis=str(market.get("market_basis") or "UNKNOWN"),
                execution_thread=str(status.get("execution_thread") or "UNKNOWN"),
                execution_error=status.get("execution_error"),
                critical_events=self._read_execution_events(),
                trade_history_available=data_error is None,
                ending_quantities=ending_quantities,
                now_ms=int(time.time() * 1000),
                fill_limit=1000,
            )
            payload["data_error"] = data_error
            payload["market_data_source"] = market.get("market_data_source")
            payload["market_basis"] = market.get("market_basis")
            payload["execution_error"] = status.get("execution_error")
            payload["note"] = (
                "Advisory only. READY never enables production execution automatically."
            )
            self._readiness_cache = dict(payload)
            self._readiness_cached_at = time.time()
            return dict(payload)

    def trade_report(self, force: bool = False) -> dict:
        """Return today's TESTNET trade-by-trade performance with safe exit labels."""
        account = self.account(force=force)
        daily = dict(account.get("daily_performance") or {})
        ledger = dict(account.get("daily_trade_ledger") or {})
        trades = [dict(row) for row in ledger.get("trades") or []]
        events = self._read_execution_events()

        journal_map = {
            "FAST_PROFIT_EXIT": "FAST_PROFIT_EXIT",
            "EARLY_EXIT": "EARLY_EXIT",
            "SMOKE_CLOSE": "SMOKE_TEST",
            "MANUAL_CLOSE": "MANUAL_CLOSE",
            "OPERATOR_CLOSE": "MANUAL_CLOSE",
            "PROFIT_PARTIAL": "PROFIT_PARTIAL",
            "FAST_PROFIT_PARTIAL": "PROFIT_PARTIAL",
        }
        for trade in trades:
            if not str(trade.get("exit_reason") or "").startswith("MARKET_EXIT"):
                continue
            closed_at = int(trade.get("closed_at") or 0)
            nearest = None
            nearest_gap = None
            for event in events:
                action = str(event.get("action") or "").upper()
                if action not in journal_map:
                    continue
                ts = int(event.get("timestamp") or 0)
                gap = abs(ts - closed_at)
                if gap <= 120_000 and (nearest_gap is None or gap < nearest_gap):
                    nearest = action
                    nearest_gap = gap
            if nearest is not None:
                trade["exit_reason"] = journal_map[nearest]
                trade["exit_reason_source"] = "EXECUTION_JOURNAL"
            else:
                trade["exit_reason_source"] = "BINANCE_ORDER_HISTORY"

        ledger["trades"] = trades
        losses = [row for row in trades if float(row.get("net_pnl_usdt") or 0.0) < 0]
        wins = [row for row in trades if float(row.get("net_pnl_usdt") or 0.0) > 0]
        return {
            "status": "AVAILABLE" if ledger.get("status") == "AVAILABLE" else "UNAVAILABLE",
            "timezone": "Europe/Istanbul",
            "date_istanbul": ledger.get("date_istanbul") or daily.get("date_istanbul"),
            "daily_performance": daily,
            "daily_trade_ledger": ledger,
            "trades": trades,
            "winning_trades": wins,
            "losing_trades": losses,
            "largest_loss_usdt": min(
                (float(row.get("net_pnl_usdt") or 0.0) for row in losses),
                default=0.0,
            ),
            "largest_win_usdt": max(
                (float(row.get("net_pnl_usdt") or 0.0) for row in wins),
                default=0.0,
            ),
            "generated_at": int(time.time() * 1000),
        }

    def snapshot(self, force: bool = False) -> dict:
        """Annotate Render snapshots with source authority and learning state.

        Production Futures data remains preferred. If Render receives HTTP 451,
        real Binance Spot candles/ticker can drive TESTNET forward-test price
        decisions as an explicit SPOT proxy. TESTNET public data remains display
        only and never grants new-entry authority.
        """
        snapshot = super().snapshot(force=force)
        market = self.binance.status()
        source = str(market.get("market_data_source") or "UNKNOWN")
        trading_safe = bool(market.get("market_data_trading_safe", False))
        market_basis = str(market.get("market_basis") or "UNKNOWN")

        meta = snapshot.setdefault("meta", {})
        meta["market_data_source"] = source
        meta["market_data_trading_safe"] = trading_safe
        meta["market_basis"] = market_basis
        meta["forward_test_price_proxy"] = source == "BINANCE_SPOT_PUBLIC_PROXY"

        binance_source = snapshot.setdefault("sources", {}).setdefault("binance", {})
        binance_source["environment"] = source
        binance_source["fallback_active"] = bool(market.get("fallback_active", False))
        binance_source["market_data_trading_safe"] = trading_safe
        binance_source["market_basis"] = market_basis
        binance_source["spot_proxy_status"] = market.get("spot_proxy_status")
        if not trading_safe:
            # Defense in depth: the executor also rejects non-healthy source state.
            binance_source["status"] = "DEGRADED"

        strategy = snapshot.setdefault("strategy", {})
        hard_blockers = list(
            strategy.get("hard_blockers")
            if "hard_blockers" in strategy
            else strategy.get("blocking_reasons") or []
        )
        if not trading_safe:
            strategy["eligible"] = False
            if "MARKET_DATA_NOT_TRADING_SAFE" not in hard_blockers:
                hard_blockers.append("MARKET_DATA_NOT_TRADING_SAFE")
            strategy["hard_blockers"] = hard_blockers
            strategy["blocking_reasons"] = hard_blockers
            if snapshot.get("final_decision") in {"LONG_ENTRY", "SHORT_ENTRY"}:
                snapshot["final_decision"] = "NO_TRADE"

        derivatives = snapshot.setdefault("derivatives", {})
        derivatives["trading_authority"] = (
            "PRODUCTION_FUTURES"
            if source == "PRODUCTION_FUTURES_PUBLIC"
            else "SUPPLEMENTAL_OR_NONE"
        )

        # When production Futures REST is restricted, the market client may keep
        # TESTNET derivative values solely for operator visibility. The pipeline
        # itself received None for these values, so they cannot veto/confirm.
        if source != "PRODUCTION_FUTURES_PUBLIC":
            telemetry = self.binance.fallback_derivatives_telemetry()
            mapping = {
                "get_open_interest": "open_interest",
                "get_funding_rate": "funding_rate",
                "get_long_short_ratio": "long_short_ratio",
                "get_taker_volume_ratio": "taker_buy_ratio",
            }
            has_display_value = False
            for method, field_name in mapping.items():
                field = telemetry.get(method)
                if field is None:
                    continue
                derivatives[field_name] = dict(field)
                if field.get("value") is not None:
                    has_display_value = True
            if has_display_value:
                derivatives["display_status"] = "DEGRADED_DISPLAY_ONLY"

            market_payload = snapshot.setdefault("market", {})
            oi = telemetry.get("get_open_interest") or {}
            funding = telemetry.get("get_funding_rate") or {}
            ls = telemetry.get("get_long_short_ratio") or {}
            taker = telemetry.get("get_taker_volume_ratio") or {}
            if oi:
                market_payload["open_interest_btc"] = oi.get("value")
            if funding:
                market_payload["funding_rate"] = funding.get("value")
            if ls:
                market_payload["long_short_ratio"] = ls.get("value")
            if taker:
                market_payload["taker_buy_sell_ratio"] = taker.get("value")

        snapshot["learning"] = self.learning_engine.analyze()
        return snapshot


def _update_execution_status(**changes) -> None:
    if "last_error" in changes:
        changes["execution_error"] = changes.pop("last_error") or None
    with _EXECUTION_STATUS_LOCK:
        _EXECUTION_STATUS.update(changes)


def execution_status() -> dict:
    with _EXECUTION_STATUS_LOCK:
        return dict(_EXECUTION_STATUS)


def _market_status(runtime) -> dict:
    if runtime is None:
        return {}
    status_fn = getattr(getattr(runtime, "binance", None), "status", None)
    if not callable(status_fn):
        return {}
    try:
        return dict(status_fn())
    except Exception:
        return {}


def execution_doctor_payload(settings=None) -> dict:
    """Return a secret-free TESTNET execution readiness report."""

    from execution.testnet_runtime import TestnetExecutionRuntime

    runtime = getattr(base, "RUNTIME", None)
    settings = settings or (runtime.settings if runtime is not None else base.get_settings())
    doctor = TestnetExecutionRuntime.doctor(settings)
    blockers = []
    if not doctor["binance_api_key_configured"]:
        blockers.append("BINANCE_API_KEY_MISSING")
    if not doctor["binance_api_secret_configured"]:
        blockers.append("BINANCE_API_SECRET_MISSING")
    if not doctor["binance_testnet"]:
        blockers.append("BINANCE_TESTNET_FALSE")
    if doctor["env"] != "TESTNET":
        blockers.append("ENV_NOT_TESTNET")
    if not doctor["order_submission_enabled"]:
        blockers.append("ORDER_SUBMISSION_DISABLED")
    if doctor["account_read_only"]:
        blockers.append("ACCOUNT_READ_ONLY")
    if doctor["shadow_mode"]:
        blockers.append("SHADOW_MODE_ACTIVE")

    boundary_ready = bool(settings.testnet_execution_enabled) and not blockers
    return {
        **doctor,
        "execution_boundary_ready": boundary_ready,
        "blockers": blockers,
        "operator_smoke_available": bool(
            boundary_ready
            and getattr(settings, "TELEGRAM_ENABLED", False)
            and getattr(settings, "TELEGRAM_MANUAL_TRADING_ENABLED", False)
            and getattr(settings, "TELEGRAM_BOT_TOKEN", None)
            and getattr(settings, "TELEGRAM_CHAT_ID", None)
        ),
    }


def run_operator_smoke_test() -> dict:
    """Run one authenticated operator-requested TESTNET smoke and restore loop status."""

    from data.binance_execution_client import ExecutionError
    from execution.testnet_runtime import TestnetExecutionRuntime

    runtime = getattr(base, "RUNTIME", None)
    if runtime is None:
        raise ExecutionError("DASHBOARD_UNAVAILABLE")

    previous = execution_status()
    smoke_runtime = TestnetExecutionRuntime(
        settings=runtime.settings,
        dashboard_runtime=runtime,
        status_callback=_update_execution_status,
    )
    try:
        result = smoke_runtime.run_smoke_test(operator_requested=True)
    except ExecutionError as exc:
        _update_execution_status(
            execution_thread=previous.get("execution_thread", "DISABLED"),
            bot_status=previous.get("bot_status", "STOPPED"),
            smoke_test="FAIL",
            last_execution_result="OPERATOR_SMOKE_FAIL",
            execution_error=exc.category,
        )
        raise
    except Exception as exc:
        _update_execution_status(
            execution_thread=previous.get("execution_thread", "DISABLED"),
            bot_status=previous.get("bot_status", "STOPPED"),
            smoke_test="FAIL",
            last_execution_result="OPERATOR_SMOKE_FAIL",
            execution_error=type(exc).__name__,
        )
        raise
    _update_execution_status(
        execution_thread=previous.get("execution_thread", "DISABLED"),
        bot_status=previous.get("bot_status", "STOPPED"),
        smoke_test="PASS",
        last_execution_result="OPERATOR_SMOKE_PASS",
        execution_error=None,
    )
    return result


def bootstrap_payload() -> dict:
    """Return fast, network-free Render startup diagnostics.

    Only configuration presence and cached runtime state are exposed. Secret
    values are never returned and this endpoint never performs an external API
    call.
    """

    runtime = getattr(base, "RUNTIME", None)
    settings = runtime.settings if runtime is not None else base.get_settings()
    execution_enabled = settings.testnet_execution_enabled
    market = _market_status(runtime)
    payload = {
        "ok": True,
        "runtime": "RENDER" if os.environ.get("RENDER") else "CLOUD",
        "service": "BTC Intelligence Console",
        "ui": "READY",
        # Supplemental providers (CoinGlass/CMC) never gate process startup.
        "ready_for_render": "YES_DEGRADED",
        "orders_enabled": execution_enabled,
        "shadow_mode": settings.SHADOW_MODE,
        "account_read_only": settings.ACCOUNT_READ_ONLY,
        "binance_testnet": settings.BINANCE_TESTNET,
        "binance_credentials_configured": bool(
            os.environ.get("BINANCE_API_KEY") and os.environ.get("BINANCE_API_SECRET")
        ),
        "market_data_source": market.get("market_data_source", "UNKNOWN"),
        "market_data_trading_safe": bool(market.get("market_data_trading_safe", False)),
        "market_basis": market.get("market_basis", "UNKNOWN"),
        "spot_proxy_status": market.get("spot_proxy_status", "UNKNOWN"),
        "production_public_status": market.get("production_public_status", "UNKNOWN"),
        "production_public_retry_after_seconds": market.get(
            "production_public_retry_after_seconds", 0
        ),
        "market_fallback_active": bool(market.get("fallback_active", False)),
        "derivatives_status": market.get("derivatives_status", "UNKNOWN"),
        "learning_mode": "ADVISORY_ONLY",
        "dashboard_admin_token_configured": False,
        "telegram_enabled": os.environ.get("TELEGRAM_ENABLED", "false").lower() == "true",
        "telegram_configured": bool(
            os.environ.get("TELEGRAM_BOT_TOKEN") and os.environ.get("TELEGRAM_CHAT_ID")
        ),
        "news_enabled": os.environ.get("NEWS_ENABLED", "true").lower() == "true",
        "ai_enabled": os.environ.get("AI_ENABLED", "false").lower() == "true",
        "execution_doctor": execution_doctor_payload(settings),
        "render_git_commit": (os.environ.get("RENDER_GIT_COMMIT") or "")[:12] or None,
        "generated_at": int(time.time() * 1000),
    }
    payload.update(execution_status())
    return payload


def _render_index_html() -> bytes:
    """Inject the Render runtime panel into the existing dashboard."""

    html = (DASHBOARD_DIR / "index.html").read_text(encoding="utf-8")
    panel = """
    <section id="renderRuntimePanel" class="glass render-runtime-strip">
      <div class="section-head">
        <span>Render Çalışma Durumu</span>
        <span id="renderBootBadge" class="badge warning">BAŞLIYOR</span>
      </div>
      <div class="intelligence-grid">
        <div><span>Web Arayüzü</span><strong id="renderUiState">HAZIR</strong></div>
        <div><span>Sunucu</span><strong id="renderBackendState">KONTROL EDİLİYOR</strong></div>
        <div><span>Piyasa Verisi</span><strong id="renderMarketState">YÜKLENİYOR</strong></div>
        <div><span>TESTNET Hesabı</span><strong id="renderAccountState">KONTROL EDİLİYOR</strong></div>
      </div>
      <div class="reason-box">
        <span>Çalışma Durumu</span>
        <p id="renderRuntimeMessage">Render servisi açıldı. Canlı piyasa verisi yükleniyor…</p>
      </div>
    </section>
    <section id="liveReadinessPanel" class="glass render-runtime-strip">
      <div class="section-head">
        <span>Canlıya Hazırlık / Live Readiness</span>
        <span id="liveReadinessBadge" class="badge warning">HESAPLANIYOR</span>
      </div>
      <div class="intelligence-grid">
        <div><span>SKOR</span><strong id="readinessScore">—</strong></div>
        <div><span>KAPALI İŞLEM</span><strong id="readinessTrades">—</strong></div>
        <div><span>TESTNET SÜRESİ</span><strong id="readinessDays">—</strong></div>
        <div><span>NET PnL</span><strong id="readinessPnl">—</strong></div>
        <div><span>PROFIT FACTOR</span><strong id="readinessPf">—</strong></div>
        <div><span>MAX DRAWDOWN</span><strong id="readinessDd">—</strong></div>
        <div><span>WIN RATE</span><strong id="readinessWin">—</strong></div>
      </div>
      <div id="readinessCriteria" class="intelligence-grid"></div>
      <div class="reason-box">
        <span>SONUÇ</span>
        <p id="readinessMessage">Binance TESTNET işlem geçmişi değerlendiriliyor…</p>
        <small id="readinessScope" class="muted">—</small>
      </div>
    </section>
    """
    html = html.replace('<!-- RENDER_RUNTIME_SLOT -->', panel, 1)
    html = html.replace(
        '</body>',
        '  <script src="/render-bridge.js" defer></script>\n'
        '  <script src="/live-readiness.js" defer></script>\n</body>',
        1,
    )
    return html.encode("utf-8")


class RenderDashboardHandler(base.DashboardHandler):
    """Dashboard handler with an instant Render bootstrap surface."""

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path == "/api/bootstrap":
            self._send_json(bootstrap_payload())
            return
        if path == "/api/trade-report":
            runtime = getattr(base, "RUNTIME", None)
            if runtime is None or not hasattr(runtime, "trade_report"):
                self._send_json({"status": "UNAVAILABLE", "error": "TRADE_REPORT_UNAVAILABLE"}, 503)
                return
            try:
                force = urlparse(self.path).query == "force=1"
                self._send_json(runtime.trade_report(force=force))
            except Exception:
                logger.warning("Trade report endpoint failed")
                self._send_json({"status": "UNAVAILABLE", "error": "TRADE_REPORT_UNAVAILABLE"}, 503)
            return
        if path == "/api/live-readiness":
            runtime = getattr(base, "RUNTIME", None)
            if runtime is None or not hasattr(runtime, "live_readiness"):
                self._send_json({"status": "NOT_READY", "error": "READINESS_UNAVAILABLE"}, 503)
                return
            try:
                force = urlparse(self.path).query == "force=1"
                self._send_json(runtime.live_readiness(force=force))
            except Exception:
                logger.warning("Live readiness endpoint failed")
                self._send_json({"status": "NOT_READY", "error": "READINESS_UNAVAILABLE"}, 503)
            return
        if path in {"/", "/index.html"}:
            content = _render_index_html()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(content)))
            self._security_headers()
            self.end_headers()
            self.wfile.write(content)
            return
        super().do_GET()

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path != "/api/telegram/webhook":
            self._send_json({"error": "NOT_FOUND"}, 404)
            return

        service = _TELEGRAM_COMMAND_SERVICE
        if service is None:
            self._send_json({"ok": False, "error": "TELEGRAM_COMMANDS_UNAVAILABLE"}, 503)
            return

        provided_secret = self.headers.get("X-Telegram-Bot-Api-Secret-Token")
        if not service.webhook_secret_matches(provided_secret):
            logger.warning("TELEGRAM WEBHOOK: REJECTED | INVALID_SECRET")
            self._send_json({"ok": False, "error": "FORBIDDEN"}, 403)
            return

        try:
            content_length = int(self.headers.get("Content-Length") or "0")
        except ValueError:
            content_length = -1
        if content_length <= 0 or content_length > 1_000_000:
            self._send_json({"ok": False, "error": "INVALID_PAYLOAD"}, 400)
            return

        try:
            update = json.loads(self.rfile.read(content_length).decode("utf-8"))
        except (UnicodeDecodeError, TypeError, ValueError):
            self._send_json({"ok": False, "error": "INVALID_JSON"}, 400)
            return
        if not isinstance(update, dict):
            self._send_json({"ok": False, "error": "INVALID_UPDATE"}, 400)
            return

        # Acknowledge Telegram immediately. Potentially slow Binance-backed
        # commands run outside the HTTP request so Telegram will not retry a
        # valid operator command merely because Binance took a few seconds.
        threading.Thread(
            target=service.handle_update,
            args=(update,),
            name="telegram-webhook-command",
            daemon=True,
        ).start()
        self._send_json({"ok": True})


def _warm_snapshot() -> None:
    """Prime the 15-second snapshot cache after a cold Render boot."""

    time.sleep(0.35)
    try:
        snapshot = base.RUNTIME.snapshot(force=True)
        logger.info(
            "Render snapshot warm-up complete: {}",
            snapshot.get("final_decision", "UNKNOWN"),
        )
        strategy = snapshot.get("strategy") or {}
        decision = snapshot.get("decision") or {}
        blockers = strategy.get("hard_blockers") or strategy.get("blocking_reasons") or []
        logger.info(
            "STRATEGY SNAPSHOT: FINAL {} | REGIME {} | SETUP {} | DIRECTION {} | TRIGGER {} | ELIGIBLE {} | RISK {} | BLOCKERS {}",
            snapshot.get("final_decision", "UNKNOWN"),
            decision.get("regime", "UNKNOWN"),
            strategy.get("setup_type", "NONE"),
            strategy.get("direction", "WAIT"),
            strategy.get("entry_trigger_state", "UNKNOWN"),
            strategy.get("eligible", False),
            decision.get("risk_status", "UNKNOWN"),
            ",".join(str(item) for item in blockers) if isinstance(blockers, (list, tuple)) and blockers else "NONE",
        )
        trade_report_fn = getattr(base.RUNTIME, "trade_report", None)
        if callable(trade_report_fn):
            report = trade_report_fn(force=True)
            daily = report.get("daily_performance") or {}
            ledger = report.get("daily_trade_ledger") or {}
            logger.info(
                "DAILY TRADE REPORT: DATE {} | NET {} | REALIZED {} | COMMISSION {} | FUNDING {} | CLOSED {} | WINS {} | LOSSES {}",
                report.get("date_istanbul") or "UNKNOWN",
                daily.get("net_pnl_usdt"),
                daily.get("realized_pnl_usdt"),
                daily.get("commission_usdt"),
                daily.get("funding_usdt"),
                ledger.get("closed_trades_today"),
                ledger.get("winning_trades_today"),
                ledger.get("losing_trades_today"),
            )
            for trade in report.get("trades") or []:
                logger.info(
                    "DAILY TRADE #{}: {} | ENTRY {} | EXIT {} | NET {} | REALIZED {} | COMMISSION {} | REASON {}",
                    trade.get("trade_no"),
                    trade.get("direction"),
                    trade.get("entry_price"),
                    trade.get("exit_price"),
                    trade.get("net_pnl_usdt"),
                    trade.get("realized_pnl_usdt"),
                    trade.get("commission_usdt"),
                    trade.get("exit_reason"),
                )
        readiness_fn = getattr(base.RUNTIME, "live_readiness", None)
        if callable(readiness_fn):
            readiness = readiness_fn(force=True)
            perf = readiness.get("performance") or {}
            logger.info(
                "LIVE READINESS: {} | PASS {}/{} | FILLS {} | TRADES {} | DAYS {} | PF {} | DD {}% | DATA_ERROR {} | HARD_BLOCKERS {}",
                readiness.get("status", "NOT_READY"),
                readiness.get("passed", 0),
                readiness.get("total", 0),
                readiness.get("fill_records_observed", 0),
                perf.get("total_trades", 0),
                perf.get("observation_days", 0),
                perf.get("profit_factor", 0),
                perf.get("max_drawdown_pct", 0),
                readiness.get("data_error") or "NONE",
                ",".join(readiness.get("hard_failures") or []) or "NONE",
            )
    except Exception as exc:
        # Do not crash the web service because one external data source is down.
        logger.warning("Render snapshot warm-up degraded: {}", type(exc).__name__)


def _run_testnet_execution() -> None:
    """Run one optional startup smoke before the normal TESTNET loop."""

    from data.binance_execution_client import ExecutionError
    from execution.testnet_runtime import TestnetExecutionRuntime

    try:
        runtime = TestnetExecutionRuntime(
            settings=base.RUNTIME.settings,
            dashboard_runtime=base.RUNTIME,
            status_callback=_update_execution_status,
        )

        runtime.run_loop()
    except ExecutionError as exc:
        _update_execution_status(
            execution_thread="STOPPED",
            bot_status="DEGRADED",
            execution_error=exc.category,
            last_execution_result="EXECUTION_STOPPED",
        )
        logger.error("Render TESTNET execution stopped: {}", exc.category)
    except Exception as exc:
        _update_execution_status(
            execution_thread="STOPPED",
            bot_status="DEGRADED",
            execution_error=type(exc).__name__,
            last_execution_result="EXECUTION_STOPPED",
        )
        logger.error("Render TESTNET execution stopped: {}", type(exc).__name__)


def _start_telegram_delivery() -> None:
    service = _TELEGRAM_COMMAND_SERVICE
    if service is None or not service.enabled:
        logger.info("TELEGRAM COMMANDS: DISABLED")
        return

    hostname = str(os.environ.get("RENDER_EXTERNAL_HOSTNAME") or "").strip()
    if hostname:
        base_url = hostname.rstrip("/")
        if not base_url.startswith(("http://", "https://")):
            base_url = f"https://{base_url}"
        webhook_url = f"{base_url}/api/telegram/webhook"
        try:
            service.activate_webhook(webhook_url)
            threading.Thread(
                target=service.webhook_maintenance_forever,
                name="telegram-webhook-maintenance",
                daemon=True,
            ).start()
            return
        except Exception as exc:
            logger.warning(
                "TELEGRAM COMMANDS: WEBHOOK DEGRADED | {} | FALLBACK POLLING",
                getattr(exc, "category", type(exc).__name__),
            )

    threading.Thread(
        target=service.serve_forever,
        name="telegram-command-listener",
        daemon=True,
    ).start()


def main() -> None:
    host = "0.0.0.0"
    try:
        port = int(os.environ.get("PORT", "8080"))
    except ValueError as exc:
        raise SystemExit("Invalid PORT environment variable") from exc
    if not 1 <= port <= 65535:
        raise SystemExit("PORT must be between 1 and 65535")

    base.RUNTIME = RenderDashboardRuntime()
    settings = base.RUNTIME.settings
    threading.Thread(target=_warm_snapshot, name="render-snapshot-warmup", daemon=True).start()

    execution_enabled = settings.testnet_execution_enabled
    if execution_enabled:
        _update_execution_status(
            execution_thread="STARTING",
            bot_status="STARTING",
            smoke_test="RUNNING" if settings.RUN_EXECUTION_SMOKE_TEST else "NOT_RUN",
            execution_error=None,
        )
        threading.Thread(
            target=_run_testnet_execution,
            name="render-testnet-execution",
            daemon=True,
        ).start()

    logger.info("RENDER WEB UI: READY")
    logger.info("DASHBOARD ADMIN GATE: DISABLED")
    logger.info("ENV: {}", "TESTNET" if settings.ENV.strip().lower() == "testnet" else settings.ENV.upper())
    logger.info("BINANCE_TESTNET: {}", str(settings.BINANCE_TESTNET).upper())
    logger.info("ACCOUNT_READ_ONLY: {}", str(settings.ACCOUNT_READ_ONLY).upper())
    logger.info("ORDER_SUBMISSION_ENABLED: {}", str(settings.ORDER_SUBMISSION_ENABLED).upper())
    logger.info("SHADOW_MODE: {}", str(settings.SHADOW_MODE).upper())
    logger.info("RUN_EXECUTION_SMOKE_TEST: {}", str(settings.RUN_EXECUTION_SMOKE_TEST).upper())
    logger.info("COINGLASS: {}", "CONFIGURED" if settings.COINGLASS_API_KEY else "NOT CONFIGURED")
    logger.info("COINMARKETCAP: {}", "CONFIGURED" if settings.COINMARKETCAP_API_KEY else "NOT CONFIGURED")
    logger.info("BINANCE ACCOUNT MODE: TESTNET")
    logger.info("ACCOUNT ACCESS: {}", "EXECUTION" if execution_enabled else "READ ONLY")
    logger.info("SHADOW MODE: {}", "DISABLED" if execution_enabled else "ENABLED")
    logger.info(
        "ORDER SUBMISSION: {}",
        "ENABLED - TESTNET ONLY" if execution_enabled else "DISABLED",
    )
    logger.info(
        "EXECUTION SMOKE TEST: {}",
        "RUN BEFORE AUTO LOOP" if settings.RUN_EXECUTION_SMOKE_TEST else "DISABLED",
    )

    server = ThreadingHTTPServer((host, port), RenderDashboardHandler)
    logger.info("BTC Intelligence Console listening on 0.0.0.0:{}", port)
    _start_telegram_delivery()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Render dashboard stopped")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
