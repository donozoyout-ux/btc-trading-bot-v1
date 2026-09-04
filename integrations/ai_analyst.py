"""Optional OpenAI advisory analyst with a deliberately non-executable schema."""

from __future__ import annotations

import json
from typing import Any, Dict, Optional

import requests


class AIAnalystError(RuntimeError):
    def __init__(self, category: str):
        self.category = category
        super().__init__(category)


class AIAnalystV2:
    ENDPOINT = "https://api.openai.com/v1/responses"
    SCHEMA = {
        "type": "object",
        "additionalProperties": False,
        "required": ["market_view", "best_setup", "conflicts", "risk_notes", "decision_explanation", "confidence", "execution_authority"],
        "properties": {
            "market_view": {"type": "string"},
            "best_setup": {"type": "string"},
            "conflicts": {"type": "array", "items": {"type": "string"}},
            "risk_notes": {"type": "array", "items": {"type": "string"}},
            "decision_explanation": {"type": "string"},
            "confidence": {"type": "integer", "minimum": 0, "maximum": 100},
            "execution_authority": {"type": "boolean", "const": False},
        },
    }

    def __init__(self, api_key: Optional[str], model: str = "gpt-5", enabled: bool = False, timeout: int = 20):
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
            "status": "AVAILABLE" if self._last_result else "READY" if self.configured else "UNAVAILABLE",
            "advisory_only": True,
            "execution_authority": False,
            "model": self.model if self.configured else None,
        }

    @staticmethod
    def unavailable(category: str = "AI_UNAVAILABLE") -> Dict[str, Any]:
        return {
            "status": "UNAVAILABLE",
            "error_category": category,
            "market_view": "",
            "best_setup": "",
            "conflicts": [],
            "risk_notes": [],
            "decision_explanation": "",
            "confidence": 0,
            "execution_authority": False,
        }

    @staticmethod
    def _extract_text(payload: Dict[str, Any]) -> str:
        if isinstance(payload.get("output_text"), str):
            return payload["output_text"]
        for item in payload.get("output", []):
            for content in item.get("content", []):
                if content.get("type") == "output_text" and isinstance(content.get("text"), str):
                    return content["text"]
        raise AIAnalystError("AI_RESPONSE_INVALID")

    def analyze(self, context: Dict[str, Any]) -> Dict[str, Any]:
        if not self.configured:
            return self.unavailable()
        prompt = (
            "You are a read-only BTC market analyst. Explain the supplied deterministic decision. "
            "Never change setup, direction, entry, stop, targets, size, risk result, or kill-switch result. "
            "A risk rejection is always NO TRADE. Return only the required JSON.\n\n"
            + json.dumps(context, ensure_ascii=False, separators=(",", ":"))
        )
        payload = {
            "model": self.model,
            "store": False,
            "input": prompt,
            "text": {"format": {"type": "json_schema", "name": "btc_advisory", "strict": True, "schema": self.SCHEMA}},
            "max_output_tokens": 900,
        }
        try:
            response = requests.post(
                self.ENDPOINT,
                headers={"Authorization": f"Bearer {self._api_key}", "Content-Type": "application/json"},
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
                "best_setup": str(parsed.get("best_setup", "")),
                "conflicts": [str(value) for value in parsed.get("conflicts", [])],
                "risk_notes": [str(value) for value in parsed.get("risk_notes", [])],
                "decision_explanation": str(parsed.get("decision_explanation", "")),
                "confidence": max(0, min(100, int(parsed.get("confidence", 0)))),
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
