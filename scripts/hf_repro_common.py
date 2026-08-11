from __future__ import annotations

import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from importlib import metadata
from pathlib import Path
from typing import Any, Iterable

BUNDLE_SCHEMA = "project_blends.hf_repro_bundle.v1"
BUNDLE_DIRNAME = "data_hf"
LOCKED_SERVICE_VERSION = "0.1.7"
LOCKED_RUN_ID = "pb-20260810T160809-be9a0fcb65"
STORAGE_DATASET = "storage_reaction_evidence"
EXPECTED_STORAGE_VERSION = "v1.0.1"
REQUIRED_RXN_LANES = ("uspto_templates", "dess_physics", "taxonomy_coconut")
RUNTIME_PACKAGES = (
    "fastapi",
    "uvicorn",
    "pydantic",
    "pydantic-settings",
    "httpx",
    "pandas",
    "numpy",
    "scipy",
    "rdkit",
    "python-docx",
    "duckdb",
    "lightgbm",
    "pyarrow",
    "huggingface-hub",
)

# Only these keys are needed by the frozen v0.1.7 runtime. The collector copies
# the entire active USPTO template artifact root, the DESS serving DB, and the
# exact COCONUT inference files. This avoids publishing unrelated training caches.
LANE_RUNTIME_PATH_KEYS: dict[str, tuple[str, ...]] = {
    "uspto_templates": ("artifact_root", "manifest", "templates", "template_stats", "duckdb"),
    "dess_physics": ("duckdb",),
    "taxonomy_coconut": (
        "model_superclass",
        "model_class",
        "metadata_superclass",
        "metadata_class",
        "config_normalized",
        "classes_lookup",
        "eval_summary",
        "qc_report",
    ),
}

LICENSE_SCHEMA = "project_blends.artifact_licenses.v1"
LICENSE_TEMPLATE_DIR = Path(__file__).resolve().parents[1] / "resources" / "hf_licenses"


@dataclass(frozen=True)
class CopyRecord:
    logical_name: str
    destination: str
    kind: str
    bytes: int
    sha256: str | None = None



def portable_copy_summary(records: list[CopyRecord], bundle_root: Path) -> list[dict[str, Any]]:
    """Serialize collector provenance without leaking machine-specific absolute paths."""
    root = bundle_root.resolve()
    rows: list[dict[str, Any]] = []
    for record in records:
        raw = Path(record.destination)
        try:
            destination = raw.resolve().relative_to(root).as_posix()
        except Exception:
            destination = raw.as_posix()
        rows.append({
            "logical_name": record.logical_name,
            "destination": destination,
            "kind": record.kind,
            "bytes": int(record.bytes),
            "sha256": record.sha256,
        })
    return rows


