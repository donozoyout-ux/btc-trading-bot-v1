"""Cloud deployment entrypoint for the BTC Intelligence Console.

Render injects ``PORT`` automatically. Cloud startup goes through
``render_server`` so the UI gets an instant bootstrap panel and background
snapshot warm-up instead of appearing empty during cold starts.

For Render only, TESTNET auto-execution defaults are applied when the operator
has not explicitly set the corresponding variables. Explicit environment
values always win, and the application still fail-closes unless ENV=testnet and
BINANCE_TESTNET=true.
"""

import os
import threading

from config.settings import get_settings
import render_server
from notifications.telegram_commands import TelegramCommandService


def _apply_render_testnet_defaults() -> None:
    """Make a plain Render Web Service usable without hidden execution flags.

    ``setdefault`` is deliberate: operators can still force read-only/shadow
    mode from Render Environment. These defaults never enable production
    execution because the settings/executor boundary separately requires
    ``ENV=testnet`` and ``BINANCE_TESTNET=true``.
    """
    if not os.environ.get("RENDER"):
        return

    os.environ.setdefault("ENV", "testnet")
    os.environ.setdefault("BINANCE_TESTNET", "true")

    # Only opt into TESTNET execution when the environment itself has not been
    # explicitly configured for another mode.
    if (
        os.environ.get("ENV", "").strip().lower() == "testnet"
        and os.environ.get("BINANCE_TESTNET", "true").strip().lower() in {"1", "true", "yes", "on"}
    ):
        os.environ.setdefault("ACCOUNT_READ_ONLY", "false")
        os.environ.setdefault("ORDER_SUBMISSION_ENABLED", "true")
        os.environ.setdefault("SHADOW_MODE", "false")
        # Smoke tests are intentionally not automatic on every Render restart.
        os.environ.setdefault("RUN_EXECUTION_SMOKE_TEST", "false")


def _safe_startup_status() -> None:
    """Log configuration presence only; never log credentials."""
    settings = get_settings()
    print("BTC Intelligence Console cloud startup")
    print(f"RENDER runtime: {'YES' if os.environ.get('RENDER') else 'NO'}")
    print(f"ENV: {settings.ENV}")
    print(f"BINANCE_TESTNET: {str(settings.BINANCE_TESTNET).lower()}")
    print(f"BINANCE_API_KEY configured: {'YES' if os.environ.get('BINANCE_API_KEY') else 'NO'}")
    print(f"BINANCE_API_SECRET configured: {'YES' if os.environ.get('BINANCE_API_SECRET') else 'NO'}")
    print(f"ACCOUNT_READ_ONLY: {str(settings.ACCOUNT_READ_ONLY).lower()}")
    print(f"ORDER_SUBMISSION_ENABLED: {str(settings.ORDER_SUBMISSION_ENABLED).lower()}")
    print(f"SHADOW_MODE: {str(settings.SHADOW_MODE).lower()}")
    print(f"TELEGRAM_ENABLED: {os.environ.get('TELEGRAM_ENABLED', 'false').lower()}")
    print(f"TELEGRAM_BOT_TOKEN configured: {'YES' if os.environ.get('TELEGRAM_BOT_TOKEN') else 'NO'}")
    print(f"TELEGRAM_CHAT_ID configured: {'YES' if os.environ.get('TELEGRAM_CHAT_ID') else 'NO'}")
    print(f"AI_ENABLED: {os.environ.get('AI_ENABLED', 'false').lower()}")
    print(
        "ORDER SUBMISSION: "
        + ("ENABLED - TESTNET ONLY" if settings.testnet_execution_enabled else "DISABLED")
    )


def _start_telegram_commands() -> None:
    """Start one authenticated, read-only Telegram command poller."""
    settings = get_settings()
    service = TelegramCommandService(
        settings,
        dashboard_provider=lambda: getattr(render_server.base, "RUNTIME", None),
        execution_status_provider=render_server.execution_status,
    )
    threading.Thread(
        target=service.serve_forever,
        name="telegram-command-listener",
        daemon=True,
    ).start()


if __name__ == "__main__":
    _apply_render_testnet_defaults()
    _safe_startup_status()
    _start_telegram_commands()
    render_server.main()
