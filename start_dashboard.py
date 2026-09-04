"""Cloud deployment entrypoint for the BTC Intelligence Console.

Render injects the public listening port through ``PORT``.  This entrypoint
always binds to 0.0.0.0 and never prints secret environment values.
"""

import os
import sys

from dashboard_server import main


def _port() -> int:
    raw = os.environ.get("PORT", "8080").strip()
    try:
        value = int(raw)
    except ValueError as exc:
        raise SystemExit("Invalid PORT environment variable") from exc
    if not 1 <= value <= 65535:
        raise SystemExit("PORT must be between 1 and 65535")
    return value


def _safe_startup_status() -> None:
    """Log configuration presence only; never log credentials."""
    print("BTC Intelligence Console cloud startup")
    print(f"RENDER runtime: {'YES' if os.environ.get('RENDER') else 'NO'}")
    print(f"BINANCE_TESTNET: {os.environ.get('BINANCE_TESTNET', 'true').lower()}")
    print(f"BINANCE_API_KEY configured: {'YES' if os.environ.get('BINANCE_API_KEY') else 'NO'}")
    print(f"BINANCE_API_SECRET configured: {'YES' if os.environ.get('BINANCE_API_SECRET') else 'NO'}")
    print(f"TELEGRAM_ENABLED: {os.environ.get('TELEGRAM_ENABLED', 'false').lower()}")
    print(f"TELEGRAM_BOT_TOKEN configured: {'YES' if os.environ.get('TELEGRAM_BOT_TOKEN') else 'NO'}")
    print(f"TELEGRAM_CHAT_ID configured: {'YES' if os.environ.get('TELEGRAM_CHAT_ID') else 'NO'}")
    print(f"AI_ENABLED: {os.environ.get('AI_ENABLED', 'false').lower()}")
    print("ORDER SUBMISSION: controlled by application safety gates")


if __name__ == "__main__":
    host = "0.0.0.0"
    port = _port()
    _safe_startup_status()
    sys.argv = ["dashboard_server.py", "--host", host, "--port", str(port)]
    main()
