"""Presentation helpers. Raw backend timestamps are never mutated."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo


ISTANBUL = ZoneInfo("Europe/Istanbul")


def text(value: Any, fallback: str = "—") -> str:
    if value is None or value == "":
        return fallback
    return str(value)


def number(value: Any, digits: int = 2, suffix: str = "") -> str:
    try:
        return f"{float(value):,.{digits}f}{suffix}"
    except (TypeError, ValueError):
        return "—"


def price(value: Any) -> str:
    return number(value, 2, " USDT")


def ratio(value: Any) -> str:
    return number(value, 2, "R")


def istanbul_time(timestamp: Any) -> str:
    try:
        numeric = float(timestamp)
        seconds = numeric / 1000 if numeric > 10_000_000_000 else numeric
        return datetime.fromtimestamp(seconds, tz=timezone.utc).astimezone(ISTANBUL).strftime("%d.%m.%Y %H:%M:%S TSİ")
    except (TypeError, ValueError, OSError):
        return "—"
