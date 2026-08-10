from __future__ import annotations

from typing import Any

from project_blends_compute.schemas.common import EvidenceGrade
from project_blends_compute.schemas.reactions import CuratedReactionEvidence, ReactionCandidate, ReactionEvaluation, TemplateEvidence


MAJOR_REORGANIZATION_FAMILIES = {"unclassified_skeletal_change"}


def condition_match_score(storage_context: dict[str, Any], evidence: CuratedReactionEvidence) -> float | None:
    if not evidence.conditions:
        return None
    scores: list[float] = []
    target_temp = storage_context.get("temperature_c")
    target_duration_days = storage_context.get("duration_days")
    target_atmosphere = str(storage_context.get("atmosphere") or storage_context.get("oxygen_exposure") or "").lower()
    for condition in evidence.conditions:
        parts: list[float] = []
        evidence_temp = condition.get("temperature_c") or condition.get("temperature_value_c") or condition.get("temperature_c_primary")
        if target_temp is not None and evidence_temp is not None:
            delta = abs(float(target_temp) - float(evidence_temp))
            parts.append(max(0.0, 1.0 - delta / 50.0))
        evidence_hours = condition.get("duration_h") or condition.get("time_value_hr") or condition.get("time_h_primary")
        if target_duration_days is not None and evidence_hours is not None:
            target_hours = float(target_duration_days) * 24.0
            ratio = min(target_hours, float(evidence_hours)) / max(target_hours, float(evidence_hours), 1e-9)
            parts.append(ratio)
        evidence_atmosphere = str(condition.get("atmosphere") or condition.get("atmosphere_text") or condition.get("oxygen_exposure") or "").lower()
        if target_atmosphere and evidence_atmosphere:
            parts.append(1.0 if target_atmosphere in evidence_atmosphere or evidence_atmosphere in target_atmosphere else 0.25)
        if parts:
            scores.append(sum(parts) / len(parts))
    return max(scores) if scores else None


def evaluate_candidate(
    candidate: ReactionCandidate,
    template_evidence: list[TemplateEvidence],
    curated_evidence: list[CuratedReactionEvidence],
    storage_context: dict[str, Any],
) -> ReactionEvaluation:
    best_template = max(
        ((item.product_match_similarity or 0.0) * item.confidence for item in template_evidence),
        default=0.0,
    )
    best_curated = max(
        (0.5 * item.precursor_match + 0.5 * item.product_match for item in curated_evidence),
        default=0.0,
    )
    condition_scores = [score for evidence in curated_evidence if (score := condition_match_score(storage_context, evidence)) is not None]
    condition = max(condition_scores) if condition_scores else None
    identity_factor = min(
        float(candidate.metadata.get("precursor_identity_confidence", 0.0)),
        float(candidate.metadata.get("product_identity_confidence", 0.0)),
    )
    plausibility = (
        0.30 * candidate.heuristic_score
        + 0.25 * best_template
        + 0.30 * best_curated
        + 0.10 * (condition if condition is not None else 0.0)
        + 0.05 * identity_factor
    )
    abstention: list[str] = []
    if identity_factor < 0.65:
        abstention.append("identity_confidence_below_publication_threshold")
    if candidate.transformation_family in MAJOR_REORGANIZATION_FAMILIES and best_curated < 0.85:
        abstention.append("major_skeletal_reorganization_lacks_direct_curated_precedent")
    if not template_evidence and not curated_evidence:
        abstention.append("no_external_reaction_evidence")
    if candidate.same_formula and not curated_evidence:
        abstention.append("cannot_distinguish_isomerization_from_identity_ambiguity")
    if best_curated >= 0.90 and (condition or 0.0) >= 0.65 and identity_factor >= 0.80:
        grade = EvidenceGrade.A
    elif best_curated >= 0.75 and (best_template >= 0.55 or (condition or 0.0) >= 0.45) and identity_factor >= 0.70:
        grade = EvidenceGrade.B
    elif plausibility >= 0.55 and identity_factor >= 0.60:
        grade = EvidenceGrade.C
    elif plausibility >= 0.30:
        grade = EvidenceGrade.D
    else:
        grade = EvidenceGrade.U
    if candidate.transformation_family in MAJOR_REORGANIZATION_FAMILIES and best_curated < 0.50:
        grade = EvidenceGrade.R
    abstained = bool(abstention) or grade in {EvidenceGrade.D, EvidenceGrade.U, EvidenceGrade.R}
    claim_boundary = (
        "Direct condition-matched precedent supports this transformation hypothesis, but the Project Blends GC-MS data alone do not prove conversion."
        if grade == EvidenceGrade.A
        else "Convergent structural and reaction evidence supports plausibility; report as a supported hypothesis, not an observed reaction."
        if grade == EvidenceGrade.B
        else "Chemically plausible candidate requiring targeted analytical or experimental validation."
        if grade == EvidenceGrade.C
        else "Insufficient evidence for a causal storage transformation; retain non-reaction explanations."
    )
    return ReactionEvaluation(
        candidate=candidate,
        template_evidence=template_evidence,
        curated_evidence=curated_evidence,
        condition_match_score=condition,
        evidence_grade=grade,
        plausibility_score=max(0.0, min(1.0, plausibility)),
        abstained=abstained,
        abstention_reasons=abstention,
        claim_boundary=claim_boundary,
        provenance={
            "rxn_bridge_role": "analogous_template_evidence_not_direct_proof",
            "reaction_curation_role": "authoritative_curated_reaction_and_condition_evidence",
            "condition_retrieval_policy": "reaction_specific_first_signature_only_as_secondary_context",
        },
    )
