from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def stable_hash(value: str, *, algorithm: str = "sha256") -> str:
    h = hashlib.new(algorithm)
    h.update(value.encode("utf-8"))
    return h.hexdigest()


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(chunk_size), b""):
            h.update(chunk)
    return h.hexdigest()


def jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(k): jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [jsonable(v) for v in value]
    if hasattr(value, "model_dump"):
        return jsonable(value.model_dump(mode="json"))
    if hasattr(value, "to_dict"):
        return jsonable(value.to_dict())
    if hasattr(value, "tolist"):
        try:
            return jsonable(value.tolist())
        except Exception:
            pass
    if hasattr(value, "item"):
        try:
            return value.item()
        except Exception:
            pass
    return repr(value)


def canonical_json_bytes(payload: Any) -> bytes:
    return json.dumps(jsonable(payload), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def write_json_atomic(path: Path, payload: Any) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as tmp:
        json.dump(jsonable(payload), tmp, ensure_ascii=False, indent=2, sort_keys=True)
        tmp.write("\n")
        temp_name = tmp.name
    os.replace(temp_name, path)
    return path


def write_text_atomic(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as tmp:
        tmp.write(text)
        temp_name = tmp.name
    os.replace(temp_name, path)
    return path


def normalize_name(value: str) -> str:
    text = str(value or "").strip().lower()
    text = text.replace("α", "alpha").replace("β", "beta").replace("γ", "gamma")
    text = text.replace(".alpha.", "alpha").replace(".beta.", "beta").replace(".gamma.", "gamma")
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"\s*([,;()\[\]])\s*", r"\1", text)
    return text.strip(" ,;-")


def dedupe_keep_order(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            out.append(value)
    return out
