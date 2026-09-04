"""Telegram notification client for deterministic bot events."""

from __future__ import annotations

import hashlib
import time
from typing import Any, Dict, Optional

import requests
from loguru import logger


class TelegramNotifier:
    def __init__(
        self,
        bot_token: Optional[str],
        chat_id: Optional[str],
        enabled: bool = False,
        timeout: int = 8,
        dedupe_seconds: int = 120,
    ):
        self.bot_token = bot_token or None
        self.chat_id = str(chat_id) if chat_id not in (None, "") else None
        self.enabled = bool(enabled)
        self.timeout = timeout
        self.dedupe_seconds = dedupe_seconds
        self._last_hash: Optional[str] = None
        self._last_sent_at: float = 0.0

    @property
    def configured(self) -> bool:
        return bool(self.enabled and self.bot_token and self.chat_id)

    def status(self) -> Dict[str, Any]:
        return {
            "enabled": self.enabled,
            "configured": self.configured,
            "chat_id_configured": bool(self.chat_id),
            "token_configured": bool(self.bot_token),
        }

    def send_message(self, text: str, force: bool = False) -> Dict[str, Any]:
        if not self.configured:
            return {"ok": False, "status": "UNAVAILABLE", "reason": "Telegram not configured"}

        body = str(text).strip()
        if not body:
            return {"ok": False, "status": "SKIPPED", "reason": "Empty message"}

        digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
        now = time.time()
        if not force and digest == self._last_hash and (now - self._last_sent_at) < self.dedupe_seconds:
            return {"ok": True, "status": "DEDUPED"}

        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        payload = {
            "chat_id": self.chat_id,
            "text": body,
            "disable_web_page_preview": True,
        }
        try:
            resp = requests.post(url, json=payload, timeout=self.timeout)
            resp.raise_for_status()
            data = resp.json()
            if not data.get("ok"):
                return {"ok": False, "status": "ERROR", "reason": "Telegram API rejected message"}
            self._last_hash = digest
            self._last_sent_at = now
            return {"ok": True, "status": "SENT"}
        except Exception as exc:
            # Never log token, URL, headers or request body.
            logger.warning(f"Telegram notification failed: {type(exc).__name__}")
            return {"ok": False, "status": "ERROR", "reason": type(exc).__name__}

    @staticmethod
    def format_decision(snapshot: Dict[str, Any]) -> str:
        decision = snapshot.get("decision") or {}
        market = snapshot.get("market") or {}
        account = snapshot.get("account") or {}
        plan = decision.get("trade_plan") or {}
        risk = decision.get("risk_assessment") or {}

        def val(value, default="—"):
            return default if value in (None, "") else value

        lines = [
            f"BTCUSDT — {val(decision.get('final_decision'), 'WAIT')}",
            "",
            f"Price: {val(market.get('price'))}",
            f"Regime: {val(decision.get('regime'))}",
            f"4H / 1H Structure: {val(decision.get('structure_4h'))} / {val(decision.get('structure_1h'))}",
            f"Setup: {val(decision.get('setup'))}",
            f"Trigger: {val(decision.get('trigger_state'))}",
            f"Derivatives: {val(decision.get('derivatives'))}",
            f"Risk: {val(decision.get('risk_status'))}",
        ]

        if plan:
            lines.extend(
                [
                    "",
                    f"Entry: {val(plan.get('entry_price'))}",
                    f"Stop: {val(plan.get('stop_loss'))}",
                    f"TP1: {val(plan.get('tp1'))}",
                    f"TP2: {val(plan.get('tp2'))}",
                    f"R:R: {val(risk.get('risk_reward') or plan.get('risk_reward'))}",
                ]
            )

        if account.get("connected"):
            lines.extend(
                [
                    "",
                    "DEMO ACCOUNT",
                    f"Wallet: {val(account.get('wallet_balance_usdt'))} USDT",
                    f"Available: {val(account.get('available_balance_usdt'))} USDT",
                    f"uPnL: {val(account.get('unrealized_pnl_usdt'))} USDT",
                ]
            )

        lines.extend(["", f"Reason: {val(decision.get('reason'))}"])
        return "\n".join(str(x) for x in lines)
