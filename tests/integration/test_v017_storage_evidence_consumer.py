from __future__ import annotations

import json
from pathlib import Path

import pytest

from project_blends_compute.orchestrator import RunManager
from project_blends_compute.reports.evidence import build_evidence_packets
from project_blends_compute.schemas.profiles import PeakRecord, Timepoint
from project_blends_compute.schemas.runs import RunRequest
from project_blends_compute.settings import Settings
from project_blends_compute.storage_curation.builder import StorageReactionCurationBuilder
from project_blends_compute.storage_curation.models import StorageCurationBuildRequest
from project_blends_compute.storage_evidence import StorageEvidenceService


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "data" / "curation" / "storage_reaction_evidence_v1_0_1.source.json"


def _build_storage_dataset(tmp_path: Path) -> tuple[Path, dict]:
    payload = json.loads(SOURCE.read_text(encoding="utf-8"))
    request = StorageCurationBuildRequest.model_validate(payload)
    registry = tmp_path / "reaction_curation" / "benchmark_registry.json"
    builder = StorageReactionCurationBuilder(
        output_root=tmp_path / "reaction_curation" / "curated",
        registry_path=registry,
    )
    result = builder.build(request)
    assert result.ok
    return registry, payload


def _sample_records(compound_id: str) -> list[PeakRecord]:
    records: list[PeakRecord] = []
    for sample in ("lemongrass", "blend_a", "blend_b", "blend_c"):
        records.extend([
            PeakRecord(
                sample_id=sample,
                blend_id=sample,
                timepoint=Timepoint.BEFORE_STORAGE,
                storage_days=0,
                reported_compound_name="test compound",
                area_percent=10.0,
                compound_id=compound_id,
            ),
            PeakRecord(
                sample_id=sample,
                blend_id=sample,
                timepoint=Timepoint.AFTER_STORAGE,
                storage_days=28,
                reported_compound_name="test compound",
                area_percent=5.0,
                compound_id=compound_id,
            ),
        ])
    return records


def test_storage_evidence_v101_consumes_all_curated_artifact_families(tmp_path: Path):
    registry, payload = _build_storage_dataset(tmp_path)
    settings = Settings(
        project_root=tmp_path,
        state_root=tmp_path / "state",
        artifact_root=tmp_path / "artifacts",
        reaction_curation_registry=registry,
    )
    service = StorageEvidenceService(settings)
    canonical = payload["identity_linkage"]
    records = _sample_records(canonical[0]["compound_id"])

    result = service.evaluate(records, canonical)

    assert result["status"] == "available"
    assert result["version"] == "v1.0.1"
    assert result["artifact_verification"]["status"] == "matched"
    assert result["counts"]["sources"] == 13
    assert result["counts"]["nonreaction_explanations"] == 27
    assert result["counts"]["transformation_precedents"] == 2
    assert result["counts"]["condition_records"] == 2
    assert result["counts"]["identity_linkage_rows"] == 49
    assert result["linkage_qc"]["pass"] is True
    assert result["linkage_qc"]["missing_source_links"] == []
    assert result["linkage_qc"]["missing_compound_links"] == []
    assert result["linkage_qc"]["missing_sample_links"] == []


