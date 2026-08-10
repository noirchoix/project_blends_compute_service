import pytest

from project_blends_compute.supporting.taxonomy_aggregation import aggregate_taxonomy_profiles


def test_probability_weighted_taxonomy_aggregation_collapses_alias_peak_rows():
    records = [
        {"sample_id": "b", "blend_id": "b", "timepoint": "before_storage", "compound_id": "cmp-1", "area_percent": 20.0},
        {"sample_id": "b", "blend_id": "b", "timepoint": "before_storage", "compound_id": "cmp-1", "area_percent": 10.0},
        {"sample_id": "b", "blend_id": "b", "timepoint": "after_storage", "compound_id": "cmp-1", "area_percent": 40.0},
    ]
    evidence = [
        {
            "lane": "taxonomy",
            "compound_id": "cmp-1",
            "result": {
                "chemical_super_class": {
                    "label": "S1",
                    "confidence": 0.9,
                    "probabilities": [{"label": "S1", "prob": 0.75}, {"label": "S2", "prob": 0.25}],
                },
                "chemical_class": {
                    "label": "C1",
                    "confidence": 0.7,
                    "probabilities": [{"label": "C1", "prob": 0.6}, {"label": "C2", "prob": 0.4}],
                },
            },
        }
    ]
    result = aggregate_taxonomy_profiles(records, evidence)
    before_c1 = next(
        row for row in result["composition_rows"]
        if row["timepoint"] == "before_storage" and row["taxonomy_level"] == "chemical_class" and row["label"] == "C1"
    )
    assert before_c1["probability_weighted_area_percent"] == pytest.approx(18.0)
    assert before_c1["normalized_share_of_taxonomy_covered_area"] == pytest.approx(0.6)
    before_summary = next(row for row in result["sample_timepoint_summaries"] if row["timepoint"] == "before_storage")
    assert before_summary["source_area_percent"] == pytest.approx(30.0)
    assert before_summary["levels"]["chemical_class"]["uncertain_top1_area_percent"] == pytest.approx(30.0)
    assert before_summary["levels"]["chemical_super_class"]["high_confidence_top1_area_percent"] == pytest.approx(30.0)
