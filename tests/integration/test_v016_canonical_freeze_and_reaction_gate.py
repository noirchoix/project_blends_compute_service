import json
from pathlib import Path

import pytest

from project_blends_compute.orchestrator import RunManager
from project_blends_compute.schemas.runs import RunRequest
from project_blends_compute.settings import Settings


@pytest.mark.asyncio
async def test_bundled_study_collapses_to_49_entities_and_hardens_reaction_gate(tmp_path: Path):
    settings = Settings(project_root=tmp_path, state_root=tmp_path / "state", artifact_root=tmp_path / "artifacts")
    manager = RunManager(settings)
    result = await manager.execute(
        RunRequest(
            dataset_id="project_blends_reported_v1",
            include_food_provenance=False,
            include_reaction_intelligence=True,
            include_molecular_screening=False,
            include_quantum_descriptors=False,
        )
    )
    assert not result.errors
    assert result.stages["identity"]["reported_identity_labels"] == 53
    assert result.stages["identity"]["canonical_entities"] == 49
    assert result.stages["reactions"]["candidates"] == 0
    assert result.stages["reactions"]["evaluations"] == 0

    run_dir = manager.store.run_dir(result.run_id)
    canonical_rows = [json.loads(line) for line in (run_dir / "identity" / "compound_registry.jsonl").read_text(encoding="utf-8").splitlines() if line]
    reported_rows = [json.loads(line) for line in (run_dir / "identity" / "reported_name_crosswalk.jsonl").read_text(encoding="utf-8").splitlines() if line]
    screening = [json.loads(line) for line in (run_dir / "reactions" / "reaction_screening.jsonl").read_text(encoding="utf-8").splitlines() if line]
    ambiguities = [json.loads(line) for line in (run_dir / "reactions" / "reaction_analytical_ambiguities.jsonl").read_text(encoding="utf-8").splitlines() if line]
    assert len(canonical_rows) == 49
    assert len(reported_rows) == 53
    assert len(screening) == 211
    assert len(ambiguities) >= 1
    assert all(row["decision"] != "candidate" for row in screening)
