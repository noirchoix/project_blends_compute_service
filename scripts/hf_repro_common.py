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


@dataclass(frozen=True)
class CopyRecord:
    logical_name: str
    destination: str
    kind: str
    bytes: int
    sha256: str | None = None


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
                "__pycache__", "*.pyc", "*.pyo", ".pytest_cache", ".mypy_cache", ".ruff_cache", "*.ipynb"
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
    mapping = {
        "fooddb_db_path": "serving.duckdb",
        "fooddb_food_lookup_path": "curated_food_lookup.parquet",
        "fooddb_compound_lookup_path": "curated_compound_lookup.parquet",
        "fooddb_edges_path": "curated_food_compound_content.parquet",
    }
    out: dict[str, str | None] = {}
    records: list[CopyRecord] = []
    target = destination_root / "fooddb"
    for key, filename in mapping.items():
        raw = paths.get(key)
        if raw in (None, ""):
            out[key] = None
            continue
        src = Path(str(raw)).expanduser().resolve()
        if not src.exists():
            raise FileNotFoundError(f"Configured FoodDB artifact is missing ({key}): {src}")
        dst = target / filename
        records.append(copy_path(src, dst, logical_name=f"fooddb:{key}"))
        out[key] = dst.as_posix()
    if not out.get("fooddb_db_path") and not all(out.get(k) for k in ("fooddb_food_lookup_path", "fooddb_compound_lookup_path", "fooddb_edges_path")):
        raise ValueError("FoodDB reproduction requires serving.duckdb or all three curated Parquet tables")
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
---

# Project Blends v0.1.7 reproducibility artifacts

This dataset repository is an immutable runtime-artifact bundle for the public
`project_blends_compute_service` codebase. It is intended to reproduce frozen run
`{LOCKED_RUN_ID}` without machine-specific external paths.

The bundle contains runtime inputs only: curated storage evidence, the mapped reaction-template
runtime artifact, the precomputed DESS serving artifact, the exact COCONUT taxonomy model files,
FoodDB serving/curated tables, and the small upstream `rxn_bridge` provider code snapshot required
by the frozen service's dynamic imports.

It intentionally does **not** include raw instrument files, xTB/ORCA binaries, FoodChem ML,
training caches, or the rejected USPTO multistep artifact.

## Integrity

Every distributed file is listed in `REPRODUCIBILITY_MANIFEST.json` with SHA-256. Reproducers
should download a pinned Hugging Face revision and run the Project Blends bootstrap script, which
verifies all hashes before writing `config/path_manifest.local.json`.

## Redistribution notice

The bundle assembler does not determine third-party redistribution rights. Before making this
repository public, the publisher must verify the licenses/terms that apply to each upstream data/model
artifact. See `REDISTRIBUTION_REVIEW.md` in the bundle.
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