def write_license_bundle(root: Path, *, reaction_curation_registry: Path | None = None) -> dict[str, Any]:
    """Write component-level mixed-license metadata and required redistribution notices."""
    licenses = root / "LICENSES"
    licenses.mkdir(parents=True, exist_ok=True)
    template_names = (
        "RXN_UTILS_APACHE_2_0.txt",
        "DESS_DESRES_DATA_SETS_LICENSE.txt",
        "COCONUT_CC0_1_0_NOTICE.txt",
        "FOODB_CC_BY_NC_4_0_NOTICE.txt",
        "REACTION_CURATION_MIT_NOTICE.txt",
        "PROJECT_BLENDS_ARTIFACT_NOTICE.txt",
    )
    for name in template_names:
        src = LICENSE_TEMPLATE_DIR / name
        if not src.exists():
            raise FileNotFoundError(f"HF license template missing: {src}")
        shutil.copy2(src, licenses / name)

    rc_license = None
    if reaction_curation_registry is not None and reaction_curation_registry.exists():
        rc_root = infer_repo_root_from_registry(reaction_curation_registry)
        for candidate in ("LICENSE", "LICENSE.txt", "LICENSE.md"):
            src = rc_root / candidate
            if src.exists() and src.is_file():
                rc_license = licenses / f"REACTION_CURATION_SOURCE_{src.name}"
                shutil.copy2(src, rc_license)
                break

    payload = {
        "schema_version": LICENSE_SCHEMA,
        "repository_license_metadata": "other",
        "blanket_license": None,
        "policy": "Each runtime subtree retains its own upstream/source terms; no license is broadened by inclusion in this bundle.",
        "components": [
            {
                "component_id": "rxn_bridge_runtime_code",
                "path_prefixes": ["rxn_bridge_runtime/reaction_framework/", "rxn_bridge_runtime/pipelines/"],
                "license_id": "source-repository-terms",
                "notice": "Publisher-authored/integrated runtime code snapshot; retain the source repository license and provenance.",
            },
            {
                "component_id": "mapped_uspto_templates",
                "path_prefixes": ["rxn_bridge_runtime/data/rxn_artifacts/uspto_templates/"],
                "license_id": "Apache-2.0",
                "notice_file": "LICENSES/RXN_UTILS_APACHE_2_0.txt",
                "provenance": "mapped_10k reaction/template runtime artifact built by the publisher using rxnutils tooling",
            },
            {
                "component_id": "dess_physics",
                "path_prefixes": ["rxn_bridge_runtime/data/rxn_artifacts/dess_physics/"],
                "license_id": "DESRES-Data-Sets-License",
                "notice_file": "LICENSES/DESS_DESRES_DATA_SETS_LICENSE.txt",
                "redistribution_requirement": "retain copyright notice, conditions, and disclaimer",
            },
            {
                "component_id": "taxonomy_coconut",
                "path_prefixes": ["rxn_bridge_runtime/data/rxn_artifacts/taxonomy_coconut/"],
                "license_id": "CC0-1.0",
                "notice_file": "LICENSES/COCONUT_CC0_1_0_NOTICE.txt",
            },
            {
                "component_id": "storage_reaction_evidence",
                "path_prefixes": ["reaction_curation_runtime/data/rxn_artifacts/reaction_curation/"],
                "license_id": "MIT",
                "notice_file": "LICENSES/REACTION_CURATION_MIT_NOTICE.txt",
                "source_license_file": rc_license.relative_to(root).as_posix() if rc_license else None,
                "note": "Primary-literature citations remain citations; this license metadata concerns the curated artifact/pipeline lineage.",
            },
            {
                "component_id": "fooddb",
                "path_prefixes": ["fooddb/"],
                "license_id": "CC-BY-NC-4.0",
                "notice_file": "LICENSES/FOODB_CC_BY_NC_4_0_NOTICE.txt",
                "commercial_use_requires_permission": True,
                "attribution_required": True,
                "citation_requested_for_significant_portions": True,
            },
            {
                "component_id": "bundle_metadata",
                "path_prefixes": ["environment/", "LICENSES/"],
                "license_id": "mixed-metadata",
                "notice_file": "LICENSES/PROJECT_BLENDS_ARTIFACT_NOTICE.txt",
            },
        ],
    }
    json_write(root / "ARTIFACT_LICENSES.json", payload)
    return payload


