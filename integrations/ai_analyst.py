"""Optional OpenAI shadow market analyst with zero execution authority."""

from __future__ import annotations

import json
import time
from typing import Any, Dict, Optional

import requests


class AIAnalystError(RuntimeError):
    def __init__(self, category: str):
        self.category = category
        super().__init__(category)


class AIAnalystV3:
    ENDPOINT = "https://api.openai.com/v1/responses"
    SCHEMA = {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "market_view",
            "market_bias",
            "setup_quality",
            "best_setup",
            "trade_opinion",
            "confirmations",
            "conflicts",
            "risk_notes",
            "news_summary",
            "derivatives_summary",
            "invalidation_watch",
            "decision_explanation",
            "confidence",
            "execution_authority",
        ],
        "properties": {
            "market_view": {"type": "string"},
            "market_bias": {
                "type": "string",
                "enum": ["BULLISH", "BEARISH", "NEUTRAL", "MIXED"],
            },
            "setup_quality": {"type": "integer", "minimum": 0, "maximum": 100},
            "best_setup": {"type": "string"},
            "trade_opinion": {
                "type": "string",
                "enum": ["TAKE", "WAIT", "AVOID"],
            },
            "confirmations": {"type": "array", "items": {"type": "string"}},
            "conflicts": {"type": "array", "items": {"type": "string"}},
            "risk_notes": {"type": "array", "items": {"type": "string"}},
            "news_summary": {"type": "string"},
            "derivatives_summary": {"type": "string"},
            "invalidation_watch": {"type": "array", "items": {"type": "string"}},
            "decision_explanation": {"type": "string"},
            "confidence": {"type": "integer", "minimum": 0, "maximum": 100},
            "execution_authority": {"type": "boolean", "const": False},
        },
    }

    def __init__(
        self,
        api_key: Optional[str],
        model: str = "gpt-5.6-luna",
        enabled: bool = False,
        timeout: int = 20,
    ):
        self._api_key = (api_key or "").strip() or None
        self.model = model
        self.enabled = bool(enabled)
        self.timeout = timeout
        self._last_result: Optional[Dict[str, Any]] = None

    @property
    def configured(self) -> bool:
        return bool(self.enabled and self._api_key)

    def safe_status(self) -> Dict[str, Any]:
        return {
            "enabled": self.enabled,
            "configured": self.configured,
            "status": "AVAILABLE"
            if self._last_result
            else "READY"
            if self.configured
            else "UNAVAILABLE",
            "advisory_only": True,
            "shadow_mode": True,
            "execution_authority": False,
            "model": self.model if self.configured else None,
        }

    def status(self) -> Dict[str, Any]:
        return self.safe_status()

    @staticmethod
    def unavailable(category: str = "AI_UNAVAILABLE") -> Dict[str, Any]:
        return {
            "status": "UNAVAILABLE",
            "error_category": category,
            "market_view": "",
            "market_bias": "NEUTRAL",
            "setup_quality": 0,
            "best_setup": "",
            "trade_opinion": "WAIT",
            "confirmations": [],
            "conflicts": [],
            "risk_notes": [],
            "news_summary": "",
            "derivatives_summary": "",
            "invalidation_watch": [],
            "decision_explanation": "",
            "confidence": 0,
            "observed_at": None,
            "model": None,
            "execution_authority": False,
        }

    @staticmethod
    def _extract_text(payload: Dict[str, Any]) -> str:
        if isinstance(payload.get("output_text"), str):
            return payload["output_text"]
        for item in payload.get("output", []):
            for content in item.get("content", []):
                if content.get("type") == "output_text" and isinstance(
                    content.get("text"), str
                ):
                    return content["text"]
        raise AIAnalystError("AI_RESPONSE_INVALID")

    def analyze(self, context: Dict[str, Any]) -> Dict[str, Any]:
        if not self.configured:
            return self.unavailable()

        prompt = (
            "You are BTC Market Analyst V3 running in SHADOW mode. "
            "You may explain and score the supplied deterministic strategy state, "
            "but you have ZERO execution authority. "
            "Never change or invent entry, stop, targets, leverage, position size, "
            "risk decision, kill-switch state, performance guard, evidence gate, "
            "or deterministic final decision. "
            "If deterministic_final_decision is NO_TRADE, risk is rejected, a guard "
            "is blocked, or the setup is not eligible, trade_opinion MUST be WAIT or AVOID. "
            "Use only supplied evidence. Explicitly surface conflicts between 4H/1H/15M/5M, "
            "derivatives, news, and trade location. "
            "setup_quality is an advisory evidence-quality score, not a probability of profit. "
            "Return only the required JSON schema.\n\n"
            + json.dumps(context, ensure_ascii=False, separators=(",", ":"))
        )
        payload = {
            "model": self.model,
            "store": False,
            "input": prompt,
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "btc_market_analyst_v3",
                    "strict": True,
                    "schema": self.SCHEMA,
                }
            },
            "max_output_tokens": 1200,
        }
        try:
            response = requests.post(
                self.ENDPOINT,
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
                timeout=self.timeout,
            )
            if response.status_code in (401, 403):
                raise AIAnalystError("AI_AUTH_ERROR")
            if response.status_code == 429:
                raise AIAnalystError("AI_RATE_LIMIT")
            if response.status_code >= 400:
                raise AIAnalystError("AI_API_ERROR")

            parsed = json.loads(self._extract_text(response.json()))
            result = {
                "status": "AVAILABLE",
                "error_category": None,
                "market_view": str(parsed.get("market_view", "")),
                "market_bias": str(parsed.get("market_bias", "NEUTRAL")),
                "setup_quality": max(
                    0, min(100, int(parsed.get("setup_quality", 0)))
                ),
                "best_setup": str(parsed.get("best_setup", "")),
                "trade_opinion": str(parsed.get("trade_opinion", "WAIT")),
                "confirmations": [
                    str(value) for value in parsed.get("confirmations", [])
                ],
                "conflicts": [str(value) for value in parsed.get("conflicts", [])],
                "risk_notes": [str(value) for value in parsed.get("risk_notes", [])],
                "news_summary": str(parsed.get("news_summary", "")),
                "derivatives_summary": str(
                    parsed.get("derivatives_summary", "")
                ),
                "invalidation_watch": [
                    str(value) for value in parsed.get("invalidation_watch", [])
                ],
                "decision_explanation": str(
                    parsed.get("decision_explanation", "")
                ),
                "confidence": max(
                    0, min(100, int(parsed.get("confidence", 0)))
                ),
                "observed_at": int(time.time() * 1000),
                "model": self.model,
                "execution_authority": False,
            }
            self._last_result = result
            return result
        except AIAnalystError:
            raise
        except requests.RequestException:
            raise AIAnalystError("AI_NETWORK_ERROR") from None
        except (ValueError, TypeError, KeyError, json.JSONDecodeError):
            raise AIAnalystError("AI_RESPONSE_INVALID") from None


AIAnalystV2 = AIAnalystV3
AIAnalyst = AIAnalystV3
