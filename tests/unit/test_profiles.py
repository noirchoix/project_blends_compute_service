from project_blends_compute.profiles.compositional import analyse_profiles
from project_blends_compute.schemas.profiles import PeakRecord, Timepoint


def test_compositional_metrics_are_descriptive():
    records = [
        PeakRecord(sample_id="x", blend_id="x", timepoint=Timepoint.BEFORE_STORAGE, storage_days=0, reported_compound_name="A", compound_id="A", area_percent=60),
        PeakRecord(sample_id="x", blend_id="x", timepoint=Timepoint.BEFORE_STORAGE, storage_days=0, reported_compound_name="B", compound_id="B", area_percent=40),
        PeakRecord(sample_id="x", blend_id="x", timepoint=Timepoint.AFTER_STORAGE, storage_days=28, reported_compound_name="A", compound_id="A", area_percent=30),
        PeakRecord(sample_id="x", blend_id="x", timepoint=Timepoint.AFTER_STORAGE, storage_days=28, reported_compound_name="C", compound_id="C", area_percent=70),
    ]
    metric = analyse_profiles(records)[0]
    assert metric.shared_count == 1
    assert 0 <= metric.weighted_jaccard <= 1
    assert 0 <= metric.bray_curtis_dissimilarity <= 1
    assert "B" in metric.removed_compounds
    assert "C" in metric.added_compounds


def test_missing_reported_classes_do_not_create_false_perfect_class_similarity():
    records = [
        PeakRecord(sample_id="x", blend_id="x", timepoint=Timepoint.BEFORE_STORAGE, storage_days=0, reported_compound_name="A", compound_id="A", area_percent=100),
        PeakRecord(sample_id="x", blend_id="x", timepoint=Timepoint.AFTER_STORAGE, storage_days=28, reported_compound_name="B", compound_id="B", area_percent=100),
    ]
    metric = analyse_profiles(records)[0]
    assert metric.class_weighted_jaccard is None
    assert "class_metric_unavailable_missing_reported_class" in metric.warnings
