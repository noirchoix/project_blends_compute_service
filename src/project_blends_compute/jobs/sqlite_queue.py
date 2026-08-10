from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

from project_blends_compute.utils import utc_now_iso


class SQLiteJobQueue:
    def __init__(self, database: Path, *, table: str = "jobs") -> None:
        if not table.replace("_", "").isalnum():
            raise ValueError("Unsafe table name")
        self.database = database
        self.table = table
        self.database.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.database, timeout=60)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _init_schema(self) -> None:
        with self.connection() as conn:
            conn.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {self.table} (
                    job_id TEXT PRIMARY KEY,
                    job_type TEXT NOT NULL,
                    status TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    result_json TEXT,
                    error_json TEXT,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    max_attempts INTEGER NOT NULL DEFAULT 3,
                    created_at_utc TEXT NOT NULL,
                    updated_at_utc TEXT NOT NULL,
                    started_at_utc TEXT,
                    completed_at_utc TEXT,
                    worker_id TEXT,
                    artifact_paths_json TEXT NOT NULL DEFAULT '[]'
                )
                """
            )
            conn.execute(f"CREATE INDEX IF NOT EXISTS idx_{self.table}_status ON {self.table}(status, created_at_utc)")

    def submit(self, job_type: str, payload: dict[str, Any], *, max_attempts: int = 3, job_id: str | None = None) -> str:
        resolved = job_id or f"job-{uuid.uuid4().hex}"
        now = utc_now_iso()
        with self.connection() as conn:
            conn.execute(
                f"""
                INSERT INTO {self.table}(job_id, job_type, status, payload_json, attempts, max_attempts, created_at_utc, updated_at_utc)
                VALUES (?, ?, 'pending', ?, 0, ?, ?, ?)
                """,
                (resolved, job_type, json.dumps(payload, ensure_ascii=False, sort_keys=True), max_attempts, now, now),
            )
        return resolved

    def get(self, job_id: str) -> dict[str, Any] | None:
        with self.connection() as conn:
            row = conn.execute(f"SELECT * FROM {self.table} WHERE job_id=?", (job_id,)).fetchone()
        return self._row(row) if row else None

    def recover_stale(self, stale_after_minutes: int) -> int:
        cutoff = (datetime.now(timezone.utc) - timedelta(minutes=stale_after_minutes)).isoformat()
        now = utc_now_iso()
        with self.connection() as conn:
            cursor = conn.execute(
                f"""
                UPDATE {self.table}
                SET status='pending', worker_id=NULL, started_at_utc=NULL,
                    error_json=?, updated_at_utc=?
                WHERE status='running' AND started_at_utc < ? AND attempts < max_attempts
                """,
                (json.dumps({"code": "stale_job_recovered"}), now, cutoff),
            )
            return int(cursor.rowcount)

    def claim(self, worker_id: str, *, job_type: str | None = None) -> dict[str, Any] | None:
        conn = sqlite3.connect(self.database, timeout=60, isolation_level=None)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("BEGIN IMMEDIATE")
            query = f"SELECT * FROM {self.table} WHERE status='pending' AND attempts < max_attempts"
            params: list[Any] = []
            if job_type:
                query += " AND job_type=?"
                params.append(job_type)
            query += " ORDER BY created_at_utc LIMIT 1"
            row = conn.execute(query, params).fetchone()
            if row is None:
                conn.execute("COMMIT")
                return None
            now = utc_now_iso()
            conn.execute(
                f"UPDATE {self.table} SET status='running', attempts=attempts+1, worker_id=?, started_at_utc=?, updated_at_utc=? WHERE job_id=?",
                (worker_id, now, now, row["job_id"]),
            )
            conn.execute("COMMIT")
            claimed = dict(row)
            claimed["status"] = "running"
            claimed["attempts"] = int(row["attempts"]) + 1
            claimed["worker_id"] = worker_id
            claimed["started_at_utc"] = now
            return self._row(claimed)
        except Exception:
            conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()

    def complete(self, job_id: str, result: dict[str, Any], artifact_paths: list[str] | None = None) -> None:
        now = utc_now_iso()
        with self.connection() as conn:
            conn.execute(
                f"""
                UPDATE {self.table}
                SET status='succeeded', result_json=?, error_json=NULL,
                    artifact_paths_json=?, completed_at_utc=?, updated_at_utc=?
                WHERE job_id=?
                """,
                (
                    json.dumps(result, ensure_ascii=False, sort_keys=True),
                    json.dumps(artifact_paths or [], ensure_ascii=False),
                    now,
                    now,
                    job_id,
                ),
            )

    def fail(self, job_id: str, error: dict[str, Any], *, retryable: bool = True) -> None:
        now = utc_now_iso()
        with self.connection() as conn:
            row = conn.execute(f"SELECT attempts, max_attempts FROM {self.table} WHERE job_id=?", (job_id,)).fetchone()
            status = "pending" if retryable and row and int(row["attempts"]) < int(row["max_attempts"]) else "failed"
            completed = None if status == "pending" else now
            conn.execute(
                f"""
                UPDATE {self.table}
                SET status=?, error_json=?, worker_id=NULL,
                    completed_at_utc=?, updated_at_utc=?
                WHERE job_id=?
                """,
                (status, json.dumps(error, ensure_ascii=False, sort_keys=True), completed, now, job_id),
            )

    def list(self, *, status: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        query = f"SELECT * FROM {self.table}"
        params: list[Any] = []
        if status:
            query += " WHERE status=?"
            params.append(status)
        query += " ORDER BY created_at_utc DESC LIMIT ?"
        params.append(limit)
        with self.connection() as conn:
            rows = conn.execute(query, params).fetchall()
        return [self._row(row) for row in rows]

    @staticmethod
    def _row(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
        payload = dict(row)
        for source, target in (
            ("payload_json", "payload"),
            ("result_json", "result"),
            ("error_json", "error"),
            ("artifact_paths_json", "artifact_paths"),
        ):
            raw = payload.pop(source, None)
            if raw:
                try:
                    payload[target] = json.loads(raw)
                except Exception:
                    payload[target] = raw
            else:
                payload[target] = [] if target == "artifact_paths" else None
        return payload
