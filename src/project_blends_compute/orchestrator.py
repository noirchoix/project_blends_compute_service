from __future__ import annotations

import asyncio
import json
from importlib.resources import files
from collections import defaultdict
from pathlib import Path
from typing import Any

from project_blends_compute.artifacts import ArtifactStore, RunRegistry
from project_blends_compute.artifacts.formats import write_records_table
from project_blends_compute.identity import IdentityService, build_canonical_entities, build_structure_qc, reported_name_crosswalk
from project_blends_compute.profiles import ProfileRepository, ProfileService
from project_blends_compute.provenance import FoodChemMLAdapter, FoodDBAdapter, ProvenanceService
from project_blends_compute.quantum import QuantumService
from project_blends_compute.reactions import ReactionCurationAdapter, ReactionService, RxnBridgeAdapter
from project_blends_compute.reports import ReportBuilder, build_evidence_packets
from project_blends_compute.schemas.identity import IdentityResolveItem, IdentityResolveRequest
from project_blends_compute.schemas.profiles import PeakRecord, ProfileAnalysisRequest, ProfileIngestRequest, Timepoint
from project_blends_compute.schemas.provenance import ProvenanceEvaluateRequest, ProvenanceQuery
from project_blends_compute.schemas.quantum import QuantumJobRequest, QuantumMolecule, QuantumTask
from project_blends_compute.schemas.reactions import ReactionEvaluateRequest, ReactionGenerateRequest
from project_blends_compute.schemas.runs import RunRequest, RunStatus, RunSummary
from project_blends_compute.settings import Settings
from project_blends_compute.supporting import SupportingEvidenceService
from project_blends_compute.supporting.taxonomy_aggregation import aggregate_taxonomy_profiles
from project_blends_compute.storage_evidence import StorageEvidenceService
from project_blends_compute.uncertainty import UncertaintyService
from project_blends_compute.utils import canonical_json_bytes, dedupe_keep_order, normalize_name, stable_hash, utc_now_iso


