from pathlib import Path

import pytest

from project_blends_compute.orchestrator import RunManager
from project_blends_compute.profiles.repository import ProfileRepository
from project_blends_compute.schemas.profiles import PeakRecord, ProfileIngestRequest, Timepoint
from project_blends_compute.schemas.runs import RunRequest, RunStatus
from project_blends_compute.settings import Settings


@pytest.mark.asyncio
async def test_offline_run_completes_with_bounded_lanes(tmp_path: Path):
    settings = Settings(project_root=tmp_path, state_root=tmp_path / "state", artifact_root=tmp_path / "artifacts")
    manager = RunManager(settings)
    records = [
        PeakRecord(sample_id="x", blend_id="x", timepoint=Timepoint.BEFORE_STORAGE, storage_days=0, reported_compound_name="Ethanol", reported_smiles="CCO", area_percent=100),
        PeakRecord(sample_id="x", blend_id="x", timepoint=Timepoint.AFTER_STORAGE, storage_days=28, reported_compound_name="Acetaldehyde", reported_smiles="CC=O", area_percent=100),
    ]
    manager.profiles.ingest(ProfileIngestRequest(records=records, dataset_id="demo"))
    result = await manager.execute(RunRequest(dataset_id="demo", include_food_provenance=False, include_reaction_intelligence=True, include_quantum_descriptors=False))
    assert result.status in {RunStatus.COMPLETE, RunStatus.COMPLETE_WITH_WARNINGS}
    assert result.report_path and Path(result.report_path).exists()
    manager.store.verify_manifest(result.run_id)

@pytest.mark.asyncio
async def test_identity_stage_deduplicates_same_name_across_wrong_legacy_smiles(tmp_path: Path):
    settings = Settings(project_root=tmp_path, state_root=tmp_path / "state", artifact_root=tmp_path / "artifacts")
    manager = RunManager(settings)
    records = [
        PeakRecord(sample_id="x", blend_id="x", timepoint=Timepoint.BEFORE_STORAGE, storage_days=0, reported_compound_name="Eugenol", reported_smiles="CC", area_percent=50),
        PeakRecord(sample_id="x", blend_id="x", timepoint=Timepoint.AFTER_STORAGE, storage_days=28, reported_compound_name="Eugenol", reported_smiles="CCC", area_percent=50),
    ]
    response = await manager._identity_stage(records, online=False)
    assert len(response.compounds) == 1
    assert set(response.compounds[0].legacy_reported_smiles) == {"CC", "CCC"}
