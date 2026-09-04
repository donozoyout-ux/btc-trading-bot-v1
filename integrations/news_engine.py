"""Small RSS-based crypto news engine with transparent keyword risk scoring."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Dict, Iterable, List, Optional
from xml.etree import ElementTree

import requests
from loguru import logger


class NewsEngine:
    HIGH_RISK_TERMS = {
        "hack",
        "hacked",
        "exploit",
        "breach",
        "bankruptcy",
        "insolvency",
        "outage",
        "liquidation",
        "liquidations",
        "sec",
        "lawsuit",
        "ban",
        "emergency",
        "war",
        "cpi",
        "fomc",
        "fed",
        "rate decision",
    }
    BULLISH_TERMS = {
        "approval",
        "approved",
        "inflow",
        "record high",
        "adoption",
        "launch",
        "rally",
        "surge",
        "buying",
    }
    BEARISH_TERMS = {
        "hack",
        "exploit",
        "outflow",
        "selloff",
        "liquidation",
        "lawsuit",
        "ban",
        "fraud",
        "collapse",
        "plunge",
    }

    def __init__(
        self,
        urls: Iterable[str],
        enabled: bool = True,
        timeout: int = 8,
        max_items: int = 20,
        lookback_hours: int = 24,
    ):
        self.urls = [u.strip() for u in urls if u and u.strip()]
        self.enabled = bool(enabled)
        self.timeout = timeout
        self.max_items = max(1, int(max_items))
        self.lookback_hours = max(1, int(lookback_hours))

    @staticmethod
    def _text(node: Optional[ElementTree.Element], tag: str) -> str:
        if node is None:
            return ""
        child = node.find(tag)
        return "" if child is None or child.text is None else child.text.strip()

    @staticmethod
    def _parse_time(value: str) -> Optional[datetime]:
        if not value:
            return None
        try:
            dt = parsedate_to_datetime(value)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except Exception:
            pass
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except Exception:
            return None

    def _parse_feed(self, xml_text: str, source_url: str) -> List[Dict[str, Any]]:
        root = ElementTree.fromstring(xml_text)
        items: List[Dict[str, Any]] = []

        # RSS 2.0
        for item in root.findall(".//item"):
            title = self._text(item, "title")
            link = self._text(item, "link")
            published_raw = self._text(item, "pubDate") or self._text(item, "date")
            items.append(
                {
                    "title": title,
                    "url": link,
                    "published_at": self._parse_time(published_raw),
                    "source_url": source_url,
                }
            )

        if items:
            return items

        # Atom fallback
        ns = {"a": "http://www.w3.org/2005/Atom"}
        for entry in root.findall(".//a:entry", ns):
            title = self._text(entry, "{http://www.w3.org/2005/Atom}title")
            link_node = entry.find("{http://www.w3.org/2005/Atom}link")
            link = link_node.attrib.get("href", "") if link_node is not None else ""
            published_raw = self._text(entry, "{http://www.w3.org/2005/Atom}published") or self._text(
                entry, "{http://www.w3.org/2005/Atom}updated"
            )
            items.append(
                {
                    "title": title,
                    "url": link,
                    "published_at": self._parse_time(published_raw),
                    "source_url": source_url,
                }
            )
        return items

    @classmethod
    def _score_item(cls, item: Dict[str, Any]) -> Dict[str, Any]:
        title = str(item.get("title") or "").lower()
        high_hits = sorted(term for term in cls.HIGH_RISK_TERMS if term in title)
        bullish_hits = sorted(term for term in cls.BULLISH_TERMS if term in title)
        bearish_hits = sorted(term for term in cls.BEARISH_TERMS if term in title)

        risk_score = min(100, len(high_hits) * 35 + len(bearish_hits) * 10)
        sentiment_score = len(bullish_hits) - len(bearish_hits)
        sentiment = "BULLISH" if sentiment_score > 0 else "BEARISH" if sentiment_score < 0 else "NEUTRAL"
        enriched = dict(item)
        enriched.update(
            {
                "risk_score": risk_score,
                "risk_terms": high_hits,
                "sentiment": sentiment,
                "sentiment_score": sentiment_score,
            }
        )
        return enriched

    def snapshot(self) -> Dict[str, Any]:
        if not self.enabled:
            return {
                "status": "DISABLED",
                "risk": "UNAVAILABLE",
                "sentiment": "UNAVAILABLE",
                "items": [],
                "errors": [],
            }
        if not self.urls:
            return {
                "status": "UNAVAILABLE",
                "risk": "UNAVAILABLE",
                "sentiment": "UNAVAILABLE",
                "items": [],
                "errors": ["No RSS sources configured"],
            }

        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(hours=self.lookback_hours)
        collected: List[Dict[str, Any]] = []
        errors: List[str] = []
        headers = {"User-Agent": "btc-trading-bot-news/1.0"}

        for url in self.urls:
            try:
                resp = requests.get(url, headers=headers, timeout=self.timeout)
                resp.raise_for_status()
                for item in self._parse_feed(resp.text, url):
                    published = item.get("published_at")
                    if published is not None and published < cutoff:
                        continue
                    collected.append(self._score_item(item))
            except Exception as exc:
                logger.warning(f"News source unavailable ({type(exc).__name__})")
                errors.append(f"{url}: {type(exc).__name__}")

        # De-duplicate by URL or title and keep the newest first when possible.
        dedup: Dict[str, Dict[str, Any]] = {}
        for item in collected:
            key = str(item.get("url") or item.get("title") or "").strip()
            if key and key not in dedup:
                dedup[key] = item
        items = list(dedup.values())
        items.sort(
            key=lambda x: x.get("published_at") or datetime.min.replace(tzinfo=timezone.utc),
            reverse=True,
        )
        items = items[: self.max_items]

        max_risk = max((int(i.get("risk_score", 0)) for i in items), default=0)
        if max_risk >= 70:
            risk = "EXTREME"
        elif max_risk >= 35:
            risk = "HIGH"
        elif items:
            risk = "LOW"
        else:
            risk = "UNAVAILABLE"

        sentiment_total = sum(int(i.get("sentiment_score", 0)) for i in items)
        sentiment = "BULLISH" if sentiment_total > 1 else "BEARISH" if sentiment_total < -1 else "NEUTRAL"
        serialized_items = []
        for item in items:
            row = dict(item)
            dt = row.get("published_at")
            row["published_at"] = dt.isoformat() if isinstance(dt, datetime) else None
            serialized_items.append(row)

        return {
            "status": "HEALTHY" if items else ("DEGRADED" if errors else "UNAVAILABLE"),
            "risk": risk,
            "sentiment": sentiment,
            "item_count": len(serialized_items),
            "items": serialized_items,
            "errors": errors,
            "method": "RSS + transparent keyword heuristics",
        }
