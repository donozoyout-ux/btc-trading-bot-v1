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

import os
import threading
import time
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from loguru import logger

import dashboard_server as base
from data.render_market_client import (
    RenderResilientBinanceFuturesMarketClient,
    StrictPublicBinanceFuturesClient,
)


ROOT = Path(__file__).resolve().parent
DASHBOARD_DIR = ROOT / "dashboard"
_EXECUTION_STATUS_LOCK = threading.Lock()
_EXECUTION_STATUS = {
    "execution_mode": "TESTNET",
    "execution_thread": "DISABLED",
    "bot_status": "STOPPED",
    "smoke_test": "NOT_RUN",
    "last_execution_result": None,
    "execution_error": None,
}


class RenderDashboardRuntime(base.DashboardRuntime):
    """Dashboard runtime with a Render-aware public Binance market client."""

    def __init__(self) -> None:
        market_client = RenderResilientBinanceFuturesMarketClient(
            primary=StrictPublicBinanceFuturesClient(
                api_key=None,
                api_secret=None,
                testnet=False,
            ),
            fallback=StrictPublicBinanceFuturesClient(
                api_key=None,
                api_secret=None,
                testnet=True,
            ),
        )
        super().__init__(market_client=market_client)

    def snapshot(self, force: bool = False) -> dict:
        """Annotate Render snapshots with explicit trading-authority provenance.

        TESTNET public market data may keep the dashboard populated, but it must
        never authorize a new TESTNET entry. Fallback derivatives values are
        display-only and are labelled explicitly instead of masquerading as
        production Binance Futures data.
        """
        snapshot = super().snapshot(force=force)
        market = self.binance.status()
        source = str(market.get("market_data_source") or "UNKNOWN")
        trading_safe = bool(market.get("market_data_trading_safe", False))

        meta = snapshot.setdefault("meta", {})
        meta["market_data_source"] = source
        meta["market_data_trading_safe"] = trading_safe

        binance_source = snapshot.setdefault("sources", {}).setdefault("binance", {})
        binance_source["environment"] = source
        binance_source["fallback_active"] = bool(market.get("fallback_active", False))
        binance_source["market_data_trading_safe"] = trading_safe
        if not trading_safe:
            # Defense in depth: the executor also has an explicit authority gate.
            binance_source["status"] = "DEGRADED"

        strategy = snapshot.setdefault("strategy", {})
        blockers = list(strategy.get("blocking_reasons") or [])
        if not trading_safe:
            strategy["eligible"] = False
            if "MARKET_DATA_NOT_TRADING_SAFE" not in blockers:
                blockers.append("MARKET_DATA_NOT_TRADING_SAFE")
            strategy["blocking_reasons"] = blockers
            if snapshot.get("final_decision") in {"LONG_ENTRY", "SHORT_ENTRY"}:
                snapshot["final_decision"] = "NO_TRADE"

        derivatives = snapshot.setdefault("derivatives", {})
        derivatives["trading_authority"] = "PRODUCTION" if trading_safe else "NONE"
        if not trading_safe:
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
            derivatives["display_status"] = "DEGRADED" if has_display_value else "UNAVAILABLE"

            market_payload = snapshot.setdefault("market", {})
            oi = telemetry.get("get_open_interest") or {}
            funding = telemetry.get("get_funding_rate") or {}
            ls = telemetry.get("get_long_short_ratio") or {}
            taker = telemetry.get("get_taker_volume_ratio") or {}
            market_payload["open_interest_btc"] = oi.get("value")
            market_payload["funding_rate"] = funding.get("value")
            market_payload["long_short_ratio"] = ls.get("value")
            market_payload["taker_buy_sell_ratio"] = taker.get("value")

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
        "production_public_status": market.get("production_public_status", "UNKNOWN"),
        "production_public_retry_after_seconds": market.get(
            "production_public_retry_after_seconds", 0
        ),
        "market_fallback_active": bool(market.get("fallback_active", False)),
        "derivatives_status": market.get("derivatives_status", "UNKNOWN"),
        "dashboard_admin_token_configured": False,
        "telegram_enabled": os.environ.get("TELEGRAM_ENABLED", "false").lower() == "true",
        "telegram_configured": bool(
            os.environ.get("TELEGRAM_BOT_TOKEN") and os.environ.get("TELEGRAM_CHAT_ID")
        ),
        "news_enabled": os.environ.get("NEWS_ENABLED", "true").lower() == "true",
        "ai_enabled": os.environ.get("AI_ENABLED", "false").lower() == "true",
        "render_git_commit": (os.environ.get("RENDER_GIT_COMMIT") or "")[:12] or None,
        "generated_at": int(time.time() * 1000),
    }
    payload.update(execution_status())
    return payload


def _render_index_html() -> bytes:
    """Inject the Render runtime panel into the existing dashboard."""

    html = (DASHBOARD_DIR / "index.html").read_text(encoding="utf-8")
    panel = """
    <section id="renderRuntimePanel" class="glass panel event-panel">
      <div class="section-head">
        <span>Render Runtime</span>
        <span id="renderBootBadge" class="badge warning">BOOTING</span>
      </div>
      <div class="intelligence-grid">
        <div><span>Web UI</span><strong id="renderUiState">READY</strong></div>
        <div><span>Backend</span><strong id="renderBackendState">CHECKING</strong></div>
        <div><span>Market Feed</span><strong id="renderMarketState">LOADING</strong></div>
        <div><span>Testnet Account</span><strong id="renderAccountState">CHECKING</strong></div>
      </div>
      <div class="reason-box">
        <span>Runtime Status</span>
        <p id="renderRuntimeMessage">Render servisi açıldı. Canlı piyasa verisi yükleniyor…</p>
      </div>
    </section>
    """
    html = html.replace('<main class="shell">', '<main class="shell">' + panel, 1)
    html = html.replace('</body>', '  <script src="/render-bridge.js" defer></script>\n</body>', 1)
    return html.encode("utf-8")


class RenderDashboardHandler(base.DashboardHandler):
    """Dashboard handler with an instant Render bootstrap surface."""

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path == "/api/bootstrap":
            self._send_json(bootstrap_payload())
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


def _warm_snapshot() -> None:
    """Prime the 15-second snapshot cache after a cold Render boot."""

    time.sleep(0.35)
    try:
        snapshot = base.RUNTIME.snapshot(force=True)
        logger.info(
            "Render snapshot warm-up complete: {}",
            snapshot.get("final_decision", "UNKNOWN"),
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
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Render dashboard stopped")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
