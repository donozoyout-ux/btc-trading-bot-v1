"""Optional advisory AI analyst.

The AI layer explains already-computed market evidence. It has no execution
method and cannot bypass deterministic strategy, risk or kill-switch decisions.
"""

from __future__ import annotations

import json
from typing import Any, Dict, Optional

import requests
from loguru import logger


class AIAnalyst:
    API_URL = "https://api.openai.com/v1/responses"

    def __init__(
        self,
        api_key: Optional[str],
        model: str,
        enabled: bool = False,
        timeout: int = 20,
        provider: str = "openai",
    ):
        self.api_key = api_key or None
        self.model = model
        self.enabled = bool(enabled)
        self.timeout = timeout
        self.provider = (provider or "openai").lower()

    @property
    def configured(self) -> bool:
        return bool(self.enabled and self.api_key and self.model and self.provider == "openai")

    def status(self) -> Dict[str, Any]:
        return {
            "enabled": self.enabled,
            "configured": self.configured,
            "provider": self.provider,
            "model": self.model if self.enabled else None,
            "execution_authority": False,
        }

    @staticmethod
    def _safe_context(snapshot: Dict[str, Any]) -> Dict[str, Any]:
        decision = snapshot.get("decision") or {}
        market = snapshot.get("market") or {}
        chart = snapshot.get("chart_reading") or {}
        news = snapshot.get("news") or {}
        account = snapshot.get("account") or {}
        sources = snapshot.get("sources") or {}

        return {
            "symbol": "BTCUSDT",
            "market": {
                "price": market.get("price"),
                "mark_price": market.get("mark_price"),
                "change_24h_pct": market.get("change_24h_pct"),
                "funding_rate": market.get("funding_rate"),
                "open_interest_btc": market.get("open_interest_btc"),
                "long_short_ratio": market.get("long_short_ratio"),
                "taker_buy_sell_ratio": market.get("taker_buy_sell_ratio"),
            },
            "decision_engine": {
                "regime": decision.get("regime"),
                "regime_score": decision.get("regime_score"),
                "confidence": decision.get("confidence"),
                "volatility": decision.get("volatility"),
                "structure_4h": decision.get("structure_4h"),
                "structure_1h": decision.get("structure_1h"),
                "location": decision.get("location"),
                "setup": decision.get("setup"),
                "trigger_state": decision.get("trigger_state"),
                "derivatives": decision.get("derivatives"),
                "risk_status": decision.get("risk_status"),
                "final_decision": decision.get("final_decision"),
                "reason": decision.get("reason"),
                "trade_plan": decision.get("trade_plan"),
            },
            "chart_reading": chart,
            "news": {
                "risk": news.get("risk"),
                "sentiment": news.get("sentiment"),
                "headlines": [
                    {
                        "title": item.get("title"),
                        "risk_score": item.get("risk_score"),
                        "sentiment": item.get("sentiment"),
                    }
                    for item in (news.get("items") or [])[:8]
                ],
            },
            "demo_account": {
                "connected": account.get("connected"),
                "environment": account.get("environment"),
                "wallet_balance_usdt": account.get("wallet_balance_usdt"),
                "available_balance_usdt": account.get("available_balance_usdt"),
                "unrealized_pnl_usdt": account.get("unrealized_pnl_usdt"),
                "open_position_count": account.get("open_position_count"),
            },
            "data_sources": {
                name: value.get("status") if isinstance(value, dict) else None
                for name, value in sources.items()
            },
        }

    @staticmethod
    def _extract_text(payload: Dict[str, Any]) -> Optional[str]:
        if isinstance(payload.get("output_text"), str):
            return payload["output_text"].strip()
        chunks = []
        for item in payload.get("output") or []:
            if not isinstance(item, dict) or item.get("type") != "message":
                continue
            for content in item.get("content") or []:
                if isinstance(content, dict) and content.get("type") == "output_text":
                    text = content.get("text")
                    if text:
                        chunks.append(str(text))
        return "\n".join(chunks).strip() or None

    def analyze(self, snapshot: Dict[str, Any]) -> Dict[str, Any]:
        if not self.configured:
            return {
                "status": "UNAVAILABLE",
                "configured": False,
                "execution_authority": False,
                "analysis": None,
            }

        context = self._safe_context(snapshot)
        instructions = (
            "You are the advisory analyst for a deterministic BTC futures system. "
            "Never invent market data. Never claim certainty. Do not tell the execution "
            "engine to bypass risk, kill-switch, setup or trigger rules. The deterministic "
            "final_decision remains authoritative. Explain conflicts and missing data. "
            "Return concise Turkish text with exactly these headings: Piyasa, Kanıt, "
            "Çatışmalar, Karar Yorumu, Güven. If deterministic final_decision is NO_TRADE "
            "or WAIT, do not recommend overriding it."
        )
        body = {
            "model": self.model,
            "instructions": instructions,
            "input": json.dumps(context, ensure_ascii=False, separators=(",", ":")),
            "store": False,
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        try:
            resp = requests.post(self.API_URL, headers=headers, json=body, timeout=self.timeout)
            resp.raise_for_status()
            payload = resp.json()
            text = self._extract_text(payload)
            return {
                "status": "HEALTHY" if text else "DEGRADED",
                "configured": True,
                "provider": self.provider,
                "model": payload.get("model") or self.model,
                "execution_authority": False,
                "analysis": text,
                "response_id": payload.get("id"),
            }
        except Exception as exc:
            # Do not log request headers/body because the header contains the API key.
            logger.warning(f"AI analyst unavailable: {type(exc).__name__}")
            return {
                "status": "ERROR",
                "configured": True,
                "provider": self.provider,
                "model": self.model,
                "execution_authority": False,
                "analysis": None,
                "error": type(exc).__name__,
            }