def test_storage_evidence_packets_preserve_noncausal_boundaries(tmp_path: Path):
    registry, payload = _build_storage_dataset(tmp_path)
    settings = Settings(
        project_root=tmp_path,
        state_root=tmp_path / "state",
        artifact_root=tmp_path / "artifacts",
        reaction_curation_registry=registry,
    )
    service = StorageEvidenceService(settings)
    storage = service.evaluate(_sample_records(payload["identity_linkage"][0]["compound_id"]), payload["identity_linkage"])
    packets = build_evidence_packets(
        profile_metrics=[],
        reaction_evaluations=[],
        occurrences=[],
        identity_compounds=[],
        storage_evidence=storage,
    )
    storage_packets = [p for p in packets if p.get("evidence_domain") == "storage_evidence"]

    assert len(storage_packets) == 29
    contamination = next(p for p in storage_packets if p.get("hypothesis_id") == "sre-v1-lemongrass-eugenol-sample-contamination")
    assert contamination["claim_class"] == "HYPOTHESIZED"
    assert contamination["confidence"] is None
    unresolved = next(p for p in storage_packets if p.get("hypothesis_id") == "sre-v1-blend-a-thymoquinone-appearance-unresolved")
    assert unresolved["claim_class"] == "UNRESOLVED"
    caryophyllene = next(p for p in storage_packets if p.get("reaction_id") == "sre-v1-caryophyllene-to-caryophyllene-oxide-001")
    assert caryophyllene["condition_compatibility"] == "low"
    assert "storage_condition_mismatch:low" in caryophyllene["contradictory_evidence"]
    assert "does_not_establish_conversion" in caryophyllene["claim_boundary"]


def test_v101_source_keeps_caryophyllene_retention_linked_to_caryophyllene():
    payload = json.loads(SOURCE.read_text(encoding="utf-8"))
    rows = {row["hypothesis_id"]: row for row in payload["nonreaction_explanations"]}
    for hypothesis_id in (
        "sre-v1-blend-b-caryophyllene-retention-shift",
        "sre-v1-blend-c-caryophyllene-retention-shift",
    ):
        row = rows[hypothesis_id]
        assert row["compound_id"] == "cmp-dc8d031a631106c2"
        assert row["compound_name"] == "(-)-Caryophyllene"


@pytest.mark.asyncio
async def test_full_run_surfaces_storage_evidence_into_report_and_chemrag(tmp_path: Path):
    registry, _ = _build_storage_dataset(tmp_path)
    settings = Settings(
        project_root=tmp_path,
        state_root=tmp_path / "state",
        artifact_root=tmp_path / "artifacts",
        reaction_curation_registry=registry,
    )
    manager = RunManager(settings)
    result = await manager.execute(RunRequest(
        dataset_id="project_blends_reported_v1",
        include_food_provenance=False,
        include_reaction_intelligence=False,
        include_molecular_screening=False,
        include_quantum_descriptors=False,
    ))

    report = json.loads(Path(result.report_path).read_text(encoding="utf-8"))
    storage = report["storage_evidence"]
    assert storage["status"] == "available"
    assert storage["version"] == "v1.0.1"
    assert storage["counts"]["sources"] == 13
    assert storage["counts"]["nonreaction_explanations"] == 27
    assert storage["linkage_qc"]["pass"] is True
    storage_packets = [p for p in report["evidence_packets"] if p.get("evidence_domain") == "storage_evidence"]
    assert len(storage_packets) == 29

    run_dir = manager.store.run_dir(result.run_id)
    assert (run_dir / "storage_evidence" / "source_evidence.csv").exists()
    assert (run_dir / "storage_evidence" / "nonreaction_evidence.csv").exists()
    assert (run_dir / "storage_evidence" / "transformation_precedents.csv").exists()
    assert (run_dir / "storage_evidence" / "condition_compatibility.csv").exists()
    rag = json.loads(Path(result.rag_export_path).read_text(encoding="utf-8"))
    assert rag["sections"]["storage_evidence"]["version"] == "v1.0.1"
    assert any(p.get("evidence_domain") == "storage_evidence" for p in rag["sections"]["evidence_packets"])


