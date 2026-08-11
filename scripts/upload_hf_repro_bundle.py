from __future__ import annotations

import argparse
import json
from pathlib import Path

from hf_repro_common import BUNDLE_DIRNAME, audit_bundle, json_load, verify_file_manifest, verify_manifest_digest


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Upload a verified Project Blends reproducibility bundle to a Hugging Face dataset repo.")
    p.add_argument("--repo-id", required=True, help="username/dataset-repo")
    p.add_argument("--bundle-dir", default=BUNDLE_DIRNAME)
    visibility = p.add_mutually_exclusive_group()
    visibility.add_argument("--private", action="store_true", help="Explicitly create a private dataset repository (this is the default).")
    visibility.add_argument("--public", action="store_true", help="Create a public dataset repository.")
    p.add_argument(
        "--acknowledge-redistribution-rights",
        action="store_true",
        help="Required for --public. Confirms that you reviewed redistribution rights for all third-party artifacts.",
    )
    return p


def main() -> None:
    args = _parser().parse_args()
    project_root = Path(__file__).resolve().parents[1]
    bundle = Path(args.bundle_dir)
    if not bundle.is_absolute():
        bundle = project_root / bundle
    digest_verification = verify_manifest_digest(bundle)
    if not digest_verification["pass"]:
        raise SystemExit("Refusing upload: REPRODUCIBILITY_MANIFEST.sha256 does not match the manifest")
    manifest = json_load(bundle / "REPRODUCIBILITY_MANIFEST.json")
    verification = verify_file_manifest(bundle, manifest.get("files") or [])
    if not verification["pass"]:
        raise SystemExit("Refusing upload: local reproducibility bundle failed SHA-256 verification")
    audit = audit_bundle(bundle)
    if not audit["pass"]:
        raise SystemExit("Refusing upload: local reproducibility bundle failed portability/license/runtime audit")
    if args.public and not args.acknowledge_redistribution_rights:
        raise SystemExit(
            "Refusing public upload until --acknowledge-redistribution-rights is supplied after reviewing REDISTRIBUTION_REVIEW.md"
        )
    try:
        from huggingface_hub import HfApi
    except ImportError as exc:
        raise SystemExit('Install Hugging Face support with: pip install -e ".[repro]"') from exc

    api = HfApi()
    api.create_repo(
        repo_id=args.repo_id,
        repo_type="dataset",
        private=not args.public,
        exist_ok=True,
    )
    # upload_large_folder is resumable and works well with model/data artifacts.
    api.upload_large_folder(
        repo_id=args.repo_id,
        repo_type="dataset",
        folder_path=str(bundle),
    )
    info = api.repo_info(args.repo_id, repo_type="dataset")
    print(
        json.dumps(
            {
                "ok": True,
                "repo_id": args.repo_id,
                "private": not args.public,
                "revision": info.sha,
                "next": f"python scripts/bootstrap_hf_repro.py --repo-id {args.repo_id} --revision {info.sha}",
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
