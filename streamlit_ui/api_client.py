"""Bounded, read-only HTTP access to the existing Render dashboard API."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Dict, Optional
from urllib.parse import urljoin, urlparse

import requests

from streamlit_ui import DEFAULT_BACKEND_URL


READ_ONLY_ENDPOINTS = frozenset({"/api/bootstrap", "/api/health", "/api/snapshot", "/api/account"})


@dataclass(frozen=True)
class APIResult:
    status: str
    data: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    http_status: Optional[int] = None

    @property
    def available(self) -> bool:
        return self.status == "AVAILABLE" and self.data is not None


class BackendAPIClient:
    """GET-only client; it has no generic method capable of reaching write routes."""

    def __init__(self, base_url: str = DEFAULT_BACKEND_URL, *, session=None,
                 connect_timeout: float = 4.0, read_timeout: float = 20.0,
                 max_attempts: int = 2):
        parsed = urlparse((base_url or DEFAULT_BACKEND_URL).strip())
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("STREAMLIT_BACKEND_URL must be an absolute HTTP(S) URL")
        self.base_url = f"{parsed.scheme}://{parsed.netloc}{parsed.path.rstrip('/')}"
        self.session = session or requests.Session()
        self.connect_timeout = connect_timeout
        self.read_timeout = read_timeout
        self.max_attempts = max(1, min(int(max_attempts), 2))

    def _get(self, endpoint: str) -> APIResult:
        if endpoint not in READ_ONLY_ENDPOINTS:
            return APIResult("UNAVAILABLE", error="READ_ONLY_ENDPOINT_BLOCKED")
        url = urljoin(f"{self.base_url}/", endpoint.lstrip("/"))
        for attempt in range(self.max_attempts):
            try:
                response = self.session.get(
                    url,
                    headers={"Accept": "application/json", "User-Agent": "btc-streamlit-observer/1"},
                    timeout=(self.connect_timeout, self.read_timeout),
                )
                if response.status_code >= 500:
                    if attempt + 1 < self.max_attempts:
                        time.sleep(0.15)
                        continue
                    return APIResult("WAKING", error="BACKEND_UNAVAILABLE", http_status=response.status_code)
                if response.status_code != 200:
                    return APIResult("UNAVAILABLE", error="HTTP_ERROR", http_status=response.status_code)
                try:
                    payload = response.json()
                except (ValueError, TypeError):
                    return APIResult("UNAVAILABLE", error="INVALID_JSON", http_status=response.status_code)
                if not isinstance(payload, dict):
                    return APIResult("UNAVAILABLE", error="INVALID_JSON", http_status=response.status_code)
                return APIResult("AVAILABLE", data=payload, http_status=response.status_code)
            except requests.Timeout:
                if attempt + 1 == self.max_attempts:
                    return APIResult("WAKING", error="BACKEND_TIMEOUT")
            except requests.RequestException:
                if attempt + 1 == self.max_attempts:
                    return APIResult("UNAVAILABLE", error="BACKEND_UNAVAILABLE")
        return APIResult("UNAVAILABLE", error="BACKEND_UNAVAILABLE")

    def bootstrap(self) -> APIResult:
        return self._get("/api/bootstrap")

    def health(self) -> APIResult:
        return self._get("/api/health")

    def snapshot(self) -> APIResult:
        return self._get("/api/snapshot")

    def account(self) -> APIResult:
        return self._get("/api/account")

    def fetch_dashboard(self) -> Dict[str, APIResult]:
        """Fetch bootstrap first so cold-start state remains visible if snapshot fails."""
        bootstrap = self.bootstrap()
        snapshot = self.snapshot()
        health = self.health() if snapshot.available else APIResult(snapshot.status, error=snapshot.error)
        account = self.account() if snapshot.available else APIResult(snapshot.status, error=snapshot.error)
        return {"bootstrap": bootstrap, "snapshot": snapshot, "health": health, "account": account}
