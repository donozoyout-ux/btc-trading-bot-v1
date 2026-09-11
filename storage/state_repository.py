"""Optional durable key/value state storage with credential filtering."""

from __future__ import annotations

import json
import os
import tempfile
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Dict, Optional


_SECRET_MARKERS = ("secret", "token", "api_key", "apikey", "password", "credential")


def sanitize_state(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): sanitize_state(item)
            for key, item in value.items()
            if not any(marker in str(key).lower() for marker in _SECRET_MARKERS)
        }
    if isinstance(value, list):
        return [sanitize_state(item) for item in value]
    if isinstance(value, tuple):
        return [sanitize_state(item) for item in value]
    return value


class StateRepository(ABC):
    durability = "LOCAL_EPHEMERAL"

    @abstractmethod
    def load(self, key: str) -> Dict[str, Any]: ...

    @abstractmethod
    def save(self, key: str, value: Dict[str, Any]) -> None: ...


class LocalStateRepository(StateRepository):
    durability = "LOCAL_EPHEMERAL"

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def load(self, key: str) -> Dict[str, Any]:
        if not self.path.exists():
            return {}
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                return {}
            if any(name in payload for name in ("execution_state", "daily_profit_target")):
                return dict(payload.get(key) or {})
            # Backwards compatibility with the former unwrapped journal file.
            return dict(payload)
        except (OSError, ValueError, TypeError):
            return {}

    def save(self, key: str, value: Dict[str, Any]) -> None:
        payload: Dict[str, Any] = {}
        if self.path.exists():
            try:
                existing = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(existing, dict):
                    payload = existing if any(name in existing for name in ("execution_state", "daily_profit_target")) else {"execution_state": existing}
            except (OSError, ValueError, TypeError):
                payload = {}
        payload[key] = sanitize_state(value)
        fd, temporary = tempfile.mkstemp(prefix="trade-state-", suffix=".json", dir=str(self.path.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2)
            os.replace(temporary, self.path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)


class PostgresStateRepository(StateRepository):
    durability = "PERSISTENT"

    def __init__(self, database_url: str):
        self.database_url = database_url
        try:
            import psycopg  # type: ignore
            self._driver = psycopg
        except ImportError:
            import psycopg2  # type: ignore
            self._driver = psycopg2
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("CREATE TABLE IF NOT EXISTS trade_state (state_key TEXT PRIMARY KEY, value_json TEXT NOT NULL, updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW())")
            conn.commit()

    def _connect(self):
        return self._driver.connect(self.database_url)

    def load(self, key: str) -> Dict[str, Any]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT value_json FROM trade_state WHERE state_key = %s", (key,))
                row = cur.fetchone()
        return json.loads(row[0]) if row else {}

    def save(self, key: str, value: Dict[str, Any]) -> None:
        encoded = json.dumps(sanitize_state(value), ensure_ascii=False, separators=(",", ":"))
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO trade_state(state_key,value_json) VALUES(%s,%s) ON CONFLICT(state_key) DO UPDATE SET value_json=EXCLUDED.value_json, updated_at=NOW()",
                    (key, encoded),
                )
            conn.commit()


def create_state_repository(local_path: str | Path, database_url: Optional[str] = None) -> StateRepository:
    url = database_url or os.getenv("DATABASE_URL")
    if url:
        try:
            return PostgresStateRepository(url)
        except Exception:
            # Persistence is optional and must never prevent application start.
            pass
    return LocalStateRepository(local_path)
