#!/usr/bin/env python
from __future__ import annotations

import argparse
from pathlib import Path

OLD = 'DatasetKind = Literal["orderly", "uspto_llm", "transformer_csv", "atommap_csv", "multistep_csv"]'
NEW = '''DatasetKind = Literal[
    "orderly",
    "uspto_llm",
    "transformer_csv",
    "atommap_csv",
    "multistep_csv",
    "storage_reaction_evidence",
]'''


def main() -> None:
    parser = argparse.ArgumentParser(description="Add the storage_reaction_evidence dataset kind to reaction_curation")
    parser.add_argument("reaction_curation_root", type=Path)
    args = parser.parse_args()
    target = args.reaction_curation_root / "reaction_curation_core" / "reaction_curation" / "schemas.py"
    if not target.exists():
        raise SystemExit(f"schemas.py not found: {target}")
    text = target.read_text(encoding="utf-8")
    if '"storage_reaction_evidence"' in text:
        print(f"Already patched: {target}")
        return
    if OLD not in text:
        raise SystemExit("Expected DatasetKind declaration not found; inspect the upstream version before applying")
    target.write_text(text.replace(OLD, NEW), encoding="utf-8")
    print(f"Patched: {target}")


if __name__ == "__main__":
    main()
