from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

from project_blends_compute.utils import utc_now_iso, write_json_atomic


class RunRegistry:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    def _load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"schema_version": "project_blends_run_registry.v1", "runs": {}}
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            return {"schema_version": "project_blends_run_registry.v1", "runs": {}}
        payload.setdefault("runs", {})
        return payload

    def upsert(self, run_id: str, record: dict[str, Any]) -> None:
        with self._lock:
            payload = self._load()
            payload["runs"][run_id] = {**payload["runs"].get(run_id, {}), **record, "updated_at_utc": utc_now_iso()}
            write_json_atomic(self.path, payload)

    def get(self, run_id: str) -> dict[str, Any] | None:
        with self._lock:
            return self._load()["runs"].get(run_id)

    def list(self) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(run_id=run_id, **record) for run_id, record in self._load()["runs"].items()]