def test_release_validation_requires_consumed_storage_evidence(tmp_path: Path):
    from project_blends_compute.artifacts.store import ArtifactStore
    from project_blends_compute.artifacts.validation import validate_run_for_release

    store = ArtifactStore(tmp_path / "artifacts")
    run_id = "pb-storage-release-test"
    report = {
        "identity": {
            "compounds": [{"compound_id": "cmp-test", "resolved_identity": True}],
            "unresolved_pending_count": 0,
            "unresolved_count": 0,
            "excluded_unresolved_count": 0,
            "manual_corrected_count": 0,
            "conflict_count": 0,
        },
        "uncertainty": {
            "lane_status": {
                "pipeline_fooddb": "available",
                "rxn_bridge": "available",
                "reaction_curation": "available",
                "storage_evidence": "available",
            }
        },
        "reactions": {"evaluations": []},
        "storage_evidence": {
            "status": "available",
            "dataset_name": "storage_reaction_evidence",
            "version": "v1.0.1",
            "counts": {"sources": 13, "nonreaction_explanations": 27, "transformation_precedents": 2},
            "linkage_qc": {"pass": True},
        },
        "evidence_packets": [{"evidence_domain": "storage_evidence", "claim_id": "claim-storage"}],
    }
    with store.create_bundle(run_id) as bundle:
        bundle.write_json("identity/compound_registry.json", [{}], logical_name="compound_registry")
        bundle.write_json("profiles/profile_metrics.json", [{}], logical_name="profile_metrics")
        bundle.write_json("storage_evidence/storage_evidence_summary.json", report["storage_evidence"], logical_name="storage_evidence_summary")
        bundle.write_json("reports/integrated_report.json", report, logical_name="integrated_report")
        bundle.write_json("rag/latest_subsystem_rag_export.json", {}, logical_name="chemrag_export")
        bundle.finalize(status="complete")

    result = validate_run_for_release(store, run_id)
    assert result["strict_pass"] is True
    storage_check = next(check for check in result["checks"] if check["name"] == "storage_evidence_consumption")
    assert storage_check["pass"] is True
    assert storage_check["source_rows"] == 13
    assert storage_check["nonreaction_rows"] == 27
    assert storage_check["storage_evidence_packets"] == 1


def test_storage_evidence_fails_closed_when_manifest_artifact_hash_changes(tmp_path: Path):
    registry, _ = _build_storage_dataset(tmp_path)
    reg = json.loads(registry.read_text(encoding="utf-8"))
    active = reg["storage_reaction_evidence"]["active_version"]
    reactions_path = Path(reg["storage_reaction_evidence"]["versions"][active]["reactions_path"])
    artifact_dir = reactions_path.parent
    manifest = json.loads((artifact_dir / "manifest.json").read_text(encoding="utf-8"))
    nonreaction_path = artifact_dir / manifest["artifacts"]["nonreaction_explanations"]["path"]
    nonreaction_path.write_bytes(nonreaction_path.read_bytes() + b"\n")

    service = StorageEvidenceService(Settings(
        project_root=tmp_path,
        state_root=tmp_path / "state",
        artifact_root=tmp_path / "artifacts",
        reaction_curation_registry=registry,
    ))
    assert service.available is False
    assert service.error == "storage_evidence_artifact_hash_mismatch"


def test_storage_evidence_linkage_qc_surfaces_missing_source_reference(tmp_path: Path):
    payload = json.loads(SOURCE.read_text(encoding="utf-8"))
    payload["version"] = "v1.0.1-bad-source-test"
    payload["nonreaction_explanations"][0]["evidence_source_ids"] = ["missing-source-id"]
    request = StorageCurationBuildRequest.model_validate(payload)
    registry = tmp_path / "reaction_curation" / "benchmark_registry.json"
    StorageReactionCurationBuilder(
        output_root=tmp_path / "reaction_curation" / "curated",
        registry_path=registry,
    ).build(request)
    service = StorageEvidenceService(Settings(
        project_root=tmp_path,
        state_root=tmp_path / "state",
        artifact_root=tmp_path / "artifacts",
        reaction_curation_registry=registry,
    ))
    result = service.evaluate(_sample_records(payload["identity_linkage"][0]["compound_id"]), payload["identity_linkage"])
    assert result["status"] == "available_with_linkage_warnings"
    assert result["linkage_qc"]["pass"] is False
    assert result["linkage_qc"]["missing_source_links"][0]["source_id"] == "missing-source-id"
    assert "storage_evidence_linkage_qc_failed" in result["warnings"]
