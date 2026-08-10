from __future__ import annotations

import json
import os
import shutil
import tempfile
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

from project_blends_compute.errors import ArtifactValidationError
from project_blends_compute.schemas.common import ArtifactRef
from project_blends_compute.utils import sha256_file, utc_now_iso, write_json_atomic, write_text_atomic


@dataclass(slots=True)
class BundleWriter:
    run_id: str
    staging_dir: Path
    final_dir: Path
    artifacts: list[ArtifactRef] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def path(self, relative: str | Path) -> Path:
        target = self.staging_dir / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        return target

    def write_json(self, relative: str | Path, payload: Any, *, logical_name: str | None = None, schema_version: str | None = None) -> Path:
        path = self.path(relative)
        write_json_atomic(path, payload)
        self.register(path, logical_name=logical_name, media_type="application/json", schema_version=schema_version)
        return path

    def write_text(self, relative: str | Path, text: str, *, logical_name: str | None = None, media_type: str = "text/plain") -> Path:
        path = self.path(relative)
        write_text_atomic(path, text)
        self.register(path, logical_name=logical_name, media_type=media_type)
        return path

    def register(
        self,
        path: Path,
        *,
        logical_name: str | None = None,
        media_type: str = "application/octet-stream",
        rows: int | None = None,
        schema_version: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> ArtifactRef:
        if not path.exists() or not path.is_file():
            raise ArtifactValidationError("Cannot register missing artifact", path=str(path))
        relative = path.relative_to(self.staging_dir)
        ref = ArtifactRef(
            logical_name=logical_name or relative.as_posix(),
            path=relative.as_posix(),
            sha256=sha256_file(path),
            media_type=media_type,
            rows=rows,
            schema_version=schema_version,
            metadata=metadata or {},
        )
        self.artifacts.append(ref)
        return ref

    def finalize(self, *, status: str, warnings: list[str] | None = None, errors: list[str] | None = None) -> Path:
        if self.final_dir.exists():
            raise ArtifactValidationError("Immutable run directory already exists", run_id=self.run_id, path=str(self.final_dir))
        manifest = {
            "schema_version": "project_blends_run_manifest.v1",
            "run_id": self.run_id,
            "generated_at_utc": utc_now_iso(),
            "status": status,
            "warnings": warnings or [],
            "errors": errors or [],
            "metadata": self.metadata,
            "artifacts": [artifact.model_dump(mode="json") for artifact in self.artifacts],
            "contract": {
                "immutable_after_finalize": True,
                "sha256_verified": True,
                "atomic_install": True,
                "artifact_paths_relative": True,
            },
        }
        manifest_path = self.staging_dir / "manifest.json"
        write_json_atomic(manifest_path, manifest)
        self.register(manifest_path, logical_name="run_manifest", media_type="application/json", schema_version="v1")
        # Refresh manifest with its own entry excluded to avoid recursive digest drift.
        manifest["artifacts"] = [artifact.model_dump(mode="json") for artifact in self.artifacts if artifact.logical_name != "run_manifest"]
        write_json_atomic(manifest_path, manifest)
        self.final_dir.parent.mkdir(parents=True, exist_ok=True)
        os.replace(self.staging_dir, self.final_dir)
        return self.final_dir / "manifest.json"


class ArtifactStore:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.runs_root = root / "runs"
        self.releases_root = root / "releases"
        self.tmp_root = root / ".staging"
        for path in (self.runs_root, self.releases_root, self.tmp_root):
            path.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def create_bundle(self, run_id: str | None = None, metadata: dict[str, Any] | None = None) -> Iterator[BundleWriter]:
        resolved_id = run_id or self.new_run_id()
        staging = Path(tempfile.mkdtemp(prefix=f"{resolved_id}-", dir=self.tmp_root))
        final = self.runs_root / resolved_id
        writer = BundleWriter(resolved_id, staging, final, metadata=metadata or {})
        try:
            yield writer
        except Exception:
            shutil.rmtree(staging, ignore_errors=True)
            raise

    @staticmethod
    def new_run_id() -> str:
        return f"pb-{utc_now_iso().replace(':', '').replace('-', '').replace('+00:00', 'Z').replace('.', '')[:15]}-{uuid.uuid4().hex[:10]}"

    def run_dir(self, run_id: str) -> Path:
        return self.runs_root / run_id

    def load_manifest(self, run_id: str) -> dict[str, Any]:
        path = self.run_dir(run_id) / "manifest.json"
        if not path.exists():
            raise FileNotFoundError(path)
        payload = json.loads(path.read_text(encoding="utf-8"))
        self.verify_manifest(run_id, payload)
        return payload

    def verify_manifest(self, run_id: str, manifest: dict[str, Any] | None = None) -> None:
        payload = manifest or json.loads((self.run_dir(run_id) / "manifest.json").read_text(encoding="utf-8"))
        failures: list[str] = []
        for artifact in payload.get("artifacts", []):
            path = self.run_dir(run_id) / artifact["path"]
            if not path.exists():
                failures.append(f"missing:{artifact['path']}")
                continue
            actual = sha256_file(path)
            if actual != artifact.get("sha256"):
                failures.append(f"sha256:{artifact['path']}")
        if failures:
            raise ArtifactValidationError("Run artifact verification failed", run_id=run_id, failures=failures)

    def lock_release(self, run_id: str, acceptance_report: dict[str, Any], release_id: str | None = None) -> Path:
        if not acceptance_report.get("strict_pass"):
            raise ArtifactValidationError("Release cannot be locked until strict acceptance passes", run_id=run_id)
        manifest = self.load_manifest(run_id)
        resolved_release_id = release_id or f"{run_id}-release"
        release_dir = self.releases_root / resolved_release_id
        if release_dir.exists():
            raise ArtifactValidationError("Release is immutable and already exists", release_id=resolved_release_id)
        release_dir.mkdir(parents=True, exist_ok=False)
        release_manifest = {
            "schema_version": "project_blends_release_manifest.v1",
            "release_id": resolved_release_id,
            "run_id": run_id,
            "locked_at_utc": utc_now_iso(),
            "run_manifest_sha256": sha256_file(self.run_dir(run_id) / "manifest.json"),
            "acceptance": acceptance_report,
            "run_manifest": manifest,
            "contract": {
                "immutable": True,
                "evidence_grounded": True,
                "identity_qc_required": True,
                "reaction_hypotheses_abstain_when_unsupported": True,
                "exploratory_food_predictions_not_evidence": True,
            },
        }
        path = release_dir / "release_manifest.json"
        write_json_atomic(path, release_manifest)
        return path
