from __future__ import annotations

import math
from collections import defaultdict
from typing import Iterable

import numpy as np
from scipy.spatial.distance import braycurtis, jensenshannon

from project_blends_compute.schemas.profiles import PeakRecord, ProfileMetric, Timepoint
from project_blends_compute.utils import normalize_name


def _identity_key(record: PeakRecord, preferred: str) -> str:
    value = getattr(record, preferred, None)
    if value:
        return str(value)
    if record.inchikey:
        return record.inchikey
    return normalize_name(record.reported_compound_name)


def _aggregate(records: Iterable[PeakRecord], key_name: str, confidence_weighted: bool) -> tuple[dict[str, float], dict[str, str]]:
    areas: dict[str, float] = defaultdict(float)
    names: dict[str, str] = {}
    for record in records:
        key = _identity_key(record, key_name)
        weight = record.identity_confidence if confidence_weighted and record.identity_confidence is not None else 1.0
        areas[key] += float(record.area_percent) * float(weight)
        names.setdefault(key, record.reported_compound_name)
    return dict(areas), names


def _normalize_vector(values: np.ndarray) -> np.ndarray:
    total = float(values.sum())
    return values / total if total > 0 else values


def weighted_jaccard(a: np.ndarray, b: np.ndarray) -> float:
    denom = float(np.maximum(a, b).sum())
    return 1.0 if denom == 0 else float(np.minimum(a, b).sum() / denom)


def aitchison_distance(a: np.ndarray, b: np.ndarray, zero_replacement: float) -> float:
    a = np.where(a <= 0, zero_replacement, a)
    b = np.where(b <= 0, zero_replacement, b)
    a = _normalize_vector(a)
    b = _normalize_vector(b)
    clr_a = np.log(a) - np.mean(np.log(a))
    clr_b = np.log(b) - np.mean(np.log(b))
    return float(np.linalg.norm(clr_a - clr_b))


def _class_vectors(
    records_before: list[PeakRecord], records_after: list[PeakRecord]
) -> tuple[np.ndarray, np.ndarray, float, float]:
    """Build class-composition vectors without treating missing labels as a class.

    ``unclassified`` is not chemical evidence. Pooling every missing class label into
    one pseudo-class can produce a false perfect similarity (the lemongrass control
    in the v0.1.3 run was the concrete regression). The metric is therefore computed
    only from explicitly reported classes, with coverage returned for QC warnings.
    """

    before: dict[str, float] = defaultdict(float)
    after: dict[str, float] = defaultdict(float)
    total_before = sum(float(record.area_percent) for record in records_before)
    total_after = sum(float(record.area_percent) for record in records_after)
    classified_before = 0.0
    classified_after = 0.0
    for record in records_before:
        if record.reported_class and normalize_name(record.reported_class):
            before[normalize_name(record.reported_class)] += record.area_percent
            classified_before += float(record.area_percent)
    for record in records_after:
        if record.reported_class and normalize_name(record.reported_class):
            after[normalize_name(record.reported_class)] += record.area_percent
            classified_after += float(record.area_percent)
    keys = sorted(set(before) | set(after))
    coverage_before = classified_before / total_before if total_before > 0 else 0.0
    coverage_after = classified_after / total_after if total_after > 0 else 0.0
    return (
        np.array([before.get(k, 0.0) for k in keys], dtype=float),
        np.array([after.get(k, 0.0) for k in keys], dtype=float),
        coverage_before,
        coverage_after,
    )


