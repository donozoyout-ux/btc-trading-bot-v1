"""Provider-agnostic shadow market analyst with zero execution authority."""

from __future__ import annotations

import json
import time
from typing import Any, Dict, Optional

import requests


class AIAnalystError(RuntimeError):
    def __init__(self, category: str):
        self.category = category
        super().__init__(category)


class AIAnalystV4:
    PROVIDER_ENDPOINTS = {
        "openai": "https://api.openai.com/v1/responses",
        "groq": "https://api.groq.com/openai/v1/responses",
    }

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
            "timeframe_alignment",
            "trade_location_assessment",
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
            "timeframe_alignment": {
                "type": "object",
                "additionalProperties": False,
                "required": ["4h", "1h", "15m", "5m"],
                "properties": {
                    "4h": {"type": "string"},
                    "1h": {"type": "string"},
                    "15m": {"type": "string"},
                    "5m": {"type": "string"},
                },
            },
            "trade_location_assessment": {"type": "string"},
            "invalidation_watch": {"type": "array", "items": {"type": "string"}},
            "decision_explanation": {"type": "string"},
            "confidence": {"type": "integer", "minimum": 0, "maximum": 100},
            "execution_authority": {"type": "boolean", "enum": [False]},
        },
    }

    def __init__(
        self,
        api_key: Optional[str],
        model: str = "openai/gpt-oss-20b",
        enabled: bool = False,
        timeout: int = 20,
        provider: str = "groq",
    ):
        self._api_key = (api_key or "").strip() or None
        self.provider = str(provider or "groq").strip().lower()
        self.model = model
        self.enabled = bool(enabled)
        self.timeout = timeout
        self.endpoint = self.PROVIDER_ENDPOINTS.get(self.provider)
        self._last_result: Optional[Dict[str, Any]] = None
        self._last_candle_key: Optional[str] = None

    @property
    def configured(self) -> bool:
        return bool(
            self.enabled
            and self._api_key
            and self.endpoint
            and self.model
        )

    def safe_status(self) -> Dict[str, Any]:
        return {
            "enabled": self.enabled,
            "configured": self.configured,
            "status": "AVAILABLE" if self.configured else "UNAVAILABLE",
            "provider": self.provider.upper() if self.provider else None,
            "advisory_only": True,
            "shadow_mode": True,
            "execution_authority": False,
            "model": self.model,
        }

    def status(self) -> Dict[str, Any]:
        return self.safe_status()

    def unavailable(self, category: str = "AI_UNAVAILABLE") -> Dict[str, Any]:
        return {
            "status": "UNAVAILABLE",
            "error_category": category,
            "provider": self.provider.upper() if self.provider else None,
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
            "timeframe_alignment": {"4h": "", "1h": "", "15m": "", "5m": ""},
            "trade_location_assessment": "",
            "invalidation_watch": [],
            "decision_explanation": "",
            "confidence": 0,
            "observed_at": None,
            "model": self.model,
            "execution_authority": False,
        }

    @staticmethod
    def _extract_text(payload: Dict[str, Any]) -> str:
        if isinstance(payload.get("output_text"), str):
            return payload["output_text"]
        for item in payload.get("output", []):
            if not isinstance(item, dict):
                continue
            for content in item.get("content", []):
                if (
                    isinstance(content, dict)
                    and content.get("type") == "output_text"
                    and isinstance(content.get("text"), str)
                ):
                    return content["text"]
        raise AIAnalystError("AI_RESPONSE_INVALID")

    def _request_payload(self, prompt: str) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "model": self.model,
            "store": False,
            "input": prompt,
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "btc_market_analyst_v4",
                    "schema": self.SCHEMA,
                }
            },
            "max_output_tokens": 1200,
        }
        if self.provider == "openai":
            payload["text"]["format"]["strict"] = True
        elif self.provider == "groq":
            payload["reasoning"] = {"effort": "low"}
        return payload

    def analyze(self, context: Dict[str, Any]) -> Dict[str, Any]:
        if not self.configured:
            return self.unavailable()
        candle_key = context.get("closed_5m_candle_timestamp")
        if candle_key is not None and str(candle_key) == self._last_candle_key and self._last_result:
            return dict(self._last_result)

        prompt = (
            "You are BTC Market Analyst V4 running in SHADOW mode. "
            "You may explain and score the supplied deterministic strategy state, "
            "but you have ZERO execution authority. "
            "The deterministic strategy and risk engine are authoritative. "
            "Never change or invent entry, stop, targets, leverage, position size, "
            "risk decision, kill-switch state, performance guard, evidence gate, "
            "or deterministic final decision. "
            "NO_TRADE cannot become ENTRY and a blocked setup cannot become valid. "
            "An unsupported evidence-gate direction cannot be opened. If the deterministic "
            "decision is NO_TRADE, risk is rejected, a guard is blocked, or the setup is not "
            "eligible, trade_opinion MUST be WAIT or AVOID. "
            "Use only supplied evidence. Explicitly surface conflicts between 4H/1H/15M/5M, "
            "derivatives, news, and trade location. "
            "setup_quality is an advisory evidence-quality score, not a probability of profit. "
            "execution_authority MUST be false. "
            "Return only the required JSON schema.\n\n"
            + json.dumps(context, ensure_ascii=False, separators=(",", ":"))
        )

        try:
            response = requests.post(
                self.endpoint,
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                },
                json=self._request_payload(prompt),
                timeout=self.timeout,
            )
            if response.status_code in (401, 403):
                raise AIAnalystError(f"{self.provider.upper()}_AUTH_ERROR")
            if response.status_code == 429:
                raise AIAnalystError(f"{self.provider.upper()}_RATE_LIMIT")
            if response.status_code >= 400:
                raise AIAnalystError(f"{self.provider.upper()}_API_ERROR")

            parsed = json.loads(self._extract_text(response.json()))
            if not isinstance(parsed, dict):
                raise AIAnalystError("AI_RESPONSE_INVALID")
            bias = str(parsed.get("market_bias", "NEUTRAL")).upper()
            opinion = str(parsed.get("trade_opinion", "WAIT")).upper()
            if bias not in {"BULLISH", "BEARISH", "NEUTRAL", "MIXED"}:
                bias = "NEUTRAL"
            if opinion not in {"TAKE", "WAIT", "AVOID"}:
                opinion = "WAIT"
            shadow = context.get("shadow_contract") or {}
            blocked = (
                str(shadow.get("deterministic_final_decision") or "NO_TRADE").upper()
                not in {"LONG_ENTRY", "SHORT_ENTRY", "ENTRY"}
                or shadow.get("setup_eligible") is not True
                or bool(shadow.get("hard_blockers"))
                or shadow.get("risk_authorized") is False
                or shadow.get("evidence_gate_authorized") is False
            )
            if blocked and opinion == "TAKE":
                opinion = "WAIT"
            alignment = parsed.get("timeframe_alignment")
            if not isinstance(alignment, dict):
                alignment = {}
            result = {
                "status": "AVAILABLE",
                "error_category": None,
                "provider": self.provider.upper(),
                "market_view": str(parsed.get("market_view", "")),
                "market_bias": bias,
                "setup_quality": max(
                    0, min(100, int(parsed.get("setup_quality", 0)))
                ),
                "best_setup": str(parsed.get("best_setup", "")),
                "trade_opinion": opinion,
                "confirmations": [
                    str(value) for value in parsed.get("confirmations", [])
                ],
                "conflicts": [str(value) for value in parsed.get("conflicts", [])],
                "risk_notes": [str(value) for value in parsed.get("risk_notes", [])],
                "news_summary": str(parsed.get("news_summary", "")),
                "derivatives_summary": str(
                    parsed.get("derivatives_summary", "")
                ),
                "timeframe_alignment": {
                    tf: str(alignment.get(tf, ""))
                    for tf in ("4h", "1h", "15m", "5m")
                },
                "trade_location_assessment": str(
                    parsed.get("trade_location_assessment", "")
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
            self._last_candle_key = str(candle_key) if candle_key is not None else None
            return result
        except AIAnalystError:
            raise
        except requests.RequestException:
            raise AIAnalystError(f"{self.provider.upper()}_NETWORK_ERROR") from None
        except (ValueError, TypeError, KeyError, json.JSONDecodeError):
            raise AIAnalystError("AI_RESPONSE_INVALID") from None


AIAnalystV3 = AIAnalystV4
AIAnalystV2 = AIAnalystV4
AIAnalyst = AIAnalystV4
