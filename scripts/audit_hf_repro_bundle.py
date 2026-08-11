from __future__ import annotations

import argparse
import json
from pathlib import Path

from hf_repro_common import BUNDLE_DIRNAME, audit_bundle


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Audit a Project Blends Hugging Face reproducibility bundle before upload."
    )
    p.add_argument("--bundle-dir", default=BUNDLE_DIRNAME)
    return p


def main() -> None:
    args = _parser().parse_args()
    project_root = Path(__file__).resolve().parents[1]
    bundle = Path(args.bundle_dir)
    if not bundle.is_absolute():
        bundle = project_root / bundle
    result = audit_bundle(bundle)
    print(json.dumps(result, indent=2))
    if not result["pass"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
