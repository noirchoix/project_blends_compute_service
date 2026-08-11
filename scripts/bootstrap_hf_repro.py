from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

from hf_repro_common import (
    BUNDLE_DIRNAME,
    LOCKED_RUN_ID,
    LOCKED_SERVICE_VERSION,
    git_commit_if_available,
    json_load,
    json_write,
    path_manifest_for_bundle,
    verify_file_manifest,
    verify_manifest_digest,
)


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Download and verify a pinned Project Blends Hugging Face artifact snapshot, then write path_manifest.local.json."
    )
    source = p.add_mutually_exclusive_group(required=True)
    source.add_argument("--repo-id", help="Hugging Face dataset repo, e.g. username/project-blends-v017-artifacts")
    source.add_argument("--source-dir", help="Local bundle directory for offline/clean-room testing.")
    p.add_argument(
        "--revision",
        help="Required with --repo-id. Use an immutable HF commit SHA or release tag, not an unpinned main branch.",
    )
    p.add_argument("--local-dir", default=BUNDLE_DIRNAME)
    p.add_argument("--path-manifest", default="config/path_manifest.local.json")
    p.add_argument("--force", action="store_true", help="Replace an existing local bundle/path manifest.")
    p.add_argument(
        "--allow-code-mismatch",
        action="store_true",
        help="Allow the current Git checkout to differ from the Project Blends commit recorded in the artifact bundle.",
    )
    return p


def _download(repo_id: str, revision: str, local_dir: Path) -> None:
    try:
        from huggingface_hub import snapshot_download
    except ImportError as exc:
        raise SystemExit(
            'huggingface_hub is required. Install with: pip install -e ".[repro]"  (or: pip install huggingface_hub)'
        ) from exc
    snapshot_download(
        repo_id=repo_id,
        repo_type="dataset",
        revision=revision,
        local_dir=str(local_dir),
    )


def bootstrap(
    project_root: Path,
    local_dir: Path,
    path_manifest: Path,
    *,
    repo_id: str | None = None,
    revision: str | None = None,
    source_dir: Path | None = None,
    force: bool = False,
    allow_code_mismatch: bool = False,
) -> dict:
    project_root = project_root.resolve()
    local_dir = local_dir if local_dir.is_absolute() else (project_root / local_dir)
    path_manifest = path_manifest if path_manifest.is_absolute() else (project_root / path_manifest)

    if source_dir is not None:
        source_dir = source_dir.resolve()
        if local_dir.exists():
            if not force:
                raise FileExistsError(f"Local bundle already exists: {local_dir}. Use --force to replace it.")
            shutil.rmtree(local_dir)
        shutil.copytree(source_dir, local_dir)
    else:
        if not repo_id:
            raise ValueError("repo_id is required when source_dir is not provided")
        if not revision:
            raise ValueError("--revision is mandatory for reproducible Hugging Face downloads")
        if local_dir.exists() and force:
            shutil.rmtree(local_dir)
        _download(repo_id, revision, local_dir)

    manifest_path = local_dir / "REPRODUCIBILITY_MANIFEST.json"
    if not manifest_path.exists():
        raise FileNotFoundError(f"Downloaded bundle has no REPRODUCIBILITY_MANIFEST.json: {local_dir}")
    digest_verification = verify_manifest_digest(local_dir)
    if not digest_verification["pass"]:
        raise RuntimeError("Artifact manifest digest verification failed:\n" + json.dumps(digest_verification, indent=2))
    manifest = json_load(manifest_path)
    if manifest.get("service_version") != LOCKED_SERVICE_VERSION:
        raise ValueError(
            f"Artifact bundle service version mismatch: expected {LOCKED_SERVICE_VERSION}, got {manifest.get('service_version')}"
        )
    if manifest.get("locked_run_id") != LOCKED_RUN_ID:
        raise ValueError(f"Artifact bundle run mismatch: expected {LOCKED_RUN_ID}, got {manifest.get('locked_run_id')}")

    expected_commit = (manifest.get("source_provenance") or {}).get("project_blends_code_commit")
    actual_commit = git_commit_if_available(project_root)
    if expected_commit and actual_commit and expected_commit != actual_commit and not allow_code_mismatch:
        raise RuntimeError(
            "Project Blends Git commit mismatch: "
            f"bundle expects {expected_commit}, current checkout is {actual_commit}. "
            "Checkout the recorded commit/tag or use --allow-code-mismatch only for deliberate compatibility testing."
        )

    rows = manifest.get("files")
    if not isinstance(rows, list):
        raise ValueError("REPRODUCIBILITY_MANIFEST.json has no files list")
    verification = verify_file_manifest(local_dir, rows)
    if not verification["pass"]:
        raise RuntimeError("Artifact verification failed:\n" + json.dumps(verification, indent=2))

    if path_manifest.exists() and not force:
        raise FileExistsError(f"Path manifest already exists: {path_manifest}. Use --force to replace it.")
    payload = path_manifest_for_bundle(project_root, local_dir)
    json_write(path_manifest, payload)

    return {
        "ok": True,
        "artifact_dir": str(local_dir),
        "path_manifest": str(path_manifest),
        "verified_files": verification["checked"],
        "manifest_digest_verified": True,
        "expected_project_commit": expected_commit,
        "current_project_commit": actual_commit,
        "repo_id": repo_id,
        "revision": revision,
        "run_command": f"project-blends-run --path-manifest {path_manifest.relative_to(project_root).as_posix()} run --dataset-id project_blends_reported_v1",
        "validate_command": "project-blends-run --path-manifest config/path_manifest.local.json validate <NEW_RUN_ID>",
    }


def main() -> None:
    args = _parser().parse_args()
    if args.repo_id and not args.revision:
        raise SystemExit("--revision is required with --repo-id; pin a Hugging Face tag or commit SHA")
    project_root = Path(__file__).resolve().parents[1]
    result = bootstrap(
        project_root,
        Path(args.local_dir),
        Path(args.path_manifest),
        repo_id=args.repo_id,
        revision=args.revision,
        source_dir=Path(args.source_dir) if args.source_dir else None,
        force=args.force,
        allow_code_mismatch=args.allow_code_mismatch,
    )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
