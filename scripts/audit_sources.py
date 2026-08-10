#!/usr/bin/env python
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description="Verify uploaded source snapshots by SHA-256")
    parser.add_argument("--source-dir", type=Path, default=Path("/mnt/data"))
    parser.add_argument("--manifest", type=Path, default=Path("config/uploaded_source_manifest.json"))
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    rows = []
    for source in manifest["sources"]:
        path = args.source_dir / source["filename"]
        actual = digest(path) if path.exists() else None
        rows.append({**source, "path": str(path), "exists": path.exists(), "actual_sha256": actual, "match": actual == source["sha256"]})
    payload = {"ok": all(row["match"] for row in rows), "sources": rows}
    print(json.dumps(payload, indent=2))
    raise SystemExit(0 if payload["ok"] else 1)


if __name__ == "__main__":
    main()
