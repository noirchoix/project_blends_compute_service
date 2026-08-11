from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from bootstrap_hf_repro import bootstrap  # noqa: E402
from collect_hf_repro_bundle import collect  # noqa: E402
from hf_repro_common import audit_bundle  # noqa: E402


def _write(path: Path, value: str = "x") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8")
    return path


def _json(path: Path, payload: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def _fixture(tmp_path: Path) -> Path:
    rxn = tmp_path / "rxn"
    _write(rxn / "reaction_framework" / "__init__.py")
    _write(rxn / "pipelines" / "__init__.py")
    _write(rxn / "pipelines" / "taxonomy_bridge" / "feature_builder.py", "VALUE = 1\n")
    _write(
        rxn / "pipelines" / "taxonomy_bridge" / "TAXONOMY_BRIDGE_DEPLOYMENT_README.md",
        r"Local deployment example: C:\Users\developer\taxonomy\model.txt" + "\n",
    )
    art = rxn / "data" / "rxn_artifacts"
    uspto = art / "uspto_templates" / "curated" / "v1_original_mapped_10k"
    for name in ("manifest.json", "templates.parquet", "template_stats.json", "screening.duckdb"):
        _write(uspto / name, name)
    dess = art / "dess_physics" / "staging" / "dess_physics.duckdb"
    _write(dess, "dess")
    tax = art / "taxonomy_coconut" / "curated" / "v1"
    for name in (
        "model_superclass.txt", "model_class.txt", "metadata_superclass.json",
        "metadata_class.json", "config.normalized.json", "classes_lookup.json"
    ):
        _write(tax / name, name)
    registry = {
        "uspto_templates": {"active_version": "v1_original_mapped_10k", "versions": {"v1_original_mapped_10k": {
            "artifact_root": str(uspto), "manifest": str(uspto / "manifest.json"),
            "templates": str(uspto / "templates.parquet"), "template_stats": str(uspto / "template_stats.json"),
            "duckdb": str(uspto / "screening.duckdb"), "schema_version": "uspto.templates.v1"
        }}},
        "dess_physics": {"active_version": "v1", "versions": {"v1": {
            "duckdb": str(dess), "schema_version": "dess.physics.v1", "model_version": "dess-test",
            "checkpoint": str(tmp_path / "huge_training_checkpoint_not_needed.pt")
        }}},
        "taxonomy_coconut": {"active_version": "v1", "versions": {"v1": {
            "model_superclass": str(tax / "model_superclass.txt"), "model_class": str(tax / "model_class.txt"),
            "metadata_superclass": str(tax / "metadata_superclass.json"), "metadata_class": str(tax / "metadata_class.json"),
            "config_normalized": str(tax / "config.normalized.json"), "classes_lookup": str(tax / "classes_lookup.json"),
            "schema_version": "taxonomy.coconut.v1", "model_version": "lightgbm_coconut_hierarchy_v1"
        }}}
    }
    rxn_registry = _json(art / "registry" / "artifact_registry.json", registry)

    rc = tmp_path / "rc"
    rcart = rc / "data" / "rxn_artifacts" / "reaction_curation" / "curated" / "storage_reaction_evidence" / "v1.0.1"
    for name in (
        "storage_reactions.parquet", "storage_condition_context.parquet", "storage_evidence_sources.parquet",
        "nonreaction_explanations.parquet", "identity_linkage.parquet", "manifest.json", "quality_report.json"
    ):
        _write(rcart / name, name)
    rc_registry = _json(
        rc / "data" / "rxn_artifacts" / "reaction_curation" / "benchmark_registry.json",
        {"storage_reaction_evidence": {"active_version": "v1.0.1", "versions": {"v1.0.1": {
            "dataset_name": "storage_reaction_evidence", "dataset_kind": "storage_reaction_evidence", "version": "v1.0.1",
            "reactions_path": str(rcart / "storage_reactions.parquet"),
            "conditions_path": str(rcart / "storage_condition_context.parquet"),
            "steps_path": None, "role_assignments_path": None,
            "schema_version": "storage.v1", "producer_module": "fixture", "build_timestamp_utc": "2026-08-10T00:00:00Z"
        }}}},
    )

    food = tmp_path / "food"
    db = _write(food / "serving.duckdb", "db")
    fl = _write(food / "curated_food_lookup.parquet", "food")
    cl = _write(food / "curated_compound_lookup.parquet", "compound")
    edges = _write(food / "curated_food_compound_content.parquet", "edges")

    return _json(tmp_path / "paths.json", {
        "reaction_curation": {"reaction_curation_project_root": str(rc), "reaction_curation_registry": str(rc_registry)},
        "rxn_bridge": {"rxn_bridge_project_root": str(rxn), "rxn_artifact_registry": str(rxn_registry), "rxn_template_artifact_root": str(uspto)},
        "fooddb": {"fooddb_db_path": str(db), "fooddb_food_lookup_path": str(fl), "fooddb_compound_lookup_path": str(cl), "fooddb_edges_path": str(edges)},
    })


def test_collects_minimal_portable_runtime_closure(tmp_path: Path) -> None:
    manifest = _fixture(tmp_path)
    bundle = tmp_path / "bundle"
    result = collect(ROOT, manifest, bundle)
    assert result["ok"] is True
    portable = json.loads((bundle / "rxn_bridge_runtime/data/rxn_artifacts/registry/artifact_registry.json").read_text())
    assert set(portable) == {"uspto_templates", "dess_physics", "taxonomy_coconut"}
    assert "checkpoint" not in portable["dess_physics"]["versions"]["v1"]
    assert (bundle / "reaction_curation_runtime/data/rxn_artifacts/reaction_curation/curated/storage_reaction_evidence/v1.0.1/manifest.json").exists()
    assert (bundle / "fooddb/serving.duckdb").exists()
    assert not (
        bundle / "rxn_bridge_runtime/pipelines/taxonomy_bridge/TAXONOMY_BRIDGE_DEPLOYMENT_README.md"
    ).exists()


def test_offline_bootstrap_verifies_and_writes_machine_local_manifest(tmp_path: Path) -> None:
    source_manifest = _fixture(tmp_path / "source")
    source_bundle = tmp_path / "source_bundle"
    collect(ROOT, source_manifest, source_bundle)
    clean_project = tmp_path / "clean_project"
    (clean_project / "config").mkdir(parents=True)
    result = bootstrap(
        clean_project,
        Path("data_hf"),
        Path("config/path_manifest.local.json"),
        source_dir=source_bundle,
    )
    assert result["ok"] is True
    payload = json.loads((clean_project / "config/path_manifest.local.json").read_text())
    assert Path(payload["rxn_bridge"]["rxn_bridge_project_root"]).is_absolute()
    assert Path(payload["reaction_curation"]["reaction_curation_registry"]).exists()
    assert payload["runtime"]["xtb_executable"] is None


def test_bootstrap_rejects_corrupted_bundle_file(tmp_path: Path) -> None:
    source_manifest = _fixture(tmp_path / "source")
    source_bundle = tmp_path / "source_bundle"
    collect(ROOT, source_manifest, source_bundle)
    target = source_bundle / "fooddb" / "serving.duckdb"
    target.write_text("corrupted", encoding="utf-8")
    clean_project = tmp_path / "clean_project"
    (clean_project / "config").mkdir(parents=True)
    with pytest.raises(RuntimeError, match="Artifact verification failed"):
        bootstrap(
            clean_project,
            Path("data_hf"),
            Path("config/path_manifest.local.json"),
            source_dir=source_bundle,
        )


def test_bootstrap_rejects_modified_reproducibility_manifest(tmp_path: Path) -> None:
    source_manifest = _fixture(tmp_path / "source")
    source_bundle = tmp_path / "source_bundle"
    collect(ROOT, source_manifest, source_bundle)
    manifest_path = source_bundle / "REPRODUCIBILITY_MANIFEST.json"
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    payload["locked_run_id"] = "tampered-run"
    manifest_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    clean_project = tmp_path / "clean_project"
    (clean_project / "config").mkdir(parents=True)
    with pytest.raises(RuntimeError, match="manifest digest verification failed"):
        bootstrap(
            clean_project,
            Path("data_hf"),
            Path("config/path_manifest.local.json"),
            source_dir=source_bundle,
        )


def test_collect_fooddb_accepts_duckdb_with_stale_optional_parquet_path(tmp_path: Path) -> None:
    manifest = _fixture(tmp_path)
    payload = json.loads(manifest.read_text())
    payload["fooddb"]["fooddb_food_lookup_path"] = str(tmp_path / "food" / "does_not_exist.parquet")
    manifest.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    bundle = tmp_path / "bundle"
    result = collect(ROOT, manifest, bundle)

    assert result["ok"] is True
    assert (bundle / "fooddb/serving.duckdb").exists()
    assert not (bundle / "fooddb/curated_food_lookup.parquet").exists()

    clean_project = tmp_path / "clean_project"
    (clean_project / "config").mkdir(parents=True)
    bootstrap(
        clean_project,
        Path("data_hf"),
        Path("config/path_manifest.local.json"),
        source_dir=bundle,
    )
    local = json.loads((clean_project / "config/path_manifest.local.json").read_text())
    assert Path(local["fooddb"]["fooddb_db_path"]).exists()
    assert local["fooddb"]["fooddb_food_lookup_path"] is None


def test_collect_fooddb_requires_complete_parquet_fallback_without_duckdb(tmp_path: Path) -> None:
    manifest = _fixture(tmp_path)
    payload = json.loads(manifest.read_text())
    payload["fooddb"]["fooddb_db_path"] = str(tmp_path / "food" / "missing.duckdb")
    payload["fooddb"]["fooddb_food_lookup_path"] = str(tmp_path / "food" / "missing_food.parquet")
    manifest.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    with pytest.raises(FileNotFoundError, match="existing serving DuckDB or all three curated Parquet tables"):
        collect(ROOT, manifest, tmp_path / "bundle")


def test_bundle_contains_mixed_license_metadata_and_notices(tmp_path: Path) -> None:
    manifest = _fixture(tmp_path)
    bundle = tmp_path / "bundle"
    result = collect(ROOT, manifest, bundle)
    assert result["audit"]["pass"] is True

    licenses = json.loads((bundle / "ARTIFACT_LICENSES.json").read_text(encoding="utf-8"))
    assert licenses["repository_license_metadata"] == "other"
    by_id = {row["component_id"]: row for row in licenses["components"]}
    assert by_id["dess_physics"]["license_id"] == "DESRES-Data-Sets-License"
    assert by_id["taxonomy_coconut"]["license_id"] == "CC0-1.0"
    assert by_id["fooddb"]["license_id"] == "CC-BY-NC-4.0"
    assert by_id["fooddb"]["commercial_use_requires_permission"] is True
    assert (bundle / "LICENSES/DESS_DESRES_DATA_SETS_LICENSE.txt").exists()
    assert (bundle / "LICENSES/RXN_UTILS_APACHE_2_0.txt").exists()
    assert (bundle / "LICENSES/FOODB_CC_BY_NC_4_0_NOTICE.txt").exists()


def test_copy_summary_is_portable_and_audit_has_no_local_path_leak(tmp_path: Path) -> None:
    manifest = _fixture(tmp_path)
    bundle = tmp_path / "bundle"
    collect(ROOT, manifest, bundle)
    payload = json.loads((bundle / "REPRODUCIBILITY_MANIFEST.json").read_text(encoding="utf-8"))
    destinations = [row["destination"] for row in payload["copy_summary"]]
    assert destinations
    assert all(not Path(value).is_absolute() for value in destinations)
    audit = audit_bundle(bundle)
    assert audit["pass"] is True
    assert audit["machine_local_path_leaks"] == []
    assert audit["forbidden_paths"] == []
    assert all(audit["runtime_presence"].values())
