"""RSS news normalization used strictly as context and a risk filter."""

from __future__ import annotations

import time
import xml.etree.ElementTree as ET
from datetime import timezone
from email.utils import parsedate_to_datetime
from typing import Any, Dict, Iterable, List, Optional
from urllib.parse import urlparse

import requests


class NewsEngineV2:
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
        "MACRO": ("jobs report", "interest rate", "treasury", "recession", "macro"),
    }
    POSITIVE = ("approval", "approved", "inflow", "adoption", "surge", "rally", "record high")
    NEGATIVE = ("hack", "exploit", "breach", "ban", "lawsuit", "outflow", "crash", "liquidation")
    HIGH_IMPORTANCE = ("fed", "fomc", "cpi", "etf", "sec", "hack", "exploit", "liquidation")

    def __init__(self, urls: Iterable[str], enabled: bool = True, timeout: int = 6, cache_seconds: int = 300):
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
                return value if "T" in value else None
            except TypeError:
                return None

    @classmethod
    def _category(cls, title: str) -> str:
        haystack = f" {title.lower()} "
        for category, keywords in cls.CATEGORIES.items():
            if any(keyword in haystack for keyword in keywords):
                return category
        return "OTHER"

    @classmethod
    def _sentiment(cls, title: str) -> str:
        lowered = title.lower()
        positives = sum(word in lowered for word in cls.POSITIVE)
        negatives = sum(word in lowered for word in cls.NEGATIVE)
        if positives > negatives:
            return "BULLISH"
        if negatives > positives:
            return "BEARISH"
        return "NEUTRAL"

    @classmethod
    def _normalize(cls, source_url: str, node: ET.Element) -> Optional[Dict[str, Any]]:
        title = cls._text(node, ("title",))
        if not title:
            return None
        lowered = title.lower()
        category = cls._category(title)
        importance = "HIGH" if any(word in lowered for word in cls.HIGH_IMPORTANCE) else "MEDIUM" if category != "OTHER" else "LOW"
        return {
            "source": urlparse(source_url).netloc,
            "title": title,
            "published_at": cls._published(cls._text(node, ("pubdate", "published", "updated"))),
            "category": category,
            "sentiment": cls._sentiment(title),
            "importance": importance,
            "btc_relevance": "HIGH" if category in ("BITCOIN", "ETF", "LIQUIDATION") else "MEDIUM" if category != "OTHER" else "LOW",
            "url": cls._url(node),
        }

    def _fetch(self, url: str) -> List[Dict[str, Any]]:
        response = requests.get(url, timeout=self.timeout, headers={"User-Agent": "BTC-Demo-Intelligence/2.0"})
        response.raise_for_status()
        root = ET.fromstring(response.content)
        nodes = [node for node in root.iter() if node.tag.rsplit("}", 1)[-1].lower() in ("item", "entry")]
        return [item for node in nodes if (item := self._normalize(url, node)) is not None]

    def evaluate(self, force: bool = False) -> Dict[str, Any]:
        if not self.enabled or not self.urls:
            return {"status": "UNAVAILABLE", "news_risk": "UNAVAILABLE", "sentiment": "UNAVAILABLE", "important_events": [], "items": [], "sources": []}
        if not force and self._cache and time.monotonic() - self._cached_at < self.cache_seconds:
            return self._cache

        items: List[Dict[str, Any]] = []
        sources: List[Dict[str, str]] = []
        for url in self.urls:
            try:
                fetched = self._fetch(url)
                items.extend(fetched)
                sources.append({"source": urlparse(url).netloc, "status": "AVAILABLE"})
            except (requests.RequestException, ET.ParseError, ValueError, TypeError):
                sources.append({"source": urlparse(url).netloc, "status": "UNAVAILABLE"})
        if not items:
            result = {"status": "UNAVAILABLE", "news_risk": "UNAVAILABLE", "sentiment": "UNAVAILABLE", "important_events": [], "items": [], "sources": sources}
        else:
            high = [item for item in items if item["importance"] == "HIGH"]
            severe = [item for item in high if item["category"] in ("HACK", "SECURITY", "LIQUIDATION", "FED", "FOMC", "CPI")]
            risk = "EXTREME" if len(severe) >= 3 else "HIGH" if severe else "MEDIUM" if high else "LOW"
            bulls = sum(item["sentiment"] == "BULLISH" for item in items)
            bears = sum(item["sentiment"] == "BEARISH" for item in items)
            sentiment = "BULLISH" if bulls > bears else "BEARISH" if bears > bulls else "NEUTRAL"
            result = {
                "status": "AVAILABLE" if all(source["status"] == "AVAILABLE" for source in sources) else "DEGRADED",
                "news_risk": risk,
                "sentiment": sentiment,
                "important_events": high[:8],
                "items": items[:30],
                "sources": sources,
            }
        self._cache, self._cached_at = result, time.monotonic()
        return result
