from __future__ import annotations

import ast
import json
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable

import pandas as pd

from project_blends_compute.artifacts.formats import read_table
from project_blends_compute.settings import Settings
from project_blends_compute.utils import sha256_file


class StorageEvidenceService:
    """Consume the full storage_reaction_evidence artifact family.

    The reaction_curation adapter remains responsible for reaction-candidate lookup.
    This service consumes the complementary source, non-reaction, identity-linkage and
    condition-compatibility tables even when the reaction gate yields zero candidates.
    """

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.dataset_name = settings.reaction_curation_dataset
        self.version: str | None = None
        self.artifact_dir: Path | None = None
        self.manifest: dict[str, Any] = {}
        self.quality_report: dict[str, Any] = {}
        self.sources: pd.DataFrame | None = None
        self.nonreactions: pd.DataFrame | None = None
        self.reactions: pd.DataFrame | None = None
        self.conditions: pd.DataFrame | None = None
        self.identity_linkage: pd.DataFrame | None = None
        self.error: str | None = None
        self.verification: dict[str, Any] = {"status": "not_run", "checked_files": [], "mismatches": []}
        self._load()

    @property
    def available(self) -> bool:
        return self.error is None and all(
            frame is not None
            for frame in (self.sources, self.nonreactions, self.reactions, self.conditions, self.identity_linkage)
        )

    def _load(self) -> None:
        registry_path = self.settings.reaction_curation_registry
        if registry_path is None or not registry_path.exists():
            self.error = "storage_evidence_registry_not_configured"
            return
        try:
            registry = json.loads(registry_path.read_text(encoding="utf-8"))
            block = registry.get(self.dataset_name)
            if not isinstance(block, dict):
                self.error = f"dataset_not_registered:{self.dataset_name}"
                return
            active = block.get("active_version")
            entry = (block.get("versions") or {}).get(active)
            if not isinstance(active, str) or not isinstance(entry, dict):
                self.error = "malformed_storage_evidence_registry_entry"
                return
            reactions_path = self._resolve_path(registry_path, entry.get("reactions_path"))
            if reactions_path is None or not reactions_path.exists():
                self.error = "storage_evidence_reactions_missing"
                return
            self.version = active
            self.artifact_dir = reactions_path.parent
            manifest_path = self.artifact_dir / "manifest.json"
            quality_path = self.artifact_dir / "quality_report.json"
            if not manifest_path.exists():
                self.error = "storage_evidence_manifest_missing"
                return
            if not quality_path.exists():
                self.error = "storage_evidence_quality_report_missing"
                return
            self.manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.quality_report = json.loads(quality_path.read_text(encoding="utf-8"))
            if not bool(self.quality_report.get("pass")):
                self.error = "storage_evidence_quality_report_failed"
                return
            if self.manifest.get("dataset_name") != self.dataset_name or self.manifest.get("version") != active:
                self.error = "storage_evidence_manifest_registry_mismatch"
                return
            if self.manifest.get("dataset_kind") != "storage_reaction_evidence":
                self.error = "storage_evidence_manifest_kind_mismatch"
                return
            artifacts = self.manifest.get("artifacts") or {}
            required_artifacts = {"reactions", "conditions", "sources", "nonreaction_explanations", "identity_linkage", "quality_report"}
            missing_artifacts = sorted(required_artifacts - set(artifacts)) if isinstance(artifacts, dict) else sorted(required_artifacts)
            if missing_artifacts:
                self.error = f"storage_evidence_manifest_missing_artifacts:{','.join(missing_artifacts)}"
                return
            self.verification = self._verify_manifest(self.artifact_dir, self.manifest)
            if self.verification.get("status") != "matched":
                self.error = "storage_evidence_artifact_hash_mismatch"
                return

            self.reactions = self._read_manifest_table(artifacts, "reactions", reactions_path)
            conditions_path = self._resolve_path(registry_path, entry.get("conditions_path"))
            self.conditions = self._read_manifest_table(artifacts, "conditions", conditions_path)
            self.sources = self._read_manifest_table(artifacts, "sources", self.artifact_dir / "storage_evidence_sources.parquet")
            self.nonreactions = self._read_manifest_table(artifacts, "nonreaction_explanations", self.artifact_dir / "nonreaction_explanations.parquet")
            self.identity_linkage = self._read_manifest_table(artifacts, "identity_linkage", self.artifact_dir / "identity_linkage.parquet")
            count_error = self._loaded_count_error()
            if count_error:
                self.error = count_error
                return
        except Exception as exc:
            self.error = f"storage_evidence_load_failed:{type(exc).__name__}:{exc}"

    def evaluate(self, records: Iterable[Any], canonical_entities: list[dict[str, Any]]) -> dict[str, Any]:
        if not self.available:
            return {
                "status": "unavailable",
                "dataset_name": self.dataset_name,
                "version": self.version,
                "error": self.error,
                "lane_status": {"storage_evidence": "unavailable"},
                "warnings": [self.error] if self.error else [],
                "counts": {},
                "source_evidence": [],
                "nonreaction_evidence": [],
                "transformation_precedents": [],
                "condition_compatibility": [],
                "sample_evidence": [],
                "compound_evidence": [],
                "linkage_qc": {},
                "claim_boundary": "storage-evidence corpus unavailable; do not infer storage mechanisms from appearance/disappearance alone",
            }

        source_rows = self._records(self.sources)
        nonreaction_rows = self._records(self.nonreactions)
        reaction_rows = self._records(self.reactions)
        condition_rows = self._records(self.conditions)
        linkage_rows = self._records(self.identity_linkage)
        source_ids_all = [str(row.get("source_id")) for row in source_rows if row.get("source_id")]
        source_by_id = {str(row.get("source_id")): row for row in source_rows if row.get("source_id")}
        condition_by_reaction: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in condition_rows:
            condition_by_reaction[str(row.get("reaction_id") or "")].append(row)

        canonical_ids = {str(row.get("compound_id")) for row in canonical_entities if row.get("compound_id")}
        linkage_ids = {str(row.get("compound_id")) for row in linkage_rows if row.get("compound_id")}
        observation_index, known_samples = self._observation_index(records)

        missing_source_links: list[dict[str, str]] = []
        missing_compound_links: list[dict[str, str]] = []
        missing_sample_links: list[dict[str, str]] = []

        enriched_nonreactions: list[dict[str, Any]] = []
        for row in nonreaction_rows:
            source_ids = _as_list(row.get("evidence_source_ids"))
            sources = []
            for source_id in source_ids:
                source = source_by_id.get(source_id)
                if source is None:
                    missing_source_links.append({"record_id": str(row.get("hypothesis_id")), "source_id": source_id})
                else:
                    sources.append(source)
            compound_id = _none_if_blank(row.get("compound_id"))
            sample_id = str(row.get("sample_id") or "")
            if compound_id and compound_id not in canonical_ids:
                missing_compound_links.append({"record_id": str(row.get("hypothesis_id")), "compound_id": compound_id})
            if sample_id and sample_id != "all" and sample_id not in known_samples:
                missing_sample_links.append({"record_id": str(row.get("hypothesis_id")), "sample_id": sample_id})
            observation = observation_index.get((sample_id, compound_id)) if compound_id and sample_id != "all" else None
            enriched_nonreactions.append({
                **row,
                "evidence_source_ids": source_ids,
                "sources": sources,
                "source_dois": [s.get("doi") for s in sources if s.get("doi")],
                "source_linkage_complete": len(sources) == len(source_ids),
                "compound_linkage_complete": (compound_id is None) or (compound_id in canonical_ids),
                "sample_linkage_complete": sample_id == "all" or sample_id in known_samples,
                "observed_profile": observation,
                "directness": "literature_supported_alternative" if sources else "project_specific_unresolved_alternative",
                "condition_compatibility": "not_formally_scored",
                "claim_boundary": "supports an alternative explanation only; does not establish that this process occurred in the Project Blends sample",
            })

        precedents: list[dict[str, Any]] = []
        compatibility_rows: list[dict[str, Any]] = []
        for row in reaction_rows:
            reaction_id = str(row.get("reaction_id") or "")
            source_id = str(row.get("source_id") or "")
            source = source_by_id.get(source_id)
            if source is None and source_id:
                missing_source_links.append({"record_id": reaction_id, "source_id": source_id})
            precursor_id = _none_if_blank(row.get("precursor_compound_id"))
            product_id = _none_if_blank(row.get("product_compound_id"))
            for role, compound_id in (("precursor", precursor_id), ("product", product_id)):
                if compound_id and compound_id not in canonical_ids:
                    missing_compound_links.append({"record_id": reaction_id, "compound_id": compound_id, "role": role})
            provenance = _as_mapping(row.get("provenance"))
            compatibility = str(provenance.get("condition_compatibility_with_project_blends") or "unknown")
            claim_boundary = str(provenance.get("claim_boundary") or "analogous precedent only; not evidence that the transformation occurred during Project Blends storage")
            conditions = condition_by_reaction.get(reaction_id, [])
            compatibility_row = {
                "reaction_id": reaction_id,
                "precursor_compound_id": precursor_id,
                "product_compound_id": product_id,
                "transformation_family": row.get("transformation_family"),
                "condition_compatibility": compatibility,
                "conditions": conditions,
                "claim_boundary": claim_boundary,
            }
            compatibility_rows.append(compatibility_row)
            precedents.append({
                **row,
                "provenance": provenance,
                "source": source,
                "source_doi": row.get("source_doi") or (source or {}).get("doi"),
                "conditions": conditions,
                "condition_compatibility": compatibility,
                "claim_boundary": claim_boundary,
                "directness": row.get("evidence_directness") or "analogous",
                "evidence_role": "transformation_precedent_only",
            })

        sample_evidence = self._group_evidence(enriched_nonreactions, key="sample_id")
        compound_evidence = self._group_evidence(
            [row for row in enriched_nonreactions if row.get("compound_id")], key="compound_id"
        )
        used_source_ids = sorted({sid for row in enriched_nonreactions for sid in row.get("evidence_source_ids", [])} | {str(r.get("source_id")) for r in reaction_rows if r.get("source_id")})
        source_evidence = [{**source_by_id[sid], "referenced_by_curated_evidence": True} for sid in used_source_ids if sid in source_by_id]
        unused_sources = sorted(set(source_by_id) - set(used_source_ids))

        nonreaction_ids = [str(row.get("hypothesis_id")) for row in nonreaction_rows if row.get("hypothesis_id")]
        linkage_row_ids = [str(row.get("compound_id")) for row in linkage_rows if row.get("compound_id")]
        duplicate_source_ids = _duplicates(source_ids_all)
        duplicate_nonreaction_ids = _duplicates(nonreaction_ids)
        duplicate_identity_linkage_ids = _duplicates(linkage_row_ids)
        linkage_qc = {
            "canonical_entities_expected": len(canonical_ids),
            "identity_linkage_rows": len(linkage_rows),
            "missing_canonical_identity_links": sorted(canonical_ids - linkage_ids),
            "unexpected_identity_links": sorted(linkage_ids - canonical_ids),
            "missing_source_links": missing_source_links,
            "missing_compound_links": missing_compound_links,
            "missing_sample_links": missing_sample_links,
            "unused_source_ids": unused_sources,
            "duplicate_source_ids": duplicate_source_ids,
            "duplicate_nonreaction_ids": duplicate_nonreaction_ids,
            "duplicate_identity_linkage_ids": duplicate_identity_linkage_ids,
            "pass": not missing_source_links and not missing_compound_links and not missing_sample_links and canonical_ids == linkage_ids and not duplicate_source_ids and not duplicate_nonreaction_ids and not duplicate_identity_linkage_ids,
        }
        warnings: list[str] = []
        if not linkage_qc["pass"]:
            warnings.append("storage_evidence_linkage_qc_failed")
        if unused_sources:
            warnings.append(f"storage_evidence_unused_sources:{','.join(unused_sources)}")

        return {
            "status": "available" if linkage_qc["pass"] else "available_with_linkage_warnings",
            "dataset_name": self.dataset_name,
            "version": self.version,
            "schema_version": self.manifest.get("schema_version"),
            "producer_module": self.manifest.get("producer_module"),
            "artifact_verification": self.verification,
            "quality_report": self.quality_report,
            "counts": {
                "sources": len(source_rows),
                "referenced_sources": len(source_evidence),
                "nonreaction_explanations": len(enriched_nonreactions),
                "transformation_precedents": len(precedents),
                "condition_records": len(condition_rows),
                "identity_linkage_rows": len(linkage_rows),
                "sample_evidence_groups": len(sample_evidence),
                "compound_evidence_groups": len(compound_evidence),
            },
            "source_evidence": source_evidence,
            "nonreaction_evidence": enriched_nonreactions,
            "transformation_precedents": precedents,
            "condition_compatibility": compatibility_rows,
            "sample_evidence": sample_evidence,
            "compound_evidence": compound_evidence,
            "linkage_qc": linkage_qc,
            "lane_status": {"storage_evidence": "available" if linkage_qc["pass"] else "available_with_linkage_warnings"},
            "warnings": warnings,
            "claim_boundary": "curated storage literature and analytical alternatives support evidence-bounded interpretation; they do not convert relative-area changes into causal reactions",
        }

    def _loaded_count_error(self) -> str | None:
        metrics = self.quality_report.get("metrics") or {}
        checks = {
            "reaction_rows": len(self.reactions) if self.reactions is not None else 0,
            "condition_rows": len(self.conditions) if self.conditions is not None else 0,
            "source_rows": len(self.sources) if self.sources is not None else 0,
            "nonreaction_explanation_rows": len(self.nonreactions) if self.nonreactions is not None else 0,
            "identity_linkage_rows": len(self.identity_linkage) if self.identity_linkage is not None else 0,
        }
        mismatches = {key: {"expected": int(metrics.get(key) or 0), "actual": actual} for key, actual in checks.items() if int(metrics.get(key) or 0) != actual}
        if mismatches:
            self.verification["count_mismatches"] = mismatches
            return "storage_evidence_quality_count_mismatch"
        self.verification["count_verification"] = {"status": "matched", "counts": checks}
        return None

    @staticmethod
    def _resolve_path(registry_path: Path, raw: Any) -> Path | None:
        if not isinstance(raw, str) or not raw.strip():
            return None
        path = Path(raw)
        return path if path.is_absolute() else (registry_path.parent / path).resolve()

    def _read_manifest_table(self, artifacts: dict[str, Any], key: str, fallback: Path | None) -> pd.DataFrame:
        meta = artifacts.get(key) if isinstance(artifacts, dict) else None
        if isinstance(meta, dict) and isinstance(meta.get("path"), str) and self.artifact_dir is not None:
            path = self.artifact_dir / str(meta["path"])
        else:
            path = fallback
        if path is None:
            raise FileNotFoundError(f"storage evidence artifact missing: {key}")
        return read_table(path)

    @staticmethod
    def _verify_manifest(root: Path, manifest: dict[str, Any]) -> dict[str, Any]:
        checked: list[dict[str, Any]] = []
        mismatches: list[dict[str, Any]] = []
        for logical_name, meta in (manifest.get("artifacts") or {}).items():
            if not isinstance(meta, dict):
                continue
            raw_path = meta.get("path")
            expected = meta.get("sha256")
            if not isinstance(raw_path, str) or not isinstance(expected, str):
                continue
            path = root / raw_path
            if not path.exists():
                item = {"logical_name": logical_name, "path": str(path), "expected_sha256": expected, "actual_sha256": None, "match": False}
                checked.append(item)
                mismatches.append(item)
                continue
            actual = sha256_file(path)
            item = {"logical_name": logical_name, "path": str(path), "expected_sha256": expected, "actual_sha256": actual, "match": actual == expected}
            checked.append(item)
            if actual != expected:
                mismatches.append(item)
        return {"status": "matched" if not mismatches else "mismatch", "verification_mode": "manifest_sha256", "checked_files": checked, "mismatches": mismatches}

    @staticmethod
    def _records(frame: pd.DataFrame | None) -> list[dict[str, Any]]:
        if frame is None:
            return []
        rows = frame.where(frame.notna(), None).to_dict(orient="records")
        return [{str(k): _json_scalar(v) for k, v in row.items()} for row in rows]

    @staticmethod
    def _observation_index(records: Iterable[Any]) -> tuple[dict[tuple[str, str], dict[str, Any]], set[str]]:
        grouped: dict[tuple[str, str], dict[str, Any]] = {}
        known_samples: set[str] = set()
        for item in records:
            row = item.model_dump(mode="json") if hasattr(item, "model_dump") else dict(item)
            sample_id = str(row.get("sample_id") or "")
            compound_id = _none_if_blank(row.get("compound_id"))
            if not sample_id:
                continue
            known_samples.add(sample_id)
            if not compound_id:
                continue
            key = (sample_id, compound_id)
            slot = grouped.setdefault(key, {"sample_id": sample_id, "compound_id": compound_id, "before_area_percent": 0.0, "after_area_percent": 0.0, "reported_names": set(), "peak_records": 0})
            slot["peak_records"] += 1
            slot["reported_names"].add(str(row.get("reported_compound_name") or ""))
            area = float(row.get("area_percent") or 0.0)
            if str(row.get("timepoint")) == "before_storage":
                slot["before_area_percent"] += area
            elif str(row.get("timepoint")) == "after_storage":
                slot["after_area_percent"] += area
        normalized: dict[tuple[str, str], dict[str, Any]] = {}
        for key, row in grouped.items():
            before = round(float(row["before_area_percent"]), 6)
            after = round(float(row["after_area_percent"]), 6)
            normalized[key] = {
                "sample_id": row["sample_id"],
                "compound_id": row["compound_id"],
                "before_area_percent": before,
                "after_area_percent": after,
                "delta_area_percent": round(after - before, 6),
                "reported_names": sorted(name for name in row["reported_names"] if name),
                "peak_records": row["peak_records"],
                "measurement_semantics": "relative_gc_ms_peak_area_percent_not_absolute_concentration",
            }
        return normalized, known_samples

    @staticmethod
    def _group_evidence(rows: list[dict[str, Any]], *, key: str) -> list[dict[str, Any]]:
        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            value = row.get(key)
            if value not in (None, ""):
                grouped[str(value)].append(row)
        output = []
        for value in sorted(grouped):
            records = grouped[value]
            output.append({
                key: value,
                "evidence_records": len(records),
                "source_backed_records": sum(bool(row.get("sources")) for row in records),
                "explanation_types": sorted({str(row.get("explanation_type")) for row in records if row.get("explanation_type")}),
                "hypothesis_ids": [str(row.get("hypothesis_id")) for row in records],
            })
        return output