def audit_bundle(root: Path) -> dict[str, Any]:
    """Audit integrity, portability, runtime completeness, and license notices."""
    root = root.resolve()
    digest = verify_manifest_digest(root)
    manifest_path = root / "REPRODUCIBILITY_MANIFEST.json"
    manifest = json_load(manifest_path) if manifest_path.exists() else {}
    files = manifest.get("files") or []
    integrity = verify_file_manifest(root, files) if files else {"pass": False, "checked": 0, "missing": ["manifest_files"], "mismatches": []}
    rels = {str(row.get("path") or "") for row in files}
    required_prefixes = {
        "mapped_uspto_templates": "rxn_bridge_runtime/data/rxn_artifacts/uspto_templates/",
        "dess_physics": "rxn_bridge_runtime/data/rxn_artifacts/dess_physics/",
        "taxonomy_coconut": "rxn_bridge_runtime/data/rxn_artifacts/taxonomy_coconut/",
        "storage_reaction_evidence": f"reaction_curation_runtime/data/rxn_artifacts/reaction_curation/curated/{STORAGE_DATASET}/{EXPECTED_STORAGE_VERSION}/",
        "fooddb_serving": "fooddb/serving.duckdb",
    }
    runtime_presence = {}
    for key, prefix in required_prefixes.items():
        runtime_presence[key] = prefix in rels if not prefix.endswith("/") else any(r.startswith(prefix) for r in rels)

    forbidden_tokens = (
        "uspto_llm_multistep_only",
        "foodchem_ml",
        "/checkpoints/",
        "\\checkpoints\\",
        "training_checkpoint",
        ".git/",
        "__pycache__/",
    )
    forbidden_paths = sorted(r for r in rels if any(token.lower() in r.lower() for token in forbidden_tokens))

    local_path_hits: list[dict[str, Any]] = []
    text_suffixes = {".json", ".md", ".txt", ".toml", ".yaml", ".yml"}
    markers = ("C:\\Users\\", "C:/Users/", "/home/", "/Users/")
    text_candidates = sorted(rels | ({"REPRODUCIBILITY_MANIFEST.json"} if manifest_path.exists() else set()))
    for rel in text_candidates:
        path = root / rel
        if path.suffix.lower() not in text_suffixes or not path.exists() or path.stat().st_size > 5_000_000:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        found = [marker for marker in markers if marker in text]
        if found:
            local_path_hits.append({"path": rel, "markers": found})

    license_path = root / "ARTIFACT_LICENSES.json"
    license_payload = json_load(license_path) if license_path.exists() else {}
    missing_license_notices = []
    for component in license_payload.get("components") or []:
        notice = component.get("notice_file")
        if notice and not (root / str(notice)).exists():
            missing_license_notices.append(str(notice))
        source_license = component.get("source_license_file")
        if source_license and not (root / str(source_license)).exists():
            missing_license_notices.append(str(source_license))

    largest = sorted(
        ({"path": r, "bytes": (root / r).stat().st_size} for r in rels if (root / r).exists()),
        key=lambda row: row["bytes"], reverse=True
    )[:15]
    passed = (
        digest.get("pass") is True
        and integrity.get("pass") is True
        and all(runtime_presence.values())
        and not forbidden_paths
        and not local_path_hits
        and bool(license_payload)
        and not missing_license_notices
    )
    return {
        "pass": passed,
        "manifest_digest": digest,
        "file_integrity": integrity,
        "runtime_presence": runtime_presence,
        "forbidden_paths": forbidden_paths,
        "machine_local_path_leaks": local_path_hits,
        "license_metadata_present": bool(license_payload),
        "missing_license_notices": sorted(set(missing_license_notices)),
        "file_count": len(rels),
        "bytes": sum((root / r).stat().st_size for r in rels if (root / r).exists()),
        "largest_files": largest,
    }

