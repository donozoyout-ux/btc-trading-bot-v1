"""Cloud deployment entrypoint for the BTC Intelligence Console.

Render injects ``PORT`` automatically.  Cloud startup goes through
``render_server`` so the UI gets an instant bootstrap panel and background
snapshot warm-up instead of appearing empty during cold starts.
"""

import os

from render_server import main


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
    print("ORDER SUBMISSION: DISABLED")


if __name__ == "__main__":
    _safe_startup_status()
    main()
