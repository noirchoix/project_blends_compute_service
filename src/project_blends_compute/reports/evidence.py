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
            "compound_id": compound.get("compound_id"),
            "support": {"identity_record": compound},
            "contradictory_evidence": compound.get("conflict_flags", []),
            "confidence": float(compound.get("resolution_confidence") or 0),
            "claim_boundary": "do not use this annotation as a confirmed molecular identity",
        })
    return packets