def _duplicates(values: list[str]) -> list[str]:
    seen: set[str] = set()
    duplicates: set[str] = set()
    for value in values:
        if value in seen:
            duplicates.add(value)
        seen.add(value)
    return sorted(duplicates)


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return [str(item) for item in value if str(item).strip()]
    if hasattr(value, "tolist") and not isinstance(value, str):
        try:
            return [str(item) for item in value.tolist() if str(item).strip()]
        except Exception:
            pass
    text = str(value).strip()
    if not text or text.lower() == "nan":
        return []
    for parser in (json.loads, ast.literal_eval):
        try:
            parsed = parser(text)
            if isinstance(parsed, (list, tuple, set)):
                return [str(item) for item in parsed if str(item).strip()]
        except Exception:
            continue
    return [text]


def _as_mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return {str(k): _json_scalar(v) for k, v in value.items()}
    if value is None:
        return {}
    text = str(value).strip()
    if not text or text.lower() == "nan":
        return {}
    for parser in (json.loads, ast.literal_eval):
        try:
            parsed = parser(text)
            if isinstance(parsed, dict):
                return {str(k): _json_scalar(v) for k, v in parsed.items()}
        except Exception:
            continue
    return {}


def _none_if_blank(value: Any) -> str | None:
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except Exception:
        pass
    text = str(value).strip()
    return None if not text or text.lower() == "nan" else text


def _json_scalar(value: Any) -> Any:
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except Exception:
        pass
    if hasattr(value, "item"):
        try:
            return value.item()
        except Exception:
            pass
    return value
