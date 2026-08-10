import json
from pathlib import Path

from project_blends_compute.orchestrator import RunManager
from project_blends_compute.settings import Settings


def test_builtin_dataset_bootstraps_without_manual_seed(tmp_path: Path):
    source_dir = tmp_path / "data" / "raw"
    source_dir.mkdir(parents=True)
    payload = [
        {
            "sample_id": "lemongrass",
            "blend_id": "lemongrass",
            "timepoint": "before_storage",
            "storage_days": 0,
            "plant_components": ["Cymbopogon citratus"],
            "plant_ratios": {"Cymbopogon citratus": 30.0},
            "reported_compound_name": "Citral",
            "area_percent": 13.1,
        },
        {
            "sample_id": "lemongrass",
            "blend_id": "lemongrass",
            "timepoint": "after_storage",
            "storage_days": 28,
            "plant_components": ["Cymbopogon citratus"],
            "plant_ratios": {"Cymbopogon citratus": 30.0},
            "reported_compound_name": "Eugenol",
            "area_percent": 64.13,
        },
    ]
    (source_dir / "project_blends_reported_v1.json").write_text(json.dumps(payload), encoding="utf-8")
    settings = Settings(project_root=tmp_path, state_root=tmp_path / "state", artifact_root=tmp_path / "artifacts")
    manager = RunManager(settings)
    loaded = manager._load_profile_dataset("project_blends_reported_v1")
    assert len(loaded) == 2
    assert (tmp_path / "state" / "profile_datasets" / "project_blends_reported_v1" / "manifest.json").exists()
