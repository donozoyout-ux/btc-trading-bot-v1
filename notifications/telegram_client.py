"""Minimal outbound-only Telegram Bot API client.

The bot token stays in the request URL on the backend and is never returned,
logged, or exposed to the dashboard.  Incoming commands and trading actions
are intentionally outside this integration.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

import requests


class TelegramError(RuntimeError):
    """Sanitized Telegram failure safe for logs and health payloads."""

    def __init__(self, category: str):
        super().__init__(category)
        self.category = category


class TelegramClient:
    """Backend-only notification client for one configured Telegram chat."""

    API_ROOT = "https://api.telegram.org"

    def __init__(
        self,
        bot_token: Optional[str],
        chat_id: Optional[str],
        enabled: bool = False,
        timeout: int = 8,
    ):
        self._bot_token = (bot_token or "").strip() or None
        self._chat_id = (chat_id or "").strip() or None
        self.enabled = bool(enabled)
        self.timeout = timeout
        self.session = requests.Session()

    @property
    def configured(self) -> bool:
        return bool(self._bot_token and self._chat_id)

    @property
    def token_configured(self) -> bool:
        return bool(self._bot_token)

    def _url(self, method: str) -> str:
        if not self._bot_token:
            raise TelegramError("TELEGRAM_UNAVAILABLE")
        return f"{self.API_ROOT}/bot{self._bot_token}/{method}"

    @staticmethod
    def _category(status_code: int) -> str:
        if status_code in (401, 403):
            return "TELEGRAM_AUTH_ERROR"
        if status_code == 429:
            return "TELEGRAM_RATE_LIMITED"
        return "TELEGRAM_API_ERROR"

    def _post(self, method: str, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        if not self.enabled or not self.token_configured:
            raise TelegramError("TELEGRAM_UNAVAILABLE")
        try:
            response = self.session.post(
                self._url(method), json=payload or {}, timeout=self.timeout
            )
            if response.status_code >= 400:
                raise TelegramError(self._category(response.status_code))
            data = response.json()
            if not data.get("ok"):
                raise TelegramError("TELEGRAM_API_ERROR")
            return data
        except TelegramError:
            raise
        except requests.RequestException:
            raise TelegramError("TELEGRAM_NETWORK_ERROR") from None
        except (TypeError, ValueError):
            raise TelegramError("TELEGRAM_API_ERROR") from None

    def get_me(self) -> Dict[str, Any]:
        """Verify the token and return only non-secret bot identity fields."""
        result = self._post("getMe").get("result", {})
        return {
            "id": result.get("id"),
            "username": result.get("username"),
            "display_name": " ".join(
                value for value in (result.get("first_name"), result.get("last_name")) if value
            ) or None,
        }

    def send_message(self, text: str) -> Dict[str, Any]:
        """Send one plain-text notification to the configured chat."""
        if not self._chat_id:
            raise TelegramError("TELEGRAM_UNAVAILABLE")
        if not text or len(text) > 4096:
            raise ValueError("Telegram message must contain 1-4096 characters")
        result = self._post(
            "sendMessage",
            {
                "chat_id": self._chat_id,
                "text": text,
                "disable_web_page_preview": True,
            },
        ).get("result", {})
        return {
            "sent": True,
            "message_id": result.get("message_id"),
            "date": result.get("date"),
        }

    def discover_chats(self) -> list[Dict[str, Any]]:
        """List chats that recently sent the bot a message, without executing commands."""
        updates = self._post(
            "getUpdates", {"timeout": 0, "allowed_updates": ["message"]}
        ).get("result", [])
        chats: Dict[str, Dict[str, Any]] = {}
        for update in updates:
            chat = (update.get("message") or {}).get("chat") or {}
            chat_id = chat.get("id")
            if chat_id is None:
                continue
            chats[str(chat_id)] = {
                "chat_id": str(chat_id),
                "type": chat.get("type"),
                "username": chat.get("username"),
                "title": chat.get("title")
                or " ".join(
                    value for value in (chat.get("first_name"), chat.get("last_name")) if value
                )
                or None,
            }
        return list(chats.values())

    def safe_status(self) -> Dict[str, Any]:
        """Return configuration state without returning token or chat id."""
        return {
            "enabled": self.enabled,
            "configured": self.configured,
            "token_configured": self.token_configured,
            "mode": "NOTIFICATIONS_ONLY",
            "commands_enabled": False,
            "trading_actions_enabled": False,
        }
