from pathlib import Path

import pandas as pd

from project_blends_compute.profiles.repository import ProfileRepository
from project_blends_compute.schemas.profiles import PeakRecord, ProfileIngestRequest, Timepoint


def test_round_trip_preserves_row_local_plant_ratios(tmp_path: Path):
    repo = ProfileRepository(tmp_path / "profiles")
    records = [
        PeakRecord(
            sample_id="lemongrass",
            blend_id="lemongrass",
            timepoint=Timepoint.BEFORE_STORAGE,
            plant_components=["Cymbopogon citratus"],
            plant_ratios={"Cymbopogon citratus": 30.0},
            reported_compound_name="Citral",
            area_percent=13.10,
        ),
        PeakRecord(
            sample_id="blend_b",
            blend_id="blend_b",
            timepoint=Timepoint.BEFORE_STORAGE,
            plant_components=["Cymbopogon citratus", "Ocimum gratissimum", "Syzygium aromaticum", "Zingiber officinale"],
            plant_ratios={
                "Cymbopogon citratus": 7.5,
                "Ocimum gratissimum": 7.5,
                "Syzygium aromaticum": 7.5,
                "Zingiber officinale": 7.5,
            },
            reported_compound_name="Eugenol",
            area_percent=53.25,
        ),
    ]
    response = repo.ingest(ProfileIngestRequest(records=records, dataset_id="demo"))
    assert response.artifact_path.endswith(".jsonl")

    loaded = repo.load("demo")
    assert loaded[0].plant_ratios == {"Cymbopogon citratus": 30.0}
    assert loaded[1].plant_ratios["Syzygium aromaticum"] == 7.5


def test_clean_row_removes_only_parquet_struct_null_plant_keys():
    row = {
        "plant_ratios": {
            "Cymbopogon citratus": 30.0,
            "Ocimum gratissimum": None,
            "Syzygium aromaticum": None,
        },
        "reported_smiles": None,
    }
    cleaned = ProfileRepository._clean_row(row)
    assert cleaned["plant_ratios"] == {"Cymbopogon citratus": 30.0}
    assert "reported_smiles" in cleaned and cleaned["reported_smiles"] is None
