import json
import shutil
from pathlib import Path

import pytest

from project_blends_compute.orchestrator import RunManager
from project_blends_compute.schemas.profiles import PeakRecord, Timepoint
from project_blends_compute.schemas.runs import RunRequest
from project_blends_compute.settings import Settings


REPORTED = "Imidazolo[1,2-a]pyridine, 6-chloro -2-(4-nitrophenyl)-"


@pytest.mark.asyncio
async def test_manual_corrected_identity_can_freeze_without_being_silently_rewritten(tmp_path: Path):
    reference = tmp_path / "data" / "reference"
    reference.mkdir(parents=True)
    source = Path(__file__).parents[2] / "data" / "reference" / "identity_adjudications.json"
    shutil.copy2(source, reference / "identity_adjudications.json")

    settings = Settings(project_root=tmp_path, state_root=tmp_path / "state", artifact_root=tmp_path / "artifacts")
    manager = RunManager(settings)
    records = [
        PeakRecord(sample_id="x", blend_id="x", timepoint=Timepoint.BEFORE_STORAGE, storage_days=0, reported_compound_name=REPORTED, area_percent=5.07),
        PeakRecord(sample_id="x", blend_id="x", timepoint=Timepoint.AFTER_STORAGE, storage_days=28, reported_compound_name=REPORTED, area_percent=4.00),
    ]
    result = await manager.execute(
        RunRequest(
            records=records,
            dataset_id=None,
            include_food_provenance=False,
            include_reaction_intelligence=False,
            include_molecular_screening=False,
            include_quantum_descriptors=False,
        )
    )
    assert not result.errors
    run_dir = manager.store.run_dir(result.run_id)
    freeze = json.loads((run_dir / "identity" / "compound_registry_freeze_manifest.json").read_text(encoding="utf-8"))
    assert freeze["total_reported_identities"] == 1
    assert freeze["manual_corrected"] == 1
    assert freeze["unresolved_pending"] == 0
    assert freeze["release_eligible"] is True

    registry = (run_dir / "identity" / "compound_registry.jsonl").read_text(encoding="utf-8")
    assert REPORTED in registry
    assert "FZIBIOFNSKXAJW-UHFFFAOYSA-N" in registry
    assert (run_dir / "molecular_screening" / "summary.json").exists()
    assert (run_dir / "quantum" / "summary.json").exists()
    assert not (run_dir / "quantum" / "descriptor_results.csv").exists()
