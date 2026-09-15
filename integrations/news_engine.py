"""RSS news intelligence with recency, source quality and BTC impact scoring."""

from __future__ import annotations

import math
import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Dict, Iterable, List, Optional
from urllib.parse import urlparse

import requests


class NewsEngineV4:
    CATEGORIES = {
        "FED": ("federal reserve", " fed ", "powell"),
        "CPI": ("cpi", "consumer price", "inflation"),
        "FOMC": ("fomc",),
        "ETF": ("etf", "exchange-traded fund"),
        "SEC": (" sec ", "securities and exchange commission"),
        "REGULATION": ("regulation", "regulator", "legislation", "lawmakers"),
        "EXCHANGE": ("binance", "coinbase", "kraken", "exchange"),
        "HACK": ("hack", "exploit", "breach", "stolen"),
        "SECURITY": ("security", "vulnerability"),
        "LIQUIDATION": ("liquidation", "liquidated"),
        "BITCOIN": ("bitcoin", "btc"),
        "CRYPTO_MARKET": ("crypto", "digital asset"),
        "GEOPOLITICS": ("war", "sanction", "geopolit", "military conflict"),
        "STABLECOIN": ("stablecoin", "usdt", "usdc", "depeg"),
        "MINING": ("bitcoin miner", "bitcoin mining", "hashrate"),
        "WHALE_FLOW": ("whale", "large holder", "exchange inflow"),
        "TREASURY": ("bitcoin treasury", "corporate treasury"),
        "BANKING": ("bank failure", "banking crisis", "bank run"),
        "ETF_FLOW": ("etf inflow", "etf outflow", "spot bitcoin etf flow"),
        "MACRO": (
            "jobs report",
            "payroll",
            "unemployment",
            "interest rate",
            "treasury",
            "recession",
            "macro",
        ),
    }
    CATEGORY_IMPACT = {
        "HACK": 95,
        "SECURITY": 85,
        "FOMC": 90,
        "FED": 85,
        "CPI": 85,
        "LIQUIDATION": 80,
        "ETF": 75,
        "SEC": 75,
        "REGULATION": 70,
        "EXCHANGE": 65,
        "MACRO": 65,
        "BITCOIN": 55,
        "CRYPTO_MARKET": 45,
        "GEOPOLITICS": 70,
        "STABLECOIN": 80,
        "MINING": 45,
        "WHALE_FLOW": 55,
        "TREASURY": 55,
        "BANKING": 75,
        "ETF_FLOW": 80,
        "OTHER": 25,
    }
    SOURCE_WEIGHTS = {
        "coindesk.com": 1.00,
        "www.coindesk.com": 1.00,
        "cointelegraph.com": 0.85,
        "www.cointelegraph.com": 0.85,
    }
    POSITIVE = (
        "approval",
        "approved",
        "inflow",
        "adoption",
        "surge",
        "rally",
        "record high",
        "beats expectations",
        "rate cut",
        "cuts rates",
    )
    NEGATIVE = (
        "hack",
        "exploit",
        "breach",
        "ban",
        "lawsuit",
        "outflow",
        "crash",
        "liquidation",
        "rate hike",
        "raises rates",
        "hot inflation",
    )
    HIGH_IMPORTANCE = (
        "fed",
        "fomc",
        "cpi",
        "etf",
        "sec",
        "hack",
        "exploit",
        "liquidation",
    )

    @classmethod
    def _score_item(cls, item: Dict[str, Any]) -> Dict[str, Any]:
        """Backward-compatible transparent headline scorer used by tests/tools."""
        title = str(item.get("title") or "")
        lowered = title.lower()
        category = cls._category(title)
        high_hits = sorted(term for term in cls.HIGH_IMPORTANCE if term in lowered)
        direction_raw = cls._raw_direction(title)
        result = dict(item)
        result.update(
            {
                "category": category,
                "risk_score": min(
                    100,
                    cls.CATEGORY_IMPACT.get(category, 25)
                    + len(high_hits) * 8
                    + (15 if any(term in lowered for term in cls.NEGATIVE) else 0),
                ),
                "risk_terms": high_hits,
                "sentiment": "BULLISH"
                if direction_raw > 0
                else "BEARISH"
                if direction_raw < 0
                else "NEUTRAL",
                "sentiment_score": direction_raw,
            }
        )
        return result

    def __init__(
        self,
        urls: Iterable[str],
        enabled: bool = True,
        timeout: int = 6,
        cache_seconds: int = 300,
    ):
        self.urls = [url.strip() for url in urls if url and url.strip()]
        self.enabled = enabled
        self.timeout = timeout
        self.cache_seconds = cache_seconds
        self._cache: Optional[Dict[str, Any]] = None
        self._cached_at = 0.0

    @staticmethod
    def _text(node: ET.Element, names: Iterable[str]) -> Optional[str]:
        for child in node.iter():
            tag = child.tag.rsplit("}", 1)[-1].lower()
            if tag in names and child.text and child.text.strip():
                return child.text.strip()
        return None

    @staticmethod
    def _url(node: ET.Element) -> Optional[str]:
        for child in node.iter():
            if child.tag.rsplit("}", 1)[-1].lower() == "link":
                value = child.attrib.get("href") or child.text
                if value and value.strip().startswith(("http://", "https://")):
                    return value.strip()
        return None

    @staticmethod
    def _published(value: Optional[str]) -> Optional[str]:
        if not value:
            return None
        try:
            dt = parsedate_to_datetime(value)
            if not dt.tzinfo:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc).isoformat()
        except (TypeError, ValueError, OverflowError):
            try:
                dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
                if not dt.tzinfo:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt.astimezone(timezone.utc).isoformat()
            except (TypeError, ValueError):
                return None

    @staticmethod
    def _title_key(title: str) -> str:
        return re.sub(r"[^a-z0-9]+", " ", title.lower()).strip()

    @classmethod
    def _is_duplicate(cls, left: str, right: str) -> bool:
        """Suppress syndicated headlines with small publisher wording changes."""
        a, b = set(cls._title_key(left).split()), set(cls._title_key(right).split())
        if not a or not b:
            return False
        return len(a & b) / len(a | b) >= 0.72

    @classmethod
    def _category(cls, title: str) -> str:
        haystack = f" {title.lower()} "
        # More specific V4 categories must win over their broad parents.
        for category in ("ETF_FLOW", "TREASURY", "WHALE_FLOW", "STABLECOIN", "BANKING", "MINING", "GEOPOLITICS"):
            if any(keyword in haystack for keyword in cls.CATEGORIES[category]):
                return category
        for category, keywords in cls.CATEGORIES.items():
            if any(keyword in haystack for keyword in keywords):
                return category
        return "OTHER"

    @classmethod
    def _raw_direction(cls, title: str) -> int:
        lowered = title.lower()
        positives = sum(word in lowered for word in cls.POSITIVE)
        negatives = sum(word in lowered for word in cls.NEGATIVE)
        return max(-3, min(3, positives - negatives))

    @staticmethod
    def _age_hours(published_at: Optional[str], now: datetime) -> Optional[float]:
        if not published_at:
            return None
        try:
            dt = datetime.fromisoformat(published_at.replace("Z", "+00:00"))
            if not dt.tzinfo:
                dt = dt.replace(tzinfo=timezone.utc)
            return max(0.0, (now - dt.astimezone(timezone.utc)).total_seconds() / 3600.0)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _recency_weight(age_hours: Optional[float]) -> float:
        if age_hours is None:
            return 0.25
        # Smooth decay: half-life ~8 hours, old headlines lose trade relevance.
        return max(0.05, math.exp(-age_hours / 11.54))

    @classmethod
    def _normalize(
        cls,
        source_url: str,
        node: ET.Element,
        *,
        now: datetime,
    ) -> Optional[Dict[str, Any]]:
        title = cls._text(node, ("title",))
        if not title:
            return None
        source = urlparse(source_url).netloc.lower()
        published_at = cls._published(
            cls._text(node, ("pubdate", "published", "updated"))
        )
        category = cls._category(title)
        lowered = title.lower()
        importance = (
            "HIGH"
            if any(word in lowered for word in cls.HIGH_IMPORTANCE)
            else "MEDIUM"
            if category != "OTHER"
            else "LOW"
        )
        age_hours = cls._age_hours(published_at, now)
        recency = cls._recency_weight(age_hours)
        source_weight = cls.SOURCE_WEIGHTS.get(source, 0.65)
        direction_raw = cls._raw_direction(title)
        impact = cls.CATEGORY_IMPACT.get(category, 25)
        if importance == "HIGH":
            impact += 10
        impact_score = min(100.0, impact * recency * source_weight)
        direction_score = direction_raw * 25.0 * recency * source_weight
        return {
            "source": source,
            "source_weight": round(source_weight, 2),
            "title": title,
            "title_key": cls._title_key(title),
            "published_at": published_at,
            "age_hours": round(age_hours, 2) if age_hours is not None else None,
            "category": category,
            "sentiment": "BULLISH"
            if direction_score > 8
            else "BEARISH"
            if direction_score < -8
            else "NEUTRAL",
            "sentiment_score": round(direction_score, 2),
            "importance": importance,
            "btc_relevance": "HIGH"
            if category in ("BITCOIN", "ETF", "ETF_FLOW", "LIQUIDATION", "EXCHANGE", "HACK", "STABLECOIN")
            else "MEDIUM"
            if category != "OTHER"
            else "LOW",
            "impact_score": round(impact_score, 2),
            "risk_score": round(
                min(
                    100.0,
                    impact_score
                    + (20 if category in {"HACK", "SECURITY", "LIQUIDATION"} else 0),
                ),
                2,
            ),
            "url": cls._url(node),
        }

    def _fetch(self, url: str, *, now: datetime) -> List[Dict[str, Any]]:
        response = requests.get(
            url,
            timeout=self.timeout,
            headers={"User-Agent": "BTC-Demo-Intelligence/3.0"},
        )
        response.raise_for_status()
        root = ET.fromstring(response.content)
        nodes = [
            node
            for node in root.iter()
            if node.tag.rsplit("}", 1)[-1].lower() in ("item", "entry")
        ]
        return [
            item
            for node in nodes
            if (item := self._normalize(url, node, now=now)) is not None
        ]

    @staticmethod
    def _empty(sources: List[Dict[str, str]]) -> Dict[str, Any]:
        return {
            "status": "UNAVAILABLE",
            "news_risk": "UNAVAILABLE",
            "news_risk_score": None,
            "trade_risk": "UNKNOWN",
            "sentiment": "UNAVAILABLE",
            "sentiment_score": None,
            "important_events": [],
            "top_risks": [],
            "top_catalysts": [],
            "event_clusters": [],
            "items": [],
            "sources": sources,
            "observed_at": int(time.time() * 1000),
        }

    def evaluate(self, force: bool = False) -> Dict[str, Any]:
        if not self.enabled or not self.urls:
            return self._empty([])
        if (
            not force
            and self._cache
            and time.monotonic() - self._cached_at < self.cache_seconds
        ):
            return self._cache

        now = datetime.now(timezone.utc)
        items: List[Dict[str, Any]] = []
        sources: List[Dict[str, str]] = []
        for url in self.urls:
            try:
                fetched = self._fetch(url, now=now)
                items.extend(fetched)
                sources.append(
                    {"source": urlparse(url).netloc, "status": "AVAILABLE"}
                )
            except (requests.RequestException, ET.ParseError, ValueError, TypeError):
                sources.append(
                    {"source": urlparse(url).netloc, "status": "UNAVAILABLE"}
                )

        # Deduplicate exact and near-identical syndicated headlines across feeds.
        deduped: Dict[str, Dict[str, Any]] = {}
        for item in items:
            key = item.get("title_key") or self._title_key(str(item.get("title")))
            matched_key = next(
                (
                    candidate
                    for candidate, current_item in deduped.items()
                    if self._is_duplicate(str(item.get("title") or ""), str(current_item.get("title") or ""))
                ),
                key,
            )
            current = deduped.get(matched_key)
            if current is None or float(item.get("impact_score") or 0) > float(
                current.get("impact_score") or 0
            ):
                deduped[matched_key] = item
        items = list(deduped.values())
        items.sort(
            key=lambda row: (
                float(row.get("impact_score") or 0),
                -(float(row.get("age_hours")) if row.get("age_hours") is not None else 999),
            ),
            reverse=True,
        )

        if not items:
            result = self._empty(sources)
            self._cache, self._cached_at = result, time.monotonic()
            return result

        recent = [
            row
            for row in items
            if row.get("age_hours") is None or float(row.get("age_hours")) <= 24
        ]
        weighted_direction = sum(
            float(row.get("sentiment_score") or 0) for row in recent
        )
        weight_total = sum(
            max(0.1, float(row.get("impact_score") or 0) / 100.0)
            for row in recent
        )
        normalized_direction = (
            weighted_direction / weight_total if weight_total > 0 else 0.0
        )
        sentiment = (
            "BULLISH"
            if normalized_direction >= 10
            else "BEARISH"
            if normalized_direction <= -10
            else "NEUTRAL"
        )

        top_risk_score = max(float(row.get("risk_score") or 0) for row in recent or items)
        fresh_severe = [
            row
            for row in recent
            if float(row.get("risk_score") or 0) >= 85
            and row.get("age_hours") is not None
            and float(row["age_hours"]) <= 2
            and row.get("btc_relevance") == "HIGH"
            and row.get("category") in {
                "FED", "FOMC", "CPI", "SEC", "REGULATION", "HACK",
                "SECURITY", "LIQUIDATION", "STABLECOIN", "BANKING",
            }
        ]
        news_risk = (
            "EXTREME"
            if top_risk_score >= 85 and fresh_severe
            else "HIGH"
            if top_risk_score >= 65
            else "MEDIUM"
            if top_risk_score >= 40
            else "LOW"
        )
        trade_risk = (
            "BLOCK"
            if news_risk == "EXTREME"
            else "CAUTION"
            if news_risk in {"HIGH", "MEDIUM"}
            else "CLEAR"
        )

        clusters: Dict[str, Dict[str, Any]] = {}
        for row in recent:
            category = str(row.get("category") or "OTHER")
            cluster = clusters.setdefault(
                category,
                {
                    "category": category,
                    "count": 0,
                    "max_impact_score": 0.0,
                    "direction_score": 0.0,
                    "freshest_age_hours": None,
                    "max_risk_score": 0.0,
                },
            )
            cluster["count"] += 1
            cluster["max_impact_score"] = max(
                cluster["max_impact_score"],
                float(row.get("impact_score") or 0),
            )
            cluster["direction_score"] += float(row.get("sentiment_score") or 0)
            cluster["max_risk_score"] = max(
                cluster["max_risk_score"], float(row.get("risk_score") or 0)
            )
            age = row.get("age_hours")
            if age is not None:
                cluster["freshest_age_hours"] = min(
                    float(age),
                    float(cluster["freshest_age_hours"])
                    if cluster["freshest_age_hours"] is not None
                    else float(age),
                )
        event_clusters = sorted(
            clusters.values(),
            key=lambda row: (row["max_impact_score"], row["count"]),
            reverse=True,
        )

        top_risks = sorted(
            recent,
            key=lambda row: float(row.get("risk_score") or 0),
            reverse=True,
        )[:6]
        top_catalysts = sorted(
            recent,
            key=lambda row: abs(float(row.get("sentiment_score") or 0))
            * max(0.1, float(row.get("impact_score") or 0)),
            reverse=True,
        )[:6]
        important = [
            row
            for row in recent
            if row.get("importance") == "HIGH"
            or float(row.get("impact_score") or 0) >= 45
        ][:10]

        result = {
            "status": "AVAILABLE"
            if all(source["status"] == "AVAILABLE" for source in sources)
            else "DEGRADED",
            "news_risk": news_risk,
            "news_risk_score": round(top_risk_score, 2),
            "trade_risk": trade_risk,
            "sentiment": sentiment,
            "sentiment_score": round(normalized_direction, 2),
            "important_events": important,
            "top_risks": top_risks,
            "top_catalysts": top_catalysts,
            "event_clusters": event_clusters[:8],
            "items": items[:40],
            "sources": sources,
            "observed_at": int(time.time() * 1000),
        }
        self._cache, self._cached_at = result, time.monotonic()
        return result


NewsEngineV3 = NewsEngineV4
NewsEngineV2 = NewsEngineV4
NewsEngine = NewsEngineV4
