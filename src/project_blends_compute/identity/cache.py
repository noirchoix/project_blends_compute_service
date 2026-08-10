from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from project_blends_compute.utils import utc_now_iso


class IdentityCache:
    def __init__(self, database: Path) -> None:
        self.database = database
        self.database.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.database)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS identity_cache (
                    cache_key TEXT PRIMARY KEY,
                    payload_json TEXT NOT NULL,
                    created_at_utc TEXT NOT NULL,
                    updated_at_utc TEXT NOT NULL
                )
                """
            )

    def get(self, cache_key: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute("SELECT payload_json FROM identity_cache WHERE cache_key=?", (cache_key,)).fetchone()
        return json.loads(row["payload_json"]) if row else None

    def set(self, cache_key: str, payload: dict[str, Any]) -> None:
        now = utc_now_iso()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO identity_cache(cache_key, payload_json, created_at_utc, updated_at_utc)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(cache_key) DO UPDATE SET payload_json=excluded.payload_json, updated_at_utc=excluded.updated_at_utc
                """,
                (cache_key, json.dumps(payload, ensure_ascii=False, sort_keys=True), now, now),
            )