def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def json_load(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Expected a JSON object: {path}")
    return payload


def json_write(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def infer_repo_root_from_registry(registry_path: Path) -> Path:
    resolved = registry_path.resolve()
    parts = resolved.parts
    try:
        data_idx = parts.index("data")
    except ValueError:
        return resolved.parent
    return Path(*parts[:data_idx]) if data_idx > 0 else Path(resolved.anchor)


def resolve_registry_path(registry_path: Path, raw: str | Path) -> Path:
    p = Path(raw)
    if p.is_absolute():
        return p.resolve()
    return (infer_repo_root_from_registry(registry_path) / p).resolve()


def _dir_size(path: Path) -> int:
    return sum(p.stat().st_size for p in path.rglob("*") if p.is_file())


def copy_path(src: Path, dst: Path, *, logical_name: str) -> CopyRecord:
    src = src.resolve()
    if not src.exists():
        raise FileNotFoundError(f"Required reproducibility artifact is missing: {src}")
    if src.is_dir():
        if dst.exists():
            shutil.rmtree(dst)
        shutil.copytree(src, dst)
        return CopyRecord(logical_name, dst.as_posix(), "directory", _dir_size(dst), None)
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
    return CopyRecord(logical_name, dst.as_posix(), "file", dst.stat().st_size, sha256_file(dst))


def copy_runtime_code(source_root: Path, destination_root: Path) -> list[CopyRecord]:
    """Copy only the upstream code needed by Project Blends inference.

    The frozen service dynamically imports ``reaction_framework`` and the taxonomy
    feature builder under ``pipelines``. Large upstream data directories, Git state,
    notebooks, caches and environments are deliberately excluded.
    """
    source_root = source_root.resolve()
    records: list[CopyRecord] = []
    for name in ("reaction_framework", "pipelines"):
        src = source_root / name
        if not src.exists():
            raise FileNotFoundError(f"rxn_bridge runtime code directory missing: {src}")
        dst = destination_root / name
        if dst.exists():
            shutil.rmtree(dst)
        shutil.copytree(
            src,
            dst,
            ignore=shutil.ignore_patterns(
                "__pycache__", "*.pyc", "*.pyo", ".pytest_cache", ".mypy_cache", ".ruff_cache",
                "*.ipynb", "*.md", "*.markdown",
            ),
        )
        records.append(CopyRecord(f"rxn_bridge_code:{name}", dst.as_posix(), "directory", _dir_size(dst), None))
    init = source_root / "__init__.py"
    if init.exists():
        records.append(copy_path(init, destination_root / "__init__.py", logical_name="rxn_bridge_code:__init__"))
    return records


def _copy_under_root(src: Path, src_root: Path, dst_root: Path) -> Path:
    try:
        rel = src.resolve().relative_to(src_root.resolve())
        return dst_root / rel
    except ValueError:
        safe = hashlib.sha256(str(src.resolve()).encode("utf-8")).hexdigest()[:12]
        return dst_root / "external_runtime_assets" / f"{safe}_{src.name}"


def _is_pathlike_registry_value(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _looks_like_path_string(value: str) -> bool:
    text = value.strip()
    if not text:
        return False
    if "/" in text or "\\" in text:
        return True
    suffix = Path(text).suffix.lower()
    return suffix in {
        ".json", ".jsonl", ".parquet", ".csv", ".tsv", ".duckdb", ".db",
        ".txt", ".pt", ".pth", ".pkl", ".joblib", ".npy", ".npz", ".bin",
    }


def build_minimal_rxn_registry(
    source_registry_path: Path,
    destination_runtime_root: Path,
) -> tuple[dict[str, Any], list[CopyRecord]]:
    """Copy and rewrite the exact rxn_bridge runtime closure used by v0.1.7."""
    source_registry_path = source_registry_path.resolve()
    source_registry = json_load(source_registry_path)
    source_repo_root = infer_repo_root_from_registry(source_registry_path)
    out_registry: dict[str, Any] = {}
    records: list[CopyRecord] = []
    copied_sources: dict[Path, Path] = {}

    for lane in REQUIRED_RXN_LANES:
        block = source_registry.get(lane)
        if not isinstance(block, dict):
            raise KeyError(f"Required rxn_bridge artifact lane is not registered: {lane}")
        active = block.get("active_version")
        versions = block.get("versions")
        if not isinstance(active, str) or not isinstance(versions, dict) or not isinstance(versions.get(active), dict):
            raise ValueError(f"Malformed active registry entry for lane: {lane}")
        payload = dict(versions[active])
        new_payload: dict[str, Any] = {}

        # Keep non-path metadata unchanged.
        runtime_keys = set(LANE_RUNTIME_PATH_KEYS[lane])
        for key, value in payload.items():
            if key not in runtime_keys:
                # Drop path-like values not used by the runtime (training checkpoints,
                # training predictions, etc.), but preserve scalar metadata.
                if isinstance(value, str):
                    candidate = resolve_registry_path(source_registry_path, value)
                    if candidate.exists() or _looks_like_path_string(value):
                        continue
                new_payload[key] = value

        if lane == "uspto_templates":
            root_raw = payload.get("artifact_root")
            if not _is_pathlike_registry_value(root_raw):
                raise KeyError("uspto_templates active payload is missing artifact_root")
            source_artifact_root = resolve_registry_path(source_registry_path, root_raw)
            dest_artifact_root = destination_runtime_root / "data" / "rxn_artifacts" / "uspto_templates" / "curated" / active
            records.append(copy_path(source_artifact_root, dest_artifact_root, logical_name=f"{lane}:{active}:artifact_root"))
            copied_sources[source_artifact_root.resolve()] = dest_artifact_root
            new_payload["artifact_root"] = dest_artifact_root.relative_to(destination_runtime_root).as_posix()
            for key in ("manifest", "templates", "template_stats", "duckdb"):
                raw = payload.get(key)
                if not _is_pathlike_registry_value(raw):
                    continue
                src = resolve_registry_path(source_registry_path, raw)
                try:
                    rel_inside = src.relative_to(source_artifact_root)
                    dst = dest_artifact_root / rel_inside
                except ValueError:
                    dst = _copy_under_root(src, source_repo_root, destination_runtime_root)
                    records.append(copy_path(src, dst, logical_name=f"{lane}:{active}:{key}"))
                new_payload[key] = dst.relative_to(destination_runtime_root).as_posix()
        else:
            for key in LANE_RUNTIME_PATH_KEYS[lane]:
                raw = payload.get(key)
                if not _is_pathlike_registry_value(raw):
                    continue
                src = resolve_registry_path(source_registry_path, raw)
                if not src.exists():
                    # Optional taxonomy eval/QC paths may be absent from some registries.
                    if key in {"eval_summary", "qc_report"}:
                        continue
                    raise FileNotFoundError(f"Registry path for {lane}.{key} does not exist: {src}")
                lane_dst_root = destination_runtime_root / "data" / "rxn_artifacts" / lane / "curated" / active
                dst = lane_dst_root / src.name
                records.append(copy_path(src, dst, logical_name=f"{lane}:{active}:{key}"))
                new_payload[key] = dst.relative_to(destination_runtime_root).as_posix()

        out_registry[lane] = {"active_version": active, "versions": {active: new_payload}}

    out_path = destination_runtime_root / "data" / "rxn_artifacts" / "registry" / "artifact_registry.json"
    json_write(out_path, out_registry)
    records.append(CopyRecord("rxn_bridge:portable_registry", out_path.as_posix(), "file", out_path.stat().st_size, sha256_file(out_path)))
    return out_registry, records


def collect_storage_evidence(
    source_registry_path: Path,
    destination_root: Path,
    *,
    dataset_name: str = STORAGE_DATASET,
) -> tuple[dict[str, Any], list[CopyRecord]]:
    source_registry_path = source_registry_path.resolve()
    registry = json_load(source_registry_path)
    block = registry.get(dataset_name)
    if not isinstance(block, dict):
        raise KeyError(f"Reaction curation dataset is not registered: {dataset_name}")
    active = block.get("active_version")
    versions = block.get("versions")
    if not isinstance(active, str) or not isinstance(versions, dict) or not isinstance(versions.get(active), dict):
        raise ValueError(f"Malformed reaction curation entry: {dataset_name}")
    entry = dict(versions[active])
    reactions_raw = entry.get("reactions_path")
    if not isinstance(reactions_raw, str) or not reactions_raw.strip():
        raise KeyError("storage_reaction_evidence entry has no reactions_path")
    reactions_src = Path(reactions_raw)
    if not reactions_src.is_absolute():
        reactions_src = (source_registry_path.parent / reactions_src).resolve()
    artifact_src = reactions_src.parent
    if not artifact_src.exists():
        raise FileNotFoundError(f"Storage evidence artifact directory missing: {artifact_src}")

    artifact_dst = destination_root / "data" / "rxn_artifacts" / "reaction_curation" / "curated" / dataset_name / active
    records = [copy_path(artifact_src, artifact_dst, logical_name=f"reaction_curation:{dataset_name}:{active}")]

    registry_dir = destination_root / "data" / "rxn_artifacts" / "reaction_curation"
    portable_entry = dict(entry)
    for key in ("reactions_path", "conditions_path", "steps_path", "role_assignments_path"):
        raw = entry.get(key)
        if raw in (None, ""):
            portable_entry[key] = None if raw is None else raw
            continue
        src = Path(str(raw))
        if not src.is_absolute():
            src = (source_registry_path.parent / src).resolve()
        try:
            rel = src.relative_to(artifact_src)
            portable_entry[key] = (Path("curated") / dataset_name / active / rel).as_posix()
        except ValueError:
            dst = registry_dir / "external_runtime_assets" / src.name
            records.append(copy_path(src, dst, logical_name=f"reaction_curation:{dataset_name}:{active}:{key}"))
            portable_entry[key] = dst.relative_to(registry_dir).as_posix()

    portable_registry = {dataset_name: {"active_version": active, "versions": {active: portable_entry}}}
    out_registry = registry_dir / "benchmark_registry.json"
    json_write(out_registry, portable_registry)
    records.append(CopyRecord("reaction_curation:portable_registry", out_registry.as_posix(), "file", out_registry.stat().st_size, sha256_file(out_registry)))
    return portable_registry, records


def collect_fooddb(paths: dict[str, Any], destination_root: Path) -> tuple[dict[str, str | None], list[CopyRecord]]:
    """Collect the FoodDB runtime backend actually needed by v0.1.7.

    ``FoodDBAdapter`` prefers ``fooddb_db_path`` when a serving DuckDB exists and
    only falls back to the three curated Parquet tables when that database is
    unavailable.  The collector mirrors that runtime contract: stale/missing
    optional Parquet paths must not make an otherwise reproducible DuckDB-backed
    bundle fail.
    """
    mapping = {
        "fooddb_db_path": "serving.duckdb",
        "fooddb_food_lookup_path": "curated_food_lookup.parquet",
        "fooddb_compound_lookup_path": "curated_compound_lookup.parquet",
        "fooddb_edges_path": "curated_food_compound_content.parquet",
    }
    parquet_keys = (
        "fooddb_food_lookup_path",
        "fooddb_compound_lookup_path",
        "fooddb_edges_path",
    )
    out: dict[str, str | None] = {key: None for key in mapping}
    records: list[CopyRecord] = []
    target = destination_root / "fooddb"

    configured: dict[str, Path | None] = {}
    for key in mapping:
        raw = paths.get(key)
        configured[key] = None if raw in (None, "") else Path(str(raw)).expanduser().resolve()

    db_src = configured["fooddb_db_path"]
    if db_src is not None and db_src.exists():
        dst = target / mapping["fooddb_db_path"]
        records.append(copy_path(db_src, dst, logical_name="fooddb:fooddb_db_path"))
        out["fooddb_db_path"] = dst.as_posix()

        # Copy any available Parquet fallbacks as useful redundancy, but do not
        # require them when the primary DuckDB backend is present.
        for key in parquet_keys:
            src = configured[key]
            if src is None or not src.exists():
                continue
            dst = target / mapping[key]
            records.append(copy_path(src, dst, logical_name=f"fooddb:{key}"))
            out[key] = dst.as_posix()
        return out, records

    # No usable DuckDB: the adapter requires the complete three-table Parquet
    # fallback.  A configured-but-missing DuckDB is therefore harmless only when
    # all fallback tables are actually available.
    missing = [key for key in parquet_keys if configured[key] is None or not configured[key].exists()]
    if missing:
        detail = ", ".join(
            f"{key}={configured[key] if configured[key] is not None else '<not configured>'}"
            for key in missing
        )
        db_detail = db_src if db_src is not None else "<not configured>"
        raise FileNotFoundError(
            "FoodDB reproduction requires an existing serving DuckDB or all three curated Parquet tables. "
            f"DuckDB: {db_detail}; missing fallback artifacts: {detail}"
        )

    for key in parquet_keys:
        src = configured[key]
        assert src is not None
        dst = target / mapping[key]
        records.append(copy_path(src, dst, logical_name=f"fooddb:{key}"))
        out[key] = dst.as_posix()
    return out, records


def flatten_path_manifest(payload: dict[str, Any]) -> dict[str, Any]:
    flattened: dict[str, Any] = {}
    for key, value in payload.items():
        if isinstance(value, dict):
            flattened.update(value)
        else:
            flattened[key] = value
    return flattened


def git_commit_if_available(root: Path) -> str | None:
    try:
        proc = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        value = proc.stdout.strip()
        return value or None
    except Exception:
        return None


def runtime_versions() -> dict[str, Any]:
    packages: dict[str, str | None] = {}
    for package in RUNTIME_PACKAGES:
        try:
            packages[package] = metadata.version(package)
        except metadata.PackageNotFoundError:
            packages[package] = None
    return {
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "packages": packages,
    }


def file_manifest(root: Path, *, exclude: Iterable[str] = ()) -> list[dict[str, Any]]:
    excluded = set(exclude)
    rows: list[dict[str, Any]] = []
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        rel = path.relative_to(root).as_posix()
        if rel in excluded or rel.startswith(".cache/huggingface/"):
            continue
        rows.append({"path": rel, "bytes": path.stat().st_size, "sha256": sha256_file(path)})
    return rows


def verify_manifest_digest(root: Path) -> dict[str, Any]:
    """Verify the detached SHA-256 for REPRODUCIBILITY_MANIFEST.json."""
    manifest_path = root / "REPRODUCIBILITY_MANIFEST.json"
    digest_path = root / "REPRODUCIBILITY_MANIFEST.sha256"
    if not manifest_path.exists():
        return {"pass": False, "reason": "manifest_missing", "expected": None, "actual": None}
    if not digest_path.exists():
        return {"pass": False, "reason": "manifest_digest_missing", "expected": None, "actual": sha256_file(manifest_path)}
    tokens = digest_path.read_text(encoding="ascii").strip().split()
    expected = tokens[0] if tokens else ""
    actual = sha256_file(manifest_path)
    return {
        "pass": bool(expected) and expected == actual,
        "reason": None if expected == actual and expected else "manifest_digest_mismatch",
        "expected": expected or None,
        "actual": actual,
    }


def verify_file_manifest(root: Path, rows: list[dict[str, Any]]) -> dict[str, Any]:
    checked = 0
    missing: list[str] = []
    mismatches: list[dict[str, str]] = []
    for row in rows:
        rel = str(row.get("path") or "")
        expected = str(row.get("sha256") or "")
        path = root / rel
        if not path.exists():
            missing.append(rel)
            continue
        actual = sha256_file(path)
        checked += 1
        if actual != expected:
            mismatches.append({"path": rel, "expected": expected, "actual": actual})
    return {
        "pass": not missing and not mismatches,
        "checked": checked,
        "missing": missing,
        "mismatches": mismatches,
    }


def write_runtime_lock(root: Path, versions: dict[str, Any]) -> None:
    env = root / "environment"
    json_write(env / "runtime_versions.json", versions)
    lines = []
    for name, version in (versions.get("packages") or {}).items():
        if version:
            lines.append(f"{name}=={version}")
    (env / "requirements-repro.lock.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")


def make_dataset_card() -> str:
    return f"""---
pretty_name: Project Blends v0.1.7 reproducibility artifacts
license: other
tags:
  - chemistry
  - phytochemistry
  - gc-ms
  - cheminformatics
  - reproducibility
---

# Project Blends v0.1.7 reproducibility artifacts

This dataset repository is the portable external-runtime closure for frozen Project Blends run
`{LOCKED_RUN_ID}`. Download a pinned Hugging Face revision and use the repository bootstrap
script to verify SHA-256 values and write a machine-local `config/path_manifest.local.json`.

## Runtime scope

Included: mapped reaction-template runtime artifacts, the precomputed DESS serving artifact, the
exact COCONUT taxonomy inference models/metadata, `storage_reaction_evidence {EXPECTED_STORAGE_VERSION}`,
FoodDB serving data, and the small upstream runtime-code snapshot required by dynamic imports.

Excluded: raw GC-MS instrument exports, training datasets/caches/checkpoints not needed for inference,
xTB/ORCA/Psi4, FoodChem ML, and the screened-but-unused USPTO multistep artifact.

## Mixed licensing

There is **no single blanket license for this bundle**. The dataset-card license is therefore `other`.
Read `ARTIFACT_LICENSES.json` and `LICENSES/` before reuse or redistribution. In particular, the
FoodDB-derived serving artifact is non-commercial (CC BY-NC 4.0 according to the current FooDB
site) and requires source acknowledgment; the DESS-derived artifact must retain the DESRES notice,
conditions, and disclaimer. COCONUT data is CC0 1.0.

## Integrity and provenance

Every distributed file is listed in `REPRODUCIBILITY_MANIFEST.json` with SHA-256.
`REPRODUCIBILITY_MANIFEST.sha256` protects that manifest itself. The publication reference run is
`{LOCKED_RUN_ID}` and the service version is `{LOCKED_SERVICE_VERSION}`.
"""


def path_manifest_for_bundle(project_root: Path, bundle_root: Path) -> dict[str, Any]:
    project_root = project_root.resolve()
    bundle_root = bundle_root.resolve()
    rxn_root = bundle_root / "rxn_bridge_runtime"
    rc_root = bundle_root / "reaction_curation_runtime"
    food = bundle_root / "fooddb"
    rxn_registry = rxn_root / "data" / "rxn_artifacts" / "registry" / "artifact_registry.json"
    rxn_payload = json_load(rxn_registry)
    uspto = rxn_payload["uspto_templates"]
    uspto_active = uspto["active_version"]
    uspto_root_raw = uspto["versions"][uspto_active]["artifact_root"]
    uspto_root = (rxn_root / uspto_root_raw).resolve()
    dess = rxn_payload["dess_physics"]
    dess_active = dess["active_version"]
    dess_duck = (rxn_root / dess["versions"][dess_active]["duckdb"]).resolve()
    tax = rxn_payload["taxonomy_coconut"]
    tax_active = tax["active_version"]
    tax_model = (rxn_root / tax["versions"][tax_active]["model_class"]).resolve()

    return {
        "original_experiment": {
            "project_blends_source_document": None,
            "raw_gc_ms_export_directory": None,
            "raw_chromatogram_directory": None,
            "nist_library_match_export_directory": None,
            "existing_peak_tables_path": None,
        },
        "reaction_curation": {
            "reaction_curation_project_root": str(rc_root),
            "reaction_curation_registry": str(
                rc_root / "data" / "rxn_artifacts" / "reaction_curation" / "benchmark_registry.json"
            ),
        },
        "rxn_bridge": {
            "rxn_bridge_project_root": str(rxn_root),
            "rxn_artifact_registry": str(rxn_registry),
            "rxn_template_artifact_root": str(uspto_root),
        },
        "fooddb": {
            "fooddb_db_path": str(food / "serving.duckdb") if (food / "serving.duckdb").exists() else None,
            "fooddb_food_lookup_path": str(food / "curated_food_lookup.parquet") if (food / "curated_food_lookup.parquet").exists() else None,
            "fooddb_compound_lookup_path": str(food / "curated_compound_lookup.parquet") if (food / "curated_compound_lookup.parquet").exists() else None,
            "fooddb_edges_path": str(food / "curated_food_compound_content.parquet") if (food / "curated_food_compound_content.parquet").exists() else None,
        },
        "supporting_artifacts": {
            "dess_artifact_root": str(dess_duck.parent),
            "taxonomy_artifact_root": str(tax_model.parent),
        },
        "runtime": {
            "rxnutils_source_root": None,
            "xtb_executable": None,
            "orca_executable": None,
            "psi4_executable": None,
        },
    }
