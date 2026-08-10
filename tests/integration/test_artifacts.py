from pathlib import Path

from project_blends_compute.artifacts.store import ArtifactStore


def test_artifact_bundle_is_atomic_and_verified(tmp_path: Path):
    store = ArtifactStore(tmp_path / "artifacts")
    with store.create_bundle("test-run") as bundle:
        bundle.write_json("reports/report.json", {"ok": True}, logical_name="integrated_report")
        bundle.finalize(status="complete")
    manifest = store.load_manifest("test-run")
    assert manifest["run_id"] == "test-run"
    assert (store.run_dir("test-run") / "reports/report.json").exists()