class RunManager:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        settings.ensure_runtime_dirs()
        self.store = ArtifactStore(settings.artifact_root)
        self.registry = RunRegistry(settings.state_root / "run_registry.json")
        self.profiles = ProfileRepository(settings.state_root / "profile_datasets")
        self.identity = IdentityService(settings)
        self.profile_service = ProfileService(self.profiles)
        self.fooddb = FoodDBAdapter(settings)
        self.foodchem = FoodChemMLAdapter(settings)
        self.provenance = ProvenanceService(self.fooddb, self.foodchem)
        self.rxn_bridge = RxnBridgeAdapter(settings)
        self.reaction_curation = ReactionCurationAdapter(settings)
        self.reactions = ReactionService(self.rxn_bridge, self.reaction_curation)
        self.quantum = QuantumService(settings)
        self.supporting = SupportingEvidenceService(settings)
        self.storage_evidence = StorageEvidenceService(settings)
        self.uncertainty = UncertaintyService()
        self.reporter = ReportBuilder()

    def get(self, run_id: str) -> RunSummary:
        record = self.registry.get(run_id)
        if not record:
            raise KeyError(run_id)
        return RunSummary.model_validate({"run_id": run_id, **record})

    def list(self) -> list[dict[str, Any]]:
        return self.registry.list()

    async def execute(self, request: RunRequest) -> RunSummary:
        run_id = self.store.new_run_id()
        now = utc_now_iso()
        base = RunSummary(run_id=run_id, status=RunStatus.RUNNING, created_at_utc=now, updated_at_utc=now)
        self.registry.upsert(run_id, base.model_dump(mode="json", exclude={"run_id"}))
        warnings: list[str] = []
        stages: dict[str, dict[str, Any]] = {}
        try:
            records = list(request.records or self._load_profile_dataset(request.dataset_id or "project_blends_reported_v1"))
            identity_response = await self._identity_stage(records, request.resolve_identities_online, request.refresh_identities)
            identity_payload = identity_response.model_dump(mode="json")
            records = self._decorate_records(records, identity_payload["compounds"])
            canonical_entities = build_canonical_entities(identity_payload["compounds"])
            structure_qc = build_structure_qc(canonical_entities)
            identity_payload["canonical_entity_count"] = len(canonical_entities)
            identity_payload["structure_qc_summary"] = {
                key: value for key, value in structure_qc.items() if key != "records"
            }
            identity_payload["reported_identity_label_count"] = len(identity_payload["compounds"])
            stages["identity"] = {
                "status": "complete",
                "resolved": sum(bool(c.get("resolved_identity")) for c in identity_payload["compounds"]),
                "reported_identity_labels": len(identity_payload["compounds"]),
                "canonical_entities": len(canonical_entities),
                "automatic_resolved": sum(c.get("adjudication_status") == "resolved" for c in identity_payload["compounds"]),
                "manual_corrected": identity_payload.get("manual_corrected_count", 0),
                "excluded_unresolved": identity_payload.get("excluded_unresolved_count", 0),
                "unresolved_pending": identity_payload.get("unresolved_pending_count", identity_payload["unresolved_count"]),
                "frozen_registry_used": identity_payload.get("frozen_registry_used_count", 0),
                "structure_qc_records": structure_qc.get("entities_with_qc_records", 0),
            }

            profile_response = self.profile_service.analyse(ProfileAnalysisRequest(records=records, identity_key="compound_id"))
            profile_payload = profile_response.model_dump(mode="json")
            stages["profiles"] = {"status": "complete", "samples": len(profile_payload["metrics"])}

            provenance_payload: dict[str, Any] = {"ok": True, "occurrences": [], "exploratory_predictions": [], "unresolved_queries": [], "lane_status": {"pipeline_fooddb": "disabled", "foodchem_ml": "disabled"}}
            if request.include_food_provenance:
                queries = self._provenance_queries(records)
                provenance_response = await self.provenance.evaluate(ProvenanceEvaluateRequest(queries=queries, include_exploratory_predictions=request.include_exploratory_foodchem_ml))
                provenance_payload = provenance_response.model_dump(mode="json")
                if provenance_payload["lane_status"].get("pipeline_fooddb") != "available":
                    warnings.append(self.fooddb.load_error or "pipeline_fooddb unavailable; configure curated artifact paths")
            stages["provenance"] = {"status": provenance_payload["lane_status"].get("pipeline_fooddb", "disabled"), "occurrences": len(provenance_payload["occurrences"])}

            reaction_payload = {"samples": {}, "screening": [], "evaluations": [], "alternatives": [], "analytical_ambiguities": [], "lane_status": {"rxn_bridge": "disabled", "reaction_curation": "disabled"}}
            if request.include_reaction_intelligence:
                reaction_payload = self._reaction_stage(records, request.storage_context)
                for lane, status in reaction_payload["lane_status"].items():
                    if lane in request.strict_lanes and status != "available":
                        raise RuntimeError(f"Required lane unavailable: {lane}")
                warnings.extend(reaction_payload.get("warnings", []))
            stages["reactions"] = {"status": "complete", "candidates": sum(len(v.get("candidates", [])) for v in reaction_payload["samples"].values()), "evaluations": len(reaction_payload["evaluations"])}

            storage_evidence_payload = self.storage_evidence.evaluate(records, canonical_entities)
            warnings.extend(storage_evidence_payload.get("warnings", []))
            stages["storage_evidence"] = {
                "status": storage_evidence_payload.get("status", "unavailable"),
                "dataset_name": storage_evidence_payload.get("dataset_name"),
                "version": storage_evidence_payload.get("version"),
                **storage_evidence_payload.get("counts", {}),
                "linkage_qc_pass": storage_evidence_payload.get("linkage_qc", {}).get("pass", False),
            }

            supporting_payload = self.supporting.evaluate(canonical_entities)
            taxonomy_aggregation = aggregate_taxonomy_profiles(
                [record.model_dump(mode="json") for record in records],
                supporting_payload.get("evidence", []),
            )
            supporting_payload["taxonomy_profile_aggregation"] = taxonomy_aggregation
            warnings.extend(supporting_payload.get("warnings", []))
            stages["supporting_evidence"] = {
                "status": "complete",
                "records": len(supporting_payload.get("evidence", [])),
                "canonical_entities": len(canonical_entities),
                "taxonomy_composition_rows": len(taxonomy_aggregation.get("composition_rows", [])),
                "lanes": supporting_payload.get("lane_status", {}),
                "execution": supporting_payload.get("execution", {}),
            }

            include_screening = (
                request.include_quantum_descriptors
                if request.include_molecular_screening is None
                else request.include_molecular_screening
            )
            molecular_screening_payload = {
                "results": [],
                "lane_status": {"rdkit_screening": "disabled"},
                "claim_boundary": "deterministic cheminformatics screening only; not quantum chemistry",
            }
            if include_screening:
                molecular_screening_payload = self._molecular_screening_stage(canonical_entities)
            stages["molecular_screening"] = {
                "status": "complete" if include_screening else "disabled",
                "rdkit_results": len(molecular_screening_payload["results"]),
            }

            quantum_payload = self._quantum_stage(canonical_entities, request.queue_external_quantum)
            stages["quantum"] = {
                "status": "queued" if quantum_payload["queued_jobs"] else "not_run",
                "completed_results": len(quantum_payload["results"]),
                "queued_jobs": len(quantum_payload["queued_jobs"]),
            }

            lane_status = {
                "identity": "available",
                "profiles": "available",
                "pipeline_fooddb": provenance_payload["lane_status"].get("pipeline_fooddb", "disabled"),
                "foodchem_ml": provenance_payload["lane_status"].get("foodchem_ml", "disabled"),
                **reaction_payload.get("lane_status", {}),
                **storage_evidence_payload.get("lane_status", {}),
                **supporting_payload.get("lane_status", {}),
                **molecular_screening_payload.get("lane_status", {}),
                **quantum_payload.get("lane_status", {}),
            }
            uncertainty_payload = self.uncertainty.aggregate(
                compounds=identity_payload["compounds"],
                profile_metrics=profile_payload["metrics"],
                occurrences=provenance_payload["occurrences"],
                unresolved_provenance=provenance_payload["unresolved_queries"],
                reaction_evaluations=reaction_payload["evaluations"],
                storage_evidence=storage_evidence_payload,
                lane_status=lane_status,
            )
            evidence_packets = build_evidence_packets(
                profile_metrics=profile_payload["metrics"],
                reaction_evaluations=reaction_payload["evaluations"],
                occurrences=provenance_payload["occurrences"],
                identity_compounds=identity_payload["compounds"],
                storage_evidence=storage_evidence_payload,
            )
            report = self.reporter.integrated_report(
                run_id=run_id,
                request=request.model_dump(mode="json"),
                identity=identity_payload,
                profiles=profile_payload,
                provenance=provenance_payload,
                reactions=reaction_payload,
                storage_evidence=storage_evidence_payload,
                supporting=supporting_payload,
                molecular_screening=molecular_screening_payload,
                quantum=quantum_payload,
                uncertainty=uncertainty_payload,
                evidence_packets=evidence_packets,
                warnings=warnings,
            )
            rag_export = self.reporter.rag_export(report)
            final_status = RunStatus.COMPLETE_WITH_WARNINGS if warnings else RunStatus.COMPLETE
            with self.store.create_bundle(run_id, metadata={"run_name": request.run_name, "dataset_id": request.dataset_id}) as bundle:
                bundle.write_json("identity/identity_resolution.json", identity_payload, logical_name="identity_resolution", schema_version="v1")
                self._write_table_bundle(bundle, "identity/compound_registry", canonical_entities, "compound_registry")
                self._write_table_bundle(bundle, "identity/reported_identity_registry", identity_payload["compounds"], "reported_identity_registry")
                crosswalk = reported_name_crosswalk(identity_payload["compounds"])
                isomer_groups = [{"compound_id": c["compound_id"], "isomer_group_id": c.get("isomer_group_id"), "stereochemistry_status": c.get("stereochemistry_status"), "tautomer_parent_id": c.get("tautomer_parent_id")} for c in identity_payload["compounds"] if c.get("isomer_group_id") or c.get("tautomer_parent_id")]
                candidates = [{"compound_id": c["compound_id"], "reported_name": c["reported_name"], **candidate} for c in identity_payload["compounds"] for candidate in c.get("candidate_identity_set", [])]
                conflicts = [{"compound_id": c["compound_id"], "reported_name": c["reported_name"], "conflict_flags": c.get("conflict_flags", [])} for c in identity_payload["compounds"] if c.get("conflict_flags")]
                manual = [c for c in identity_payload["compounds"] if c.get("manual_review_status") == "pending"]
                excluded = [c for c in identity_payload["compounds"] if c.get("adjudication_status") == "excluded_unresolved"]
                adjudicated = [c for c in identity_payload["compounds"] if c.get("adjudication_status") in {"manual_corrected", "excluded_unresolved"}]
                peak_assignment_ambiguities = [
                    {
                        "sample_id": r.sample_id,
                        "blend_id": r.blend_id,
                        "timepoint": r.timepoint.value if hasattr(r.timepoint, "value") else str(r.timepoint),
                        "source_row": r.source_row,
                        "retention_time_min": r.retention_time_min,
                        "library_match_quality": r.library_match_quality,
                        "reported_compound_name": r.reported_compound_name,
                        "candidate_names": r.candidate_names,
                        "assignment_status": "ambiguous_library_assignment",
                    }
                    for r in records
                    if len(dedupe_keep_order([name for name in r.candidate_names if name])) > 1
                ]
                blocking_conflicts = [
                    c for c in identity_payload["compounds"]
                    if "cross_source_structure_conflict" in c.get("conflict_flags", [])
                ]
                informational_identity_flags = [
                    c for c in identity_payload["compounds"]
                    if c.get("conflict_flags") and c not in blocking_conflicts
                ]
                unresolved_pending = int(identity_payload.get("unresolved_pending_count", identity_payload["unresolved_count"]))
                excluded_unresolved = int(identity_payload.get("excluded_unresolved_count", 0))
                manual_corrected = int(identity_payload.get("manual_corrected_count", 0))
                identity_qc = {
                    "schema_version": "project_blends_identity_qc.v6",
                    "total": len(identity_payload["compounds"]),
                    "total_reported_identity_labels": len(identity_payload["compounds"]),
                    "canonical_entities": len(canonical_entities),
                    "resolved": sum(bool(c.get("resolved_identity")) for c in identity_payload["compounds"]),
                    "automatic_resolved": sum(c.get("adjudication_status") == "resolved" for c in identity_payload["compounds"]),
                    "manual_corrected": manual_corrected,
                    "unresolved": identity_payload["unresolved_count"],
                    "unresolved_pending": unresolved_pending,
                    "excluded_unresolved": excluded_unresolved,
                    "blocking_conflicts": len(blocking_conflicts),
                    "informational_identity_flags": len(informational_identity_flags),
                    "conflicts": len(blocking_conflicts),
                    "manual_review": identity_payload["manual_review_count"],
                    "peak_assignment_ambiguities": len(peak_assignment_ambiguities),
                    "pass": unresolved_pending == 0 and len(blocking_conflicts) == 0,
                }
                freeze_projection = [
                    {
                        "compound_id": c.get("compound_id"),
                        "preferred_name": c.get("preferred_name"),
                        "reported_names": c.get("reported_names", []),
                        "inchikey": c.get("inchikey"),
                        "canonical_smiles": c.get("canonical_smiles"),
                        "downstream_structure_eligible": c.get("downstream_structure_eligible"),
                    }
                    for c in canonical_entities
                ]
                freeze_manifest = {
                    "schema_version": "project_blends.compound_registry.freeze.v1",
                    "registry_version": "project_blends.compound_registry.v1",
                    "identity_policy": "name_first_external_structure_v5_manual_adjudication_freeze",
                    "total_reported_identities": len(identity_payload["compounds"]),
                    "total_reported_identity_labels": len(identity_payload["compounds"]),
                    "canonical_entities": len(canonical_entities),
                    "reported_labels_collapsed_to_existing_entities": len(identity_payload["compounds"]) - len(canonical_entities),
                    "automatic_resolved": sum(c.get("adjudication_status") == "resolved" for c in identity_payload["compounds"]),
                    "manual_corrected": manual_corrected,
                    "excluded_unresolved": excluded_unresolved,
                    "unresolved_pending": unresolved_pending,
                    "blocking_conflicts": len(blocking_conflicts),
                    "adjudication_registry_sha256": identity_payload.get("adjudication_registry_sha256"),
                    "frozen_registry_content_sha256": identity_payload.get("frozen_registry_sha256"),
                    "frozen_registry_used_count": identity_payload.get("frozen_registry_used_count", 0),
                    "registry_content_sha256": stable_hash(canonical_json_bytes(freeze_projection).decode("utf-8")),
                    "release_eligible": unresolved_pending == 0 and len(blocking_conflicts) == 0,
                    "scientific_boundary": "source-reported GC-MS names are preserved in the crosswalk; compound_registry contains one row per canonical molecular entity; manually corrected identities remain explicitly adjudicated",
                }
                self._write_table_bundle(bundle, "identity/reported_name_crosswalk", crosswalk, "reported_name_crosswalk")
                self._write_table_bundle(bundle, "identity/isomer_groups", isomer_groups, "isomer_groups")
                self._write_table_bundle(bundle, "identity/identity_candidates", candidates, "identity_candidates")
                self._write_table_bundle(bundle, "identity/identity_conflicts", conflicts, "identity_conflicts")
                self._write_table_bundle(bundle, "identity/manual_review_queue", manual, "manual_review_queue")
                self._write_table_bundle(bundle, "identity/excluded_identities", excluded, "excluded_identities")
                bundle.write_json("identity/identity_adjudications.json", {"schema_version": "project_blends.identity_adjudications.export.v1", "records": adjudicated}, logical_name="identity_adjudications")
                bundle.write_json("identity/compound_registry_freeze_manifest.json", freeze_manifest, logical_name="compound_registry_freeze_manifest")
                self._write_table_bundle(bundle, "identity/peak_assignment_ambiguities", peak_assignment_ambiguities, "peak_assignment_ambiguities")
                bundle.write_json("identity/identity_qc_report.json", identity_qc, logical_name="identity_qc_report")
                bundle.write_json("identity/structure_qc.json", structure_qc, logical_name="identity_structure_qc")
                bundle.write_json("identity/pubchem_cache/query_results.json", {"schema_version": "project_blends_identity_source_cache.v1", "online_requested": request.resolve_identities_online, "candidates": [row for row in candidates if row.get("source") == "pubchem"]}, logical_name="pubchem_cache_snapshot")
                bundle.write_json(
                    "identity/identity_manifest.json",
                    {
                        "schema_version": "project_blends_identity_manifest.v6",
                        "identity_policy": "name_first_external_structure_v5_manual_adjudication_freeze",
                        "analytical_identity_anchor": "reported_gc_ms_compound_name",
                        "legacy_reported_smiles_trust": "provenance_only_not_identity_evidence",
                        "peak_candidate_names_policy": "peak_assignment_metadata_not_compound_synonyms",
                        "compound_registry_semantics": "one_row_per_canonical_chemical_entity",
                        "reported_name_crosswalk_semantics": "one_row_per_source_reported_gc_ms_identity_label",
                        "lookup_variant_policy": "reported_name_plus_bounded_transcription_variants_plus_verified_database_aliases",
                        "resolution_hierarchy": ["PubChem", "ChEBI", "NIST", "LOTUS/COCONUT", "FoodDB", "manual"],
                        "source_online": request.resolve_identities_online,
                        "refresh_identities": request.refresh_identities,
                        "frozen_registry_source": "data/reference/project_blends_compound_registry_v1.json",
                        "frozen_registry_content_sha256": identity_payload.get("frozen_registry_sha256"),
                        "frozen_registry_used_count": identity_payload.get("frozen_registry_used_count", 0),
                        "alias_provenance_reference": "data/reference/name_lookup_alias_sources.json",
                        "artifacts": ["compound_registry", "reported_identity_registry", "compound_registry_freeze_manifest", "identity_adjudications", "excluded_identities", "reported_name_crosswalk", "isomer_groups", "identity_candidates", "identity_conflicts", "manual_review_queue", "peak_assignment_ambiguities", "pubchem_cache", "identity_qc_report"],
                    },
                    logical_name="identity_manifest",
                )
                self._write_table_bundle(bundle, "profiles/profile_long", [r.model_dump(mode="json") for r in records], "profile_long")
                self._write_table_bundle(bundle, "profiles/profile_metrics", profile_payload["metrics"], "profile_metrics")
                self._write_table_bundle(bundle, "provenance/food_occurrences", provenance_payload["occurrences"], "food_occurrences")
                self._write_table_bundle(bundle, "reactions/reaction_screening", reaction_payload.get("screening", []), "reaction_screening")
                self._write_table_bundle(bundle, "reactions/reaction_analytical_ambiguities", reaction_payload.get("analytical_ambiguities", []), "reaction_analytical_ambiguities")
                self._write_table_bundle(bundle, "reactions/reaction_evaluations", reaction_payload["evaluations"], "reaction_evaluations")
                self._write_table_bundle(bundle, "reactions/alternative_explanations", reaction_payload["alternatives"], "alternative_explanations")
                self._write_table_bundle(bundle, "storage_evidence/source_evidence", storage_evidence_payload.get("source_evidence", []), "storage_source_evidence")
                self._write_table_bundle(bundle, "storage_evidence/nonreaction_evidence", storage_evidence_payload.get("nonreaction_evidence", []), "storage_nonreaction_evidence")
                self._write_table_bundle(bundle, "storage_evidence/transformation_precedents", storage_evidence_payload.get("transformation_precedents", []), "storage_transformation_precedents")
                self._write_table_bundle(bundle, "storage_evidence/condition_compatibility", storage_evidence_payload.get("condition_compatibility", []), "storage_condition_compatibility")
                self._write_table_bundle(bundle, "storage_evidence/sample_evidence", storage_evidence_payload.get("sample_evidence", []), "storage_sample_evidence")
                self._write_table_bundle(bundle, "storage_evidence/compound_evidence", storage_evidence_payload.get("compound_evidence", []), "storage_compound_evidence")
                bundle.write_json("storage_evidence/storage_evidence_summary.json", storage_evidence_payload, logical_name="storage_evidence_summary")
                self._write_table_bundle(bundle, "supporting/supporting_evidence", supporting_payload.get("evidence", []), "supporting_evidence")
                self._write_table_bundle(
                    bundle,
                    "supporting/taxonomy_profile_composition",
                    taxonomy_aggregation.get("composition_rows", []),
                    "taxonomy_profile_composition",
                )
                self._write_table_bundle(
                    bundle,
                    "supporting/taxonomy_profile_shifts",
                    taxonomy_aggregation.get("shift_rows", []),
                    "taxonomy_profile_shifts",
                )
                bundle.write_json(
                    "supporting/taxonomy_profile_aggregation.json",
                    taxonomy_aggregation,
                    logical_name="taxonomy_profile_aggregation",
                )
                bundle.write_json("supporting/supporting_evidence_summary.json", supporting_payload, logical_name="supporting_evidence_summary")
                self._write_table_bundle(bundle, "molecular_screening/rdkit_descriptor_results", molecular_screening_payload["results"], "molecular_screening_rdkit")
                bundle.write_json("molecular_screening/summary.json", molecular_screening_payload, logical_name="molecular_screening_summary")
                self._write_table_bundle(bundle, "quantum/queued_jobs", quantum_payload["queued_jobs"], "quantum_queued_jobs")
                bundle.write_json("quantum/summary.json", quantum_payload, logical_name="quantum_summary")
                bundle.write_json("uncertainty/uncertainty_report.json", uncertainty_payload, logical_name="uncertainty_report")
                bundle.write_json("reports/evidence_packets.json", evidence_packets, logical_name="evidence_packets")
                bundle.write_json("reports/integrated_report.json", report, logical_name="integrated_report")
                bundle.write_text("reports/integrated_report.md", self.reporter.markdown_summary(report), logical_name="integrated_report_markdown", media_type="text/markdown")
                bundle.write_json("rag/latest_subsystem_rag_export.json", rag_export, logical_name="chemrag_export")
                manifest_path = bundle.finalize(status=final_status.value, warnings=warnings)
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            summary = RunSummary(
                run_id=run_id,
                status=final_status,
                created_at_utc=now,
                updated_at_utc=utc_now_iso(),
                completed_at_utc=utc_now_iso(),
                stages=stages,
                warnings=warnings,
                artifacts=manifest.get("artifacts", []),
                report_path=str(self.store.run_dir(run_id) / "reports/integrated_report.json"),
                rag_export_path=str(self.store.run_dir(run_id) / "rag/latest_subsystem_rag_export.json"),
            )
            self.registry.upsert(run_id, summary.model_dump(mode="json", exclude={"run_id"}))
            return summary
        except Exception as exc:
            failed = RunSummary(
                run_id=run_id,
                status=RunStatus.FAILED,
                created_at_utc=now,
                updated_at_utc=utc_now_iso(),
                completed_at_utc=utc_now_iso(),
                stages=stages,
                warnings=warnings,
                errors=[f"{type(exc).__name__}: {exc}"],
            )
            self.registry.upsert(run_id, failed.model_dump(mode="json", exclude={"run_id"}))
            return failed

    def _load_profile_dataset(self, dataset_id: str) -> list[PeakRecord]:
        """Load a registered dataset, bootstrapping only the bundled canonical study.

        Arbitrary dataset IDs remain fail-closed. The special-case bootstrap removes
        the redundant manual seed step for ``project_blends_reported_v1`` while
        preserving the repository/manifest boundary used by the runtime.
        """
        try:
            return self.profiles.load(dataset_id)
        except FileNotFoundError:
            if dataset_id != "project_blends_reported_v1":
                raise

        source = self.settings.project_root / "data" / "raw" / "project_blends_reported_v1.json"
        if not source.exists():
            source = Path(str(files("project_blends_compute").joinpath("resources", "raw", "project_blends_reported_v1.json")))
        if not source.exists():
            raise FileNotFoundError(
                f"Profile dataset not found: {dataset_id}; bundled canonical source also missing: {source}"
            )
        payload = json.loads(source.read_text(encoding="utf-8"))
        if not isinstance(payload, list):
            raise ValueError(f"Bundled profile source must contain a JSON list: {source}")
        records = [PeakRecord.model_validate(row) for row in payload]
        self.profiles.ingest(
            ProfileIngestRequest(
                records=records,
                dataset_id=dataset_id,
                source_document="PROJECT BLENDS.doc",
                replace=False,
            )
        )
        return self.profiles.load(dataset_id)

    async def _identity_stage(self, records: list[PeakRecord], online: bool, force_refresh: bool = False):
        # Batch A policy: the GC-MS reported compound NAME is the analytical identity
        # anchor. The manuscript SMILES were manually attributed later and are known
        # to be unreliable, so they are preserved only as legacy provenance.
        grouped: dict[str, list[PeakRecord]] = {}
        for record in records:
            grouped.setdefault(normalize_name(record.reported_compound_name), []).append(record)

        items: list[IdentityResolveItem] = []
        for rows in grouped.values():
            representative = rows[0]
            legacy_smiles = dedupe_keep_order([r.reported_smiles for r in rows if r.reported_smiles])
            # Peak-specific alternative library assignments remain on PeakRecord and
            # are exported separately. They are NOT synonyms for the compound entity
            # and therefore are intentionally excluded from external identity lookup.
            items.append(
                IdentityResolveItem(
                    reported_name=representative.reported_compound_name,
                    reported_smiles=legacy_smiles[0] if legacy_smiles else None,
                    legacy_reported_smiles=legacy_smiles,
                    retention_time_min=representative.retention_time_min,
                    library_match_quality=max(
                        (r.library_match_quality for r in rows if r.library_match_quality is not None),
                        default=None,
                    ),
                    candidate_names=[],
                    source_row_id=representative.source_row,
                )
            )

        return await self.identity.resolve(
            IdentityResolveRequest(
                items=items,
                online=online,
                force_refresh=force_refresh,
                # Explicitly exclude the undergraduate report SMILES from the
                # resolution evidence lane.
                sources=["pubchem", "chebi", "nist", "lotus_coconut", "fooddb"],
            )
        )

    @staticmethod
    def _decorate_records(records: list[PeakRecord], compounds: list[dict[str, Any]]) -> list[PeakRecord]:
        by_key = {normalize_name(c["reported_name"]): c for c in compounds}
        out: list[PeakRecord] = []
        for record in records:
            payload = record.model_dump(mode="json")
            compound = by_key.get(normalize_name(record.reported_compound_name))
            if compound:
                payload.update({
                    "compound_id": compound["compound_id"],
                    "inchikey": compound.get("inchikey") if compound.get("resolved_identity") else None,
                    "canonical_smiles": compound.get("canonical_smiles") if compound.get("resolved_identity") else None,
                    "identity_confidence": compound.get("resolution_confidence"),
                })
                payload.setdefault("metadata", {})["resolved_identity"] = bool(compound.get("resolved_identity"))
                payload["metadata"]["adjudication_status"] = compound.get("adjudication_status")
                payload["metadata"]["downstream_structure_eligible"] = bool(compound.get("downstream_structure_eligible"))
            out.append(PeakRecord.model_validate(payload))
        return out

    @staticmethod
    def _provenance_queries(records: list[PeakRecord]) -> list[ProvenanceQuery]:
        unique: dict[tuple[str, str, str], ProvenanceQuery] = {}
        for record in records:
            for ingredient in record.plant_components:
                key = (record.sample_id, ingredient, record.compound_id or record.reported_compound_name)
                unique[key] = ProvenanceQuery(sample_id=record.sample_id, ingredient_names=[ingredient], compound_id=record.compound_id, compound_name=record.reported_compound_name, inchikey=record.inchikey)
        return list(unique.values())

    def _reaction_stage(self, records: list[PeakRecord], storage_context: dict[str, Any]) -> dict[str, Any]:
        grouped: dict[str, dict[str, list[dict[str, Any]]]] = defaultdict(lambda: {"before": [], "after": []})
        for record in records:
            key = "before" if record.timepoint == Timepoint.BEFORE_STORAGE else "after"
            grouped[record.sample_id][key].append(record.model_dump(mode="json"))
        samples: dict[str, Any] = {}
        evaluations: list[dict[str, Any]] = []
        alternatives: list[dict[str, Any]] = []
        warnings: list[str] = []
        lane_status = {"rxn_bridge": "available" if self.rxn_bridge.available else "unavailable", "reaction_curation": "available" if self.reaction_curation.available else "unavailable"}
        for sample_id, pair in grouped.items():
            generated = self.reactions.generate(
                ReactionGenerateRequest(
                    sample_id=sample_id,
                    before=pair["before"],
                    after=pair["after"],
                    max_candidates=self.settings.max_reaction_candidates,
                )
            )
            evaluated = self.reactions.evaluate(
                ReactionEvaluateRequest(candidates=generated.candidates, storage_context=storage_context)
            )
            screening_rows = [row.model_dump(mode="json") for row in generated.screening]
            samples[sample_id] = {
                "candidates": [c.model_dump(mode="json") for c in generated.candidates],
                "screening": screening_rows,
                "screened_pairs": len(screening_rows),
                "rejected_pairs": generated.rejected_pairs,
            }
            evaluations.extend(e.model_dump(mode="json") for e in evaluated.evaluations)
            alternatives.extend(a.model_dump(mode="json") for a in generated.alternatives)
            warnings.extend(evaluated.warnings)
        screening = [row for sample in samples.values() for row in sample.get("screening", [])]
        analytical_ambiguities = [
            row for row in screening if row.get("decision") == "redirected_analytical_ambiguity"
        ]
        return {
            "samples": samples,
            "screening": screening,
            "evaluations": evaluations,
            "alternatives": alternatives,
            "analytical_ambiguities": analytical_ambiguities,
            "lane_status": lane_status,
            "warnings": sorted(set(warnings)),
            "screening_summary": {
                "screened_pairs": len(screening),
                "candidate_pairs": sum(row.get("decision") == "candidate" for row in screening),
                "redirected_analytical_ambiguity": len(analytical_ambiguities),
                "rejected_pre_evidence": sum(row.get("decision") == "rejected_pre_evidence" for row in screening),
                "policy": "conservative_storage_chemistry_pre_evidence_gate_v2",
            },
        }

    def _molecular_screening_stage(self, compounds: list[dict[str, Any]]) -> dict[str, Any]:
        molecules = [
            QuantumMolecule(
                compound_id=c.get("compound_id"),
                name=c.get("preferred_name") or c.get("reported_name"),
                smiles=c.get("isomeric_smiles") or c.get("canonical_smiles"),
            )
            for c in compounds
            if c.get("downstream_structure_eligible", c.get("resolved_identity"))
            and (c.get("isomeric_smiles") or c.get("canonical_smiles"))
        ]
        results: list[dict[str, Any]] = []
        for molecule in molecules:
            req = QuantumJobRequest(task=QuantumTask.DESCRIPTORS, engine="rdkit", molecules=[molecule])
            result = self.quantum.run_inline(
                req,
                self.settings.artifact_root / ".scratch" / "rdkit" / (molecule.compound_id or "compound"),
            )
            results.append({"molecule": molecule.model_dump(mode="json"), **result})
        return {
            "schema_version": "project_blends_molecular_screening.v1",
            "results": results,
            "lane_status": {"rdkit_screening": "executed"},
            "claim_boundary": "RDKit descriptors, ETKDG conformers and MMFF/UFF screening are cheminformatics/molecular-mechanics outputs, not quantum chemistry, reaction barriers, kinetics, or shelf-life estimates",
        }

    def _quantum_stage(self, compounds: list[dict[str, Any]], queue_external: bool) -> dict[str, Any]:
        molecules = [
            QuantumMolecule(
                compound_id=c.get("compound_id"),
                name=c.get("preferred_name") or c.get("reported_name"),
                smiles=c.get("isomeric_smiles") or c.get("canonical_smiles"),
            )
            for c in compounds
            if c.get("downstream_structure_eligible", c.get("resolved_identity"))
            and (c.get("isomeric_smiles") or c.get("canonical_smiles"))
        ]
        queued: list[dict[str, Any]] = []
        xtb_available = bool(self.settings.xtb_executable and self.settings.xtb_executable.exists())
        orca_available = bool(self.settings.orca_executable and self.settings.orca_executable.exists())
        if queue_external and xtb_available:
            for molecule in molecules:
                queued.append(
                    self.quantum.submit(
                        QuantumJobRequest(
                            task=QuantumTask.GEOMETRY_OPTIMIZATION,
                            engine="xtb",
                            molecules=[molecule],
                            method="GFN2-xTB",
                            metadata={"stage": "targeted_quantum_preparation", "publication_status": "pending_execution"},
                        )
                    ).model_dump(mode="json")
                )
        return {
            "schema_version": "project_blends_quantum_chemistry.v1",
            "results": [],
            "queued_jobs": queued,
            "lane_status": {
                "xtb": "queued" if queued else ("available_not_run" if xtb_available else "unavailable"),
                "orca": "available_not_run" if orca_available else "unavailable",
            },
            "claim_boundary": "xTB/ORCA results are quantum-chemistry evidence only after successful external-engine execution; queued jobs are not results",
        }

    @staticmethod
    def _write_table_bundle(bundle, relative_stem: str, records: list[dict[str, Any]], logical_name: str) -> None:
        result = write_records_table(records, bundle.path(relative_stem), prefer_parquet=True, write_csv=True, write_jsonl=True)
        for file in result.get("files", []):
            path = Path(file)
            media = {".parquet": "application/vnd.apache.parquet", ".csv": "text/csv", ".jsonl": "application/x-ndjson", ".json": "application/json"}.get(path.suffix, "application/octet-stream")
            bundle.register(path, logical_name=f"{logical_name}{path.suffix}", media_type=media, rows=result.get("rows"))
