from __future__ import annotations

import json
import shutil
import sys
import tempfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
from rdkit import Chem

from project_blends_compute.artifacts.formats import write_records_table
from project_blends_compute.identity.chemistry import validate_structure
from project_blends_compute.storage_curation.models import StorageCurationBuildRequest, StorageCurationBuildResponse
from project_blends_compute.utils import sha256_file, stable_hash, utc_now_iso, write_json_atomic


SCHEMA_VERSION = "storage_reaction_evidence.v1"


class StorageReactionCurationBuilder:
    """Build a reaction_curation-compatible storage evidence artifact bundle."""

    def __init__(self, output_root: Path, registry_path: Path, reaction_curation_project_root: Path | None = None) -> None:
        self.output_root = output_root
        self.registry_path = registry_path
        self.reaction_curation_project_root = reaction_curation_project_root

    def build(self, request: StorageCurationBuildRequest) -> StorageCurationBuildResponse:
        final_dir = self.output_root / request.dataset_name / request.version
        if final_dir.exists():
            raise FileExistsError(f"Immutable curation version already exists: {final_dir}")
        final_dir.parent.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(prefix=f"{request.dataset_name}-{request.version}-", dir=final_dir.parent))
        try:
            reactions = [record.model_dump(mode="json", by_alias=True) for record in request.reactions]
            conditions = [self._condition_row(record) for record in request.reactions]
            sources = [record.model_dump(mode="json") for record in request.sources]
            nonreactions = [record.model_dump(mode="json") for record in request.nonreaction_explanations]
            linkage = [record.model_dump(mode="json") for record in request.identity_linkage]

            reactions_result = write_records_table(reactions, staging / "storage_reactions", prefer_parquet=True, write_csv=True)
            conditions_result = write_records_table(conditions, staging / "storage_condition_context", prefer_parquet=True, write_csv=True)
            sources_result = write_records_table(sources, staging / "storage_evidence_sources", prefer_parquet=True, write_csv=True)
            nonreaction_result = write_records_table(nonreactions, staging / "nonreaction_explanations", prefer_parquet=True, write_csv=True)
            linkage_result = write_records_table(linkage, staging / "identity_linkage", prefer_parquet=True, write_csv=True)

            quality = self._quality_report(request)
            quality_path = staging / "quality_report.json"
            write_json_atomic(quality_path, quality)
            artifacts = {
                "reactions": reactions_result["primary"],
                "conditions": conditions_result["primary"],
                "sources": sources_result["primary"],
                "nonreaction_explanations": nonreaction_result["primary"],
                "identity_linkage": linkage_result["primary"],
                "quality_report": str(quality_path),
            }
            manifest = {
                "schema_version": SCHEMA_VERSION,
                "dataset_name": request.dataset_name,
                "dataset_kind": "storage_reaction_evidence",
                "version": request.version,
                "build_timestamp_utc": utc_now_iso(),
                "producer_module": "project_blends_compute.storage_curation",
                "artifacts": {},
                "contract": {
                    "reaction_specific_condition_context_primary": True,
                    "condition_signature_context_secondary": True,
                    "nonreaction_explanations_preserved": True,
                    "runtime_consumes_curated_artifacts_not_raw_sources": True,
                },
            }
            for name, raw_path in artifacts.items():
                path = Path(raw_path)
                manifest["artifacts"][name] = {
                    "path": path.name,
                    "sha256": sha256_file(path),
                    "rows": len(reactions) if name == "reactions" else len(conditions) if name == "conditions" else None,
                }
            manifest_path = staging / "manifest.json"
            write_json_atomic(manifest_path, manifest)
            artifacts["manifest"] = str(manifest_path)
            staging.rename(final_dir)
            registry_entry = self._register(request, final_dir, artifacts)
            return StorageCurationBuildResponse(
                ok=bool(quality["pass"]),
                dataset_name=request.dataset_name,
                version=request.version,
                output_dir=str(final_dir),
                registry_path=str(self.registry_path),
                artifacts={name: str(final_dir / Path(path).name) for name, path in artifacts.items()},
                quality_report=quality,
            )
        except Exception:
            shutil.rmtree(staging, ignore_errors=True)
            raise

    @staticmethod
    def _condition_row(record: Any) -> dict[str, Any]:
        solvents = [record.solvent] if record.solvent else []
        agents = [record.catalyst_or_initiator] if record.catalyst_or_initiator else []
        temp_bucket = _bucket_temperature(record.temperature_c)
        time_bucket = _bucket_time(record.duration_h)
        signature_payload = {
            "solvents": solvents,
            "agents": agents,
            "temperature_bucket": temp_bucket,
            "time_bucket": time_bucket,
            "oxygen_exposure": record.oxygen_exposure,
            "light_exposure": record.light_exposure,
            "matrix": record.matrix,
        }
        return {
            "reaction_id": record.reaction_id,
            "source_dataset": "storage_reaction_evidence",
            "solvent_list_norm": solvents,
            "agent_or_spectator_list_norm": agents,
            "temperature_bucket": temp_bucket,
            "time_bucket": time_bucket,
            "condition_signature": stable_hash(json.dumps(signature_payload, sort_keys=True, default=str)),
            "temperature_c": record.temperature_c,
            "duration_h": record.duration_h,
            "oxygen_exposure": record.oxygen_exposure,
            "atmosphere": record.atmosphere,
            "light_exposure": record.light_exposure,
            "pH": record.ph,
            "water_activity": record.water_activity,
            "humidity": record.humidity,
            "container_material": record.container_material,
            "headspace": record.headspace,
            "matrix": record.matrix,
            "analytical_method": record.analytical_method,
            "provenance_json": json.dumps(record.provenance, ensure_ascii=False, sort_keys=True),
        }

    @staticmethod
    def _quality_report(request: StorageCurationBuildRequest) -> dict[str, Any]:
        duplicate_ids = [key for key, count in Counter(record.reaction_id for record in request.reactions).items() if count > 1]
        parse_failures: list[dict[str, Any]] = []
        identical_sides: list[str] = []
        missing_source_ids: list[str] = []
        mapped_count = 0
        for record in request.reactions:
            precursor = validate_structure(smiles=record.precursor_smiles)
            product = validate_structure(smiles=record.product_smiles)
            if not precursor.parse_valid or not product.parse_valid:
                parse_failures.append({"reaction_id": record.reaction_id, "precursor_valid": precursor.parse_valid, "product_valid": product.parse_valid})
            if precursor.canonical_smiles and precursor.canonical_smiles == product.canonical_smiles:
                identical_sides.append(record.reaction_id)
            if not record.source_id:
                missing_source_ids.append(record.reaction_id)
            if record.mapped_reaction_smiles:
                mapped_count += 1
        warnings: list[str] = []
        if not request.sources:
            warnings.append("no_source_metadata_rows")
        if mapped_count < len(request.reactions):
            warnings.append("not_all_reactions_are_atom_mapped")
        passed = not duplicate_ids and not parse_failures and not missing_source_ids
        return {
            "schema_version": "storage_reaction_curation_qc.v1",
            "dataset_name": request.dataset_name,
            "version": request.version,
            "generated_at_utc": utc_now_iso(),
            "pass": passed,
            "metrics": {
                "reaction_rows": len(request.reactions),
                "condition_rows": len(request.reactions),
                "source_rows": len(request.sources),
                "nonreaction_explanation_rows": len(request.nonreaction_explanations),
                "identity_linkage_rows": len(request.identity_linkage),
                "mapped_reactions": mapped_count,
            },
            "failures": {
                "duplicate_reaction_ids": duplicate_ids,
                "parse_failures": parse_failures,
                "missing_source_ids": missing_source_ids,
            },
            "warnings": warnings + ([f"identical_precursor_product:{identical_sides}"] if identical_sides else []),
        }

    def _register(self, request: StorageCurationBuildRequest, final_dir: Path, artifacts: dict[str, str]) -> dict[str, Any]:
        reactions_path = final_dir / Path(artifacts["reactions"]).name
        conditions_path = final_dir / Path(artifacts["conditions"]).name
        entry = {
            "dataset_name": request.dataset_name,
            "dataset_kind": "storage_reaction_evidence",
            "version": request.version,
            "reactions_path": str(reactions_path),
            "conditions_path": str(conditions_path),
            "steps_path": None,
            "role_assignments_path": None,
            "schema_version": SCHEMA_VERSION,
            "producer_module": "project_blends_compute.storage_curation",
            "build_timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "notes": "Storage reaction evidence extension. Reaction-specific condition retrieval is primary.",
        }
        # Prefer the authoritative reaction_curation registry writer when the project root is configured.
        if self.reaction_curation_project_root and self.reaction_curation_project_root.exists():
            source_root = self.reaction_curation_project_root / "reaction_curation_core"
            for path in (self.reaction_curation_project_root, source_root):
                text = str(path.resolve())
                if text not in sys.path:
                    sys.path.insert(0, text)
            try:
                from reaction_curation.benchmark_registry import BenchmarkRegistryWriter
                from reaction_curation.schemas import BenchmarkRegistryEntry

                writer = BenchmarkRegistryWriter(self.registry_path)
                writer.register(BenchmarkRegistryEntry(**entry), overwrite=request.overwrite_registry_version)
                return entry
            except Exception:
                pass
        registry: dict[str, Any] = {}
        if self.registry_path.exists():
            registry = json.loads(self.registry_path.read_text(encoding="utf-8"))
        block = registry.setdefault(request.dataset_name, {"active_version": request.version, "versions": {}})
        versions = block.setdefault("versions", {})
        if request.version in versions and not request.overwrite_registry_version:
            raise ValueError(f"Version already registered: {request.dataset_name}/{request.version}")
        versions[request.version] = entry
        block["active_version"] = request.version
        write_json_atomic(self.registry_path, registry)
        return entry


def _bucket_temperature(value: float | None) -> str | None:
    if value is None:
        return None
    if value < 0:
        return "<0"
    if value <= 25:
        return "0-25"
    if value <= 60:
        return "25-60"
    if value <= 100:
        return "60-100"
    return "100+"


def _bucket_time(value: float | None) -> str | None:
    if value is None:
        return None
    if value <= 1:
        return "<=1h"
    if value <= 6:
        return "1-6h"
    if value <= 24:
        return "6-24h"
    if value <= 168:
        return "1-7d"
    if value <= 720:
        return "1-30d"
    return "30d+"
