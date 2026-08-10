from __future__ import annotations

from collections import defaultdict
from typing import Any


LEVELS = ("chemical_super_class", "chemical_class")


def _taxonomy_by_compound(evidence: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {
        str(row["compound_id"]): row["result"]
        for row in evidence
        if row.get("lane") == "taxonomy" and row.get("compound_id") and isinstance(row.get("result"), dict)
    }


def _probabilities(node: dict[str, Any]) -> list[dict[str, Any]]:
    values = node.get("probabilities") or node.get("top5") or []
    return [row for row in values if isinstance(row, dict) and row.get("label") is not None]


def aggregate_taxonomy_profiles(
    records: list[dict[str, Any]],
    evidence: list[dict[str, Any]],
    *,
    high_confidence_threshold: float = 0.80,
) -> dict[str, Any]:
    """Aggregate model probabilities with GC-MS relative peak areas.

    Each canonical compound is predicted once. Experimental peak rows are then joined
    back through ``compound_id``. Expected class area is sum(area_percent * p(class)).
    Because the COCONUT provider is requested with all class probabilities, this
    avoids converting uncertain top-1 predictions into hard chemical facts.
    """

    taxonomy = _taxonomy_by_compound(evidence)
    # Collapse duplicate source labels for the same compound within a sample/timepoint.
    peak_area: dict[tuple[str, str, str], float] = defaultdict(float)
    sample_blend: dict[str, str] = {}
    for row in records:
        compound_id = row.get("compound_id")
        if not compound_id:
            continue
        sample_id = str(row.get("sample_id"))
        timepoint = str(row.get("timepoint"))
        peak_area[(sample_id, timepoint, str(compound_id))] += float(row.get("area_percent") or 0.0)
        sample_blend[sample_id] = str(row.get("blend_id") or sample_id)

    composition_acc: dict[tuple[str, str, str, str], float] = defaultdict(float)
    top1_acc: dict[tuple[str, str, str, str], float] = defaultdict(float)
    summaries: dict[tuple[str, str], dict[str, Any]] = {}

    grouped_keys: dict[tuple[str, str], list[tuple[str, float]]] = defaultdict(list)
    for (sample_id, timepoint, compound_id), area in peak_area.items():
        grouped_keys[(sample_id, timepoint)].append((compound_id, area))

    for (sample_id, timepoint), compounds in sorted(grouped_keys.items()):
        source_area = sum(area for _, area in compounds)
        covered_area = sum(area for compound_id, area in compounds if compound_id in taxonomy)
        summary = {
            "sample_id": sample_id,
            "blend_id": sample_blend.get(sample_id, sample_id),
            "timepoint": timepoint,
            "source_area_percent": source_area,
            "taxonomy_covered_area_percent": covered_area,
            "taxonomy_coverage_fraction": covered_area / source_area if source_area > 0 else 0.0,
            "high_confidence_threshold": high_confidence_threshold,
            "levels": {},
        }
        for level in LEVELS:
            high_area = 0.0
            uncertain_area = 0.0
            probability_mass_area = 0.0
            for compound_id, area in compounds:
                result = taxonomy.get(compound_id)
                if not result:
                    continue
                node = result.get(level) or {}
                probs = _probabilities(node)
                probability_mass = sum(float(item.get("prob") or 0.0) for item in probs)
                probability_mass_area += area * probability_mass
                for item in probs:
                    label = str(item["label"])
                    composition_acc[(sample_id, timepoint, level, label)] += area * float(item.get("prob") or 0.0)
                top_label = node.get("label")
                confidence = float(node.get("confidence") or 0.0)
                if top_label and top_label != "unknown":
                    if confidence >= high_confidence_threshold:
                        high_area += area
                        top1_acc[(sample_id, timepoint, level, str(top_label))] += area
                    else:
                        uncertain_area += area
            summary["levels"][level] = {
                "high_confidence_top1_area_percent": high_area,
                "uncertain_top1_area_percent": uncertain_area,
                "probability_mass_weighted_area_percent": probability_mass_area,
                "probability_mass_coverage_fraction": probability_mass_area / covered_area if covered_area > 0 else 0.0,
            }
        summaries[(sample_id, timepoint)] = summary

    composition_rows: list[dict[str, Any]] = []
    for (sample_id, timepoint, level, label), weighted_area in sorted(composition_acc.items()):
        summary = summaries[(sample_id, timepoint)]
        covered_area = float(summary["taxonomy_covered_area_percent"] or 0.0)
        composition_rows.append(
            {
                "sample_id": sample_id,
                "blend_id": summary["blend_id"],
                "timepoint": timepoint,
                "taxonomy_level": level,
                "label": label,
                "probability_weighted_area_percent": weighted_area,
                "normalized_share_of_taxonomy_covered_area": weighted_area / covered_area if covered_area > 0 else 0.0,
                "high_confidence_top1_area_percent": top1_acc.get((sample_id, timepoint, level, label), 0.0),
            }
        )

    by_sample_level_label: dict[tuple[str, str, str], dict[str, float]] = defaultdict(dict)
    for row in composition_rows:
        by_sample_level_label[(row["sample_id"], row["taxonomy_level"], row["label"])][row["timepoint"]] = float(
            row["normalized_share_of_taxonomy_covered_area"]
        )
    shift_rows: list[dict[str, Any]] = []
    for (sample_id, level, label), timepoints in sorted(by_sample_level_label.items()):
        before = float(timepoints.get("before_storage", 0.0))
        after = float(timepoints.get("after_storage", 0.0))
        shift_rows.append(
            {
                "sample_id": sample_id,
                "blend_id": sample_blend.get(sample_id, sample_id),
                "taxonomy_level": level,
                "label": label,
                "before_normalized_share": before,
                "after_normalized_share": after,
                "delta_after_minus_before": after - before,
            }
        )

    return {
        "schema_version": "project_blends_taxonomy_profile_aggregation.v1",
        "model_role": "supporting_computational_taxonomy",
        "high_confidence_threshold": high_confidence_threshold,
        "composition_rows": composition_rows,
        "shift_rows": shift_rows,
        "sample_timepoint_summaries": list(summaries.values()),
        "method": {
            "formula": "sum(relative_peak_area_percent * taxonomy_probability)",
            "canonical_entity_prediction": True,
            "duplicate_reported_labels_collapsed_by_compound_id": True,
            "probability_vectors_preferred_over_hard_top1": True,
            "claim_boundary": "model-assisted class redistribution; not identity confirmation, absolute quantitation, or reaction evidence",
        },
    }
