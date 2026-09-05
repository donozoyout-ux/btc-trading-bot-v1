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


ROOT = Path(__file__).resolve().parent
DASHBOARD_DIR = ROOT / "dashboard"


def bootstrap_payload() -> dict:
    """Return fast, network-free Render startup diagnostics.

    Only configuration presence is exposed. Secret values are never returned.
    """

    runtime = getattr(base, "RUNTIME", None)
    settings = runtime.settings if runtime is not None else base.get_settings()
    execution_enabled = settings.testnet_execution_enabled
    return {
        "ok": True,
        "runtime": "RENDER" if os.environ.get("RENDER") else "CLOUD",
        "service": "BTC Intelligence Console",
        "ui": "READY",
        "orders_enabled": execution_enabled,
        "shadow_mode": settings.SHADOW_MODE,
        "account_read_only": settings.ACCOUNT_READ_ONLY,
        "binance_testnet": settings.BINANCE_TESTNET,
        "binance_credentials_configured": bool(
            os.environ.get("BINANCE_API_KEY") and os.environ.get("BINANCE_API_SECRET")
        ),
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
    """Run the existing execution runtime without ever invoking smoke mode."""

    from data.binance_execution_client import ExecutionError
    from execution.testnet_runtime import TestnetExecutionRuntime

    try:
        runtime = TestnetExecutionRuntime(
            settings=base.RUNTIME.settings,
            dashboard_runtime=base.RUNTIME,
        )
        runtime.run_loop()
    except ExecutionError as exc:
        logger.error("Render TESTNET execution stopped: {}", exc.category)
    except Exception as exc:
        logger.error("Render TESTNET execution stopped: {}", type(exc).__name__)


def main() -> None:
    host = "0.0.0.0"
    try:
        port = int(os.environ.get("PORT", "8080"))
    except ValueError as exc:
        raise SystemExit("Invalid PORT environment variable") from exc
    if not 1 <= port <= 65535:
        raise SystemExit("PORT must be between 1 and 65535")

    base.RUNTIME = base.DashboardRuntime()
    threading.Thread(target=_warm_snapshot, name="render-snapshot-warmup", daemon=True).start()

    execution_enabled = base.RUNTIME.settings.testnet_execution_enabled
    if execution_enabled:
        threading.Thread(
            target=_run_testnet_execution,
            name="render-testnet-execution",
            daemon=True,
        ).start()

    logger.info("RENDER WEB UI: READY")
    logger.info("DASHBOARD ADMIN GATE: DISABLED")
    logger.info("BINANCE ACCOUNT MODE: TESTNET")
    logger.info("ACCOUNT ACCESS: {}", "EXECUTION" if execution_enabled else "READ ONLY")
    logger.info("SHADOW MODE: {}", "DISABLED" if execution_enabled else "ENABLED")
    logger.info(
        "ORDER SUBMISSION: {}",
        "ENABLED - TESTNET ONLY" if execution_enabled else "DISABLED",
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
