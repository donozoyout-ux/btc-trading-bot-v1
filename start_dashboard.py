"""Cloud deployment entrypoint for the BTC Intelligence Console.

Render injects ``PORT`` automatically.  Cloud startup goes through
``render_server`` so the UI gets an instant bootstrap panel and background
snapshot warm-up instead of appearing empty during cold starts.
"""

import os
import threading

from config.settings import get_settings
import render_server
from notifications.telegram_commands import TelegramCommandService


def _safe_startup_status() -> None:
    """Log configuration presence only; never log credentials."""
    settings = get_settings()
    print("BTC Intelligence Console cloud startup")
    print(f"RENDER runtime: {'YES' if os.environ.get('RENDER') else 'NO'}")
    print(f"BINANCE_TESTNET: {os.environ.get('BINANCE_TESTNET', 'true').lower()}")
    print(f"BINANCE_API_KEY configured: {'YES' if os.environ.get('BINANCE_API_KEY') else 'NO'}")
    print(f"BINANCE_API_SECRET configured: {'YES' if os.environ.get('BINANCE_API_SECRET') else 'NO'}")
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
    _safe_startup_status()
    _start_telegram_commands()
    render_server.main()