def analyse_sample(
    records: list[PeakRecord],
    *,
    identity_key: str = "compound_id",
    zero_replacement: float = 1e-6,
    confidence_weighted: bool = True,
) -> ProfileMetric:
    if not records:
        raise ValueError("No records provided")
    sample_id = records[0].sample_id
    blend_id = records[0].blend_id
    before_records = [record for record in records if record.timepoint == Timepoint.BEFORE_STORAGE]
    after_records = [record for record in records if record.timepoint == Timepoint.AFTER_STORAGE]
    before_map, before_names = _aggregate(before_records, identity_key, confidence_weighted=False)
    after_map, after_names = _aggregate(after_records, identity_key, confidence_weighted=False)
    keys = sorted(set(before_map) | set(after_map))
    before_vec = np.array([before_map.get(key, 0.0) for key in keys], dtype=float)
    after_vec = np.array([after_map.get(key, 0.0) for key in keys], dtype=float)
    p = _normalize_vector(before_vec)
    q = _normalize_vector(after_vec)
    warnings: list[str] = []
    if not before_records or not after_records:
        warnings.append("missing_timepoint")
    if not math.isclose(before_vec.sum(), 100.0, rel_tol=0.05, abs_tol=5.0):
        warnings.append("before_area_sum_not_near_100")
    if not math.isclose(after_vec.sum(), 100.0, rel_tol=0.05, abs_tol=5.0):
        warnings.append("after_area_sum_not_near_100")
    class_before, class_after, class_coverage_before, class_coverage_after = _class_vectors(before_records, after_records)
    class_metric: float | None = None
    if class_before.sum() > 0 and class_after.sum() > 0:
        class_metric = weighted_jaccard(_normalize_vector(class_before), _normalize_vector(class_after))
        if class_coverage_before < 0.95:
            warnings.append("class_metric_partial_before")
        if class_coverage_after < 0.95:
            warnings.append("class_metric_partial_after")
    else:
        warnings.append("class_metric_unavailable_missing_reported_class")
    before_weighted, _ = _aggregate(before_records, identity_key, confidence_weighted=confidence_weighted)
    after_weighted, _ = _aggregate(after_records, identity_key, confidence_weighted=confidence_weighted)
    weighted_keys = sorted(set(before_weighted) | set(after_weighted))
    wb = np.array([before_weighted.get(key, 0.0) for key in weighted_keys], dtype=float)
    wa = np.array([after_weighted.get(key, 0.0) for key in weighted_keys], dtype=float)
    before_set = set(before_map)
    after_set = set(after_map)
    name_for = {**after_names, **before_names}
    return ProfileMetric(
        sample_id=sample_id,
        blend_id=blend_id,
        before_count=len(before_set),
        after_count=len(after_set),
        shared_count=len(before_set & after_set),
        weighted_jaccard=weighted_jaccard(p, q),
        bray_curtis_dissimilarity=float(braycurtis(p, q)) if p.sum() and q.sum() else 1.0,
        jensen_shannon_divergence=float(jensenshannon(p, q, base=2.0) ** 2) if p.sum() and q.sum() else 1.0,
        aitchison_distance=aitchison_distance(p, q, zero_replacement) if len(keys) > 1 else 0.0,
        class_weighted_jaccard=class_metric,
        source_area_before=float(before_vec.sum()),
        source_area_after=float(after_vec.sum()),
        confidence_weighted_retention=weighted_jaccard(_normalize_vector(wb), _normalize_vector(wa)),
        added_compounds=[name_for[key] for key in sorted(after_set - before_set)],
        removed_compounds=[name_for[key] for key in sorted(before_set - after_set)],
        shared_compounds=[name_for[key] for key in sorted(before_set & after_set)],
        warnings=warnings,
    )


def analyse_profiles(
    records: list[PeakRecord],
    *,
    identity_key: str = "compound_id",
    zero_replacement: float = 1e-6,
    confidence_weighted: bool = True,
) -> list[ProfileMetric]:
    grouped: dict[str, list[PeakRecord]] = defaultdict(list)
    for record in records:
        grouped[record.sample_id].append(record)
    return [
        analyse_sample(group, identity_key=identity_key, zero_replacement=zero_replacement, confidence_weighted=confidence_weighted)
        for _, group in sorted(grouped.items())
    ]


def stability_ranking(metrics: list[ProfileMetric]) -> list[dict]:
    ranking: list[dict] = []
    for metric in metrics:
        components: list[tuple[float, float]] = [
            (0.35, metric.weighted_jaccard),
            (0.20, 1.0 - metric.bray_curtis_dissimilarity),
            (0.15, 1.0 - metric.jensen_shannon_divergence),
        ]
        if metric.class_weighted_jaccard is not None:
            components.append((0.15, metric.class_weighted_jaccard))
        if metric.confidence_weighted_retention is not None:
            components.append((0.15, metric.confidence_weighted_retention))
        denominator = sum(weight for weight, _ in components) or 1.0
        score = sum(weight * value for weight, value in components) / denominator
        ranking.append(
            {
                "sample_id": metric.sample_id,
                "blend_id": metric.blend_id,
                "descriptive_stability_score": max(0.0, min(1.0, score)),
                "scope": "descriptive_profile_stability_not_shelf_life_or_therapeutic_efficacy",
            }
        )
    ranking.sort(key=lambda row: row["descriptive_stability_score"], reverse=True)
    for index, row in enumerate(ranking, start=1):
        row["rank"] = index
    return ranking
