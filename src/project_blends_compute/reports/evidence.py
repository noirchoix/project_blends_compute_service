from __future__ import annotations

from typing import Any

from project_blends_compute.utils import stable_hash


def _claim_id(text: str) -> str:
    return "claim-" + stable_hash(text)[:16]


def build_evidence_packets(
    *,
    profile_metrics: list[dict[str, Any]],
    reaction_evaluations: list[dict[str, Any]],
    occurrences: list[dict[str, Any]],
    identity_compounds: list[dict[str, Any]],
    storage_evidence: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    packets: list[dict[str, Any]] = []
    for metric in profile_metrics:
        text = (
            f"{metric['sample_id']} showed a weighted-Jaccard retention of "
            f"{float(metric['weighted_jaccard']):.4f} between reported before- and after-storage profiles."
        )
        packets.append({
            "claim_id": _claim_id(text),
            "claim_text": text,
            "claim_class": "DERIVED",
            "evidence_domain": "profile_composition",
            "sample_id": metric["sample_id"],
            "support": {"profile_metric": metric},
            "contradictory_evidence": [],
            "confidence": 1.0,
            "claim_boundary": "descriptive compositional comparison; no inferential significance or absolute-concentration claim",
        })
    for evaluation in reaction_evaluations:
        candidate = evaluation.get("candidate", {})
        text = (
            f"The proposed {candidate.get('precursor_name')} to {candidate.get('product_name')} relationship "
            f"received evidence grade {evaluation.get('evidence_grade')} and plausibility score "
            f"{float(evaluation.get('plausibility_score') or 0):.4f}."
        )
        packets.append({
            "claim_id": _claim_id(text),
            "claim_text": text,
            "claim_class": "HYPOTHESIZED" if evaluation.get("abstained") else "COMPUTATIONALLY_SUPPORTED",
            "evidence_domain": "reaction_intelligence",
            "sample_id": candidate.get("sample_id"),
            "precursor_compound_id": candidate.get("precursor_compound_id"),
            "product_compound_id": candidate.get("product_compound_id"),
            "support": evaluation,
            "contradictory_evidence": evaluation.get("abstention_reasons", []),
            "confidence": float(evaluation.get("plausibility_score") or 0),
            "claim_boundary": evaluation.get("claim_boundary"),
        })
    by_compound: dict[str, list[dict[str, Any]]] = {}
    for occurrence in occurrences:
        key = str(occurrence.get("compound_name") or occurrence.get("compound_id") or "")
        by_compound.setdefault(key, []).append(occurrence)
    for key, rows in by_compound.items():
        sample_ids = sorted({str(row.get("sample_id")) for row in rows})
        ingredients = sorted({str(row.get("ingredient_name")) for row in rows})
        text = f"FoodDB documented {key} occurrence evidence for {', '.join(ingredients)} in sample context {', '.join(sample_ids)}."
        packets.append({
            "claim_id": _claim_id(text),
            "claim_text": text,
            "claim_class": "LITERATURE_SUPPORTED",
            "evidence_domain": "food_provenance",
            "support": {"fooddb_occurrences": rows},
            "contradictory_evidence": [],
            "confidence": max(float(row.get("confidence") or 0) for row in rows),
            "claim_boundary": "source plausibility only; does not confirm the origin of a particular GC-MS peak",
        })
    for compound in identity_compounds:
        if compound.get("resolved_identity"):
            continue
        text = f"The reported identity {compound.get('reported_name')} remains unresolved or requires manual review."
        packets.append({
            "claim_id": _claim_id(text),
            "claim_text": text,
            "claim_class": "UNRESOLVED",
            "evidence_domain": "identity",
            "compound_id": compound.get("compound_id"),
            "support": {"identity_record": compound},
            "contradictory_evidence": compound.get("conflict_flags", []),
            "confidence": float(compound.get("resolution_confidence") or 0),
            "claim_boundary": "do not use this annotation as a confirmed molecular identity",
        })
    packets.extend(_storage_evidence_packets(storage_evidence or {}))
    return packets


def _storage_evidence_packets(storage: dict[str, Any]) -> list[dict[str, Any]]:
    if storage.get("status") not in {"available", "available_with_linkage_warnings"}:
        return []
    packets: list[dict[str, Any]] = []
    dataset_name = storage.get("dataset_name")
    version = storage.get("version")

    for row in storage.get("nonreaction_evidence", []):
        explanation_type = str(row.get("explanation_type") or "unresolved")
        sample_id = row.get("sample_id")
        compound_name = row.get("compound_name") or row.get("compound_id") or "the reported compound"
        direction = str(row.get("direction") or "the observed storage-associated profile change").replace("_", " ")
        sources = row.get("sources") or []
        if explanation_type == "unresolved":
            claim_class = "UNRESOLVED"
        elif sources:
            claim_class = "LITERATURE_SUPPORTED"
        else:
            claim_class = "HYPOTHESIZED"
        readable_type = explanation_type.replace("_", " ")
        text = (
            f"For {sample_id}, {readable_type} is retained as an evidence-bounded alternative for "
            f"{compound_name} ({direction})."
        )
        packets.append({
            "claim_id": _claim_id(text + str(row.get("hypothesis_id"))),
            "claim_text": text,
            "claim_class": claim_class,
            "evidence_domain": "storage_evidence",
            "evidence_type": "nonreaction_explanation",
            "evidence_role": "supports_alternative_interpretation" if sources else "project_specific_hypothesis_only",
            "support_or_contradiction": "support" if sources else "hypothesis_only",
            "dataset_name": dataset_name,
            "dataset_version": version,
            "hypothesis_id": row.get("hypothesis_id"),
            "sample_id": sample_id,
            "compound_id": row.get("compound_id"),
            "compound_name": row.get("compound_name"),
            "directness": row.get("directness"),
            "condition_compatibility": row.get("condition_compatibility"),
            "source_ids": row.get("evidence_source_ids", []),
            "source_dois": row.get("source_dois", []),
            "support": {
                "storage_evidence_record": row,
                "sources": sources,
                "observed_profile": row.get("observed_profile"),
            },
            "contradictory_evidence": [],
            "confidence": None,
            "confidence_semantics": "not_calibrated",
            "claim_boundary": row.get("claim_boundary") or "alternative explanation only; does not establish occurrence",
        })

    for row in storage.get("transformation_precedents", []):
        precursor = row.get("precursor_name") or row.get("precursor_compound_id")
        product = row.get("product_name") or row.get("product_compound_id")
        family = str(row.get("transformation_family") or "transformation").replace("_", " ")
        compatibility = row.get("condition_compatibility") or "unknown"
        text = (
            f"Literature contains a {family} precedent linking {precursor} and {product}; "
            f"condition compatibility with Project Blends is {compatibility}."
        )
        contradictory = []
        if str(compatibility).lower() in {"low", "very_low", "incompatible"}:
            contradictory.append(f"storage_condition_mismatch:{compatibility}")
        source = row.get("source") or {}
        packets.append({
            "claim_id": _claim_id(text + str(row.get("reaction_id"))),
            "claim_text": text,
            "claim_class": "LITERATURE_SUPPORTED",
            "evidence_domain": "storage_evidence",
            "evidence_type": "transformation_precedent",
            "evidence_role": "supports_chemical_precedent_but_limits_storage_attribution",
            "support_or_contradiction": "mixed" if contradictory else "support",
            "dataset_name": dataset_name,
            "dataset_version": version,
            "reaction_id": row.get("reaction_id"),
            "precursor_compound_id": row.get("precursor_compound_id"),
            "product_compound_id": row.get("product_compound_id"),
            "source_id": row.get("source_id"),
            "source_doi": row.get("source_doi") or source.get("doi"),
            "directness": row.get("directness"),
            "condition_compatibility": compatibility,
            "support": {
                "transformation_precedent": row,
                "source": source,
                "conditions": row.get("conditions", []),
            },
            "contradictory_evidence": contradictory,
            "confidence": None,
            "confidence_semantics": "not_calibrated",
            "claim_boundary": row.get("claim_boundary") or "chemical precedent only; does not establish storage conversion",
        })
    return packets
