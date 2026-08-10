from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from project_blends_compute.identity.chemistry import formula_delta, validate_structure
from project_blends_compute.reactions.chemistry import mcs_coverage, tanimoto
from project_blends_compute.schemas.reactions import (
    AlternativeExplanation,
    ExplanationType,
    ReactionCandidate,
    ReactionScreeningRecord,
)
from project_blends_compute.utils import normalize_name, stable_hash


# Formula deltas may describe a transformation family, but only a conservative subset
# is granted an a-priori storage-chemistry gate. Others require direct curated evidence
# before they should become reaction candidates.
DELTA_FAMILIES: dict[tuple[tuple[str, int], ...], str] = {
    (("O", 1),): "oxidation_or_oxygenation",
    (("H", -2),): "dehydrogenation_or_oxidation",
    (("H", 2),): "hydrogenation_or_reduction",
    (("H", -2), ("O", 1)): "oxidative_dehydrogenation",
    (("H", 2), ("O", 1)): "hydration_or_reduction_oxygenation",
    (("C", 2), ("H", 2), ("O", 1)): "acetylation_candidate",
    (("C", -2), ("H", -2), ("O", -1)): "deacetylation_candidate",
}

STORAGE_PRIOR_FAMILIES = {
    "oxidation_or_oxygenation",
    "dehydrogenation_or_oxidation",
    "oxidative_dehydrogenation",
}


@dataclass(slots=True)
class CompoundView:
    compound_id: str | None
    name: str
    smiles: str | None
    formula: str | None
    area_percent: float
    identity_confidence: float
    structure_eligible: bool
    heavy_atom_count: int | None
    raw: dict[str, Any]


def _view(row: dict[str, Any]) -> CompoundView:
    smiles = row.get("isomeric_smiles") or row.get("canonical_smiles")
    validation = validate_structure(smiles=smiles) if smiles else None
    eligible = bool(
        row.get("downstream_structure_eligible", row.get("metadata", {}).get("resolved_identity", bool(smiles)))
        and validation
        and validation.parse_valid
    )
    return CompoundView(
        compound_id=row.get("compound_id"),
        name=str(
            row.get("preferred_name")
            or row.get("reported_compound_name")
            or row.get("reported_name")
            or row.get("name")
            or "unknown"
        ),
        smiles=(validation.canonical_smiles if validation and validation.parse_valid else None),
        formula=row.get("molecular_formula") or (validation.molecular_formula if validation and validation.parse_valid else None),
        area_percent=float(row.get("area_percent") or 0.0),
        identity_confidence=float(row.get("identity_confidence") or row.get("resolution_confidence") or 0.0),
        structure_eligible=eligible,
        heavy_atom_count=(validation.heavy_atom_count if validation and validation.parse_valid else None),
        raw=row,
    )


def _family(delta: dict[str, int], same_formula: bool, similarity: float | None, coverage: float | None) -> str:
    if same_formula:
        return "positional_isomer_or_library_assignment_ambiguity"
    key = tuple(sorted(delta.items()))
    if key in DELTA_FAMILIES:
        return DELTA_FAMILIES[key]
    if (similarity or 0.0) >= 0.55 and (coverage or 0.0) >= 0.75:
        return "structurally_related_unknown_delta"
    return "unclassified_skeletal_change"


def _screen_pair(
    *,
    sample_id: str,
    precursor: CompoundView,
    product: CompoundView,
    minimum_identity_confidence: float,
    minimum_similarity: float,
) -> tuple[ReactionScreeningRecord, ReactionCandidate | None]:
    similarity = tanimoto(precursor.smiles, product.smiles) if precursor.smiles and product.smiles else None
    coverage = mcs_coverage(precursor.smiles, product.smiles) if precursor.smiles and product.smiles else None
    delta = formula_delta(precursor.formula, product.formula)
    same_formula = bool(precursor.formula and product.formula and precursor.formula == product.formula)
    family = _family(delta, same_formula, similarity, coverage)

    reasons: list[str] = []
    identity_gate = bool(
        precursor.structure_eligible
        and product.structure_eligible
        and precursor.identity_confidence >= minimum_identity_confidence
        and product.identity_confidence >= minimum_identity_confidence
    )
    if not identity_gate:
        reasons.append("identity_or_structure_gate_failed")

    delta_key = tuple(sorted(delta.items()))
    recognized_delta = delta_key in DELTA_FAMILIES
    formula_gate = bool(same_formula or recognized_delta)
    if not formula_gate:
        reasons.append("formula_delta_not_storage_interpretable")

    sim = similarity or 0.0
    mcs = coverage or 0.0
    if family == "positional_isomer_or_library_assignment_ambiguity":
        # Same-formula positional/stereochemical matches cannot be promoted from
        # two-timepoint GC-MS library assignments alone. Preserve them as an
        # analytical-ambiguity audit record until direct storage evidence exists.
        connectivity_gate = sim >= max(0.40, minimum_similarity) and mcs >= 0.65
        reasons.append("same_formula_isomer_or_library_assignment_requires_direct_storage_evidence")
    elif family in {"oxidation_or_oxygenation", "dehydrogenation_or_oxidation", "oxidative_dehydrogenation"}:
        # For larger phytochemicals, simple oxidation/dehydrogenation should preserve
        # most of the molecular scaffold. Low-overlap +O pairs are not promoted merely
        # because their formula delta resembles oxygenation. Very small molecules keep
        # a separate relaxed gate because one bond-order change dominates fingerprints.
        min_heavy = min(
            value for value in [precursor.heavy_atom_count, product.heavy_atom_count] if value is not None
        ) if precursor.heavy_atom_count is not None and product.heavy_atom_count is not None else None
        if min_heavy is not None and min_heavy <= 5:
            fingerprint_gate = sim >= 0.05
            connectivity_gate = fingerprint_gate and mcs >= 0.55
        else:
            fingerprint_gate = sim >= max(0.40, minimum_similarity)
            connectivity_gate = fingerprint_gate and mcs >= 0.75
    elif family == "structurally_related_unknown_delta":
        connectivity_gate = sim >= max(0.55, minimum_similarity) and mcs >= 0.75
    else:
        connectivity_gate = False
    if not connectivity_gate:
        reasons.append("connectivity_preservation_gate_failed")

    storage_prior_gate = family in STORAGE_PRIOR_FAMILIES
    if not storage_prior_gate:
        if family == "positional_isomer_or_library_assignment_ambiguity":
            reasons.append("redirect_to_analytical_identity_ambiguity")
        elif recognized_delta:
            reasons.append("transformation_family_requires_direct_storage_evidence")
        else:
            reasons.append("no_conservative_storage_chemistry_prior")

    accepted = identity_gate and formula_gate and connectivity_gate and storage_prior_gate
    screening_id = "screen-" + stable_hash(
        f"{sample_id}|{precursor.compound_id or precursor.name}|{product.compound_id or product.name}"
    )[:18]
    screening = ReactionScreeningRecord(
        screening_id=screening_id,
        sample_id=sample_id,
        precursor_compound_id=precursor.compound_id,
        product_compound_id=product.compound_id,
        precursor_name=precursor.name,
        product_name=product.name,
        formula_delta=delta,
        same_formula=same_formula,
        tanimoto_similarity=similarity,
        mcs_coverage=coverage,
        transformation_family=family,
        identity_gate=identity_gate,
        formula_gate=formula_gate,
        connectivity_gate=connectivity_gate,
        storage_prior_gate=storage_prior_gate,
        decision=(
            "candidate"
            if accepted
            else "redirected_analytical_ambiguity"
            if family == "positional_isomer_or_library_assignment_ambiguity" and identity_gate and formula_gate and connectivity_gate
            else "rejected_pre_evidence"
        ),
        rejection_reasons=list(dict.fromkeys(reasons)),
        metadata={
            "precursor_area_percent": precursor.area_percent,
            "product_area_percent": product.area_percent,
            "precursor_identity_confidence": precursor.identity_confidence,
            "product_identity_confidence": product.identity_confidence,
        },
    )
    if not accepted:
        return screening, None

    identity_factor = min(1.0, (precursor.identity_confidence + product.identity_confidence) / 2)
    family_bonus = 0.22 if family in {"oxidation_or_oxygenation", "oxidative_dehydrogenation"} else 0.18
    area_factor = min(1.0, (precursor.area_percent + product.area_percent) / 50.0)
    heuristic = min(1.0, 0.40 * sim + 0.25 * mcs + family_bonus + 0.08 * identity_factor + 0.05 * area_factor)
    hypothesis_id = "hyp-" + stable_hash(
        f"{sample_id}|{precursor.compound_id or precursor.name}|{product.compound_id or product.name}|{family}"
    )[:18]
    warnings: list[str] = []
    if same_formula:
        warnings.append("same_formula_does_not_distinguish_isomerization_from_library_ambiguity")
    candidate = ReactionCandidate(
        hypothesis_id=hypothesis_id,
        sample_id=sample_id,
        precursor_compound_id=precursor.compound_id,
        product_compound_id=product.compound_id,
        precursor_name=precursor.name,
        product_name=product.name,
        precursor_smiles=precursor.smiles,
        product_smiles=product.smiles,
        reaction_smiles=f"{precursor.smiles}>>{product.smiles}" if precursor.smiles and product.smiles else None,
        transformation_family=family,
        formula_delta=delta,
        same_formula=same_formula,
        tanimoto_similarity=similarity,
        mcs_coverage=coverage,
        heuristic_score=heuristic,
        warnings=warnings,
        metadata={
            **screening.metadata,
            "screening_id": screening_id,
            "candidate_policy": "conservative_storage_chemistry_pre_evidence_gate_v2",
        },
    )
    return screening, candidate


def generate_candidates_with_screening(
    *,
    sample_id: str,
    before: list[dict[str, Any]],
    after: list[dict[str, Any]],
    max_candidates: int = 200,
    minimum_similarity: float = 0.15,
    minimum_identity_confidence: float = 0.65,
    include_all_alternatives: bool = True,
) -> tuple[list[ReactionCandidate], list[AlternativeExplanation], list[ReactionScreeningRecord], int]:
    before_views = [_view(row) for row in before]
    after_views = [_view(row) for row in after]
    before_keys = {view.compound_id or normalize_name(view.name) for view in before_views}
    after_keys = {view.compound_id or normalize_name(view.name) for view in after_views}
    disappeared = [view for view in before_views if (view.compound_id or normalize_name(view.name)) not in after_keys]
    appeared = [view for view in after_views if (view.compound_id or normalize_name(view.name)) not in before_keys]

    candidates: list[ReactionCandidate] = []
    screening: list[ReactionScreeningRecord] = []
    for precursor in disappeared:
        for product in appeared:
            record, candidate = _screen_pair(
                sample_id=sample_id,
                precursor=precursor,
                product=product,
                minimum_identity_confidence=minimum_identity_confidence,
                minimum_similarity=minimum_similarity,
            )
            screening.append(record)
            if candidate is not None:
                candidates.append(candidate)

    candidates.sort(key=lambda candidate: candidate.heuristic_score, reverse=True)
    if len(candidates) > max_candidates:
        retained_ids = {candidate.hypothesis_id for candidate in candidates[:max_candidates]}
        candidate_screen_ids = {candidate.metadata.get("screening_id") for candidate in candidates if candidate.hypothesis_id not in retained_ids}
        for record in screening:
            if record.screening_id in candidate_screen_ids:
                record.decision = "rejected_candidate_cap"
                record.rejection_reasons.append("max_candidate_cap")
        candidates = candidates[:max_candidates]

    alternatives = _generate_alternatives(sample_id, disappeared, appeared, include_all_alternatives)
    rejected = sum(record.decision != "candidate" for record in screening)
    return candidates, alternatives, screening, rejected


def generate_candidates(
    *,
    sample_id: str,
    before: list[dict[str, Any]],
    after: list[dict[str, Any]],
    max_candidates: int = 200,
    minimum_similarity: float = 0.15,
    minimum_identity_confidence: float = 0.65,
    include_all_alternatives: bool = True,
) -> tuple[list[ReactionCandidate], list[AlternativeExplanation], int]:
    """Backward-compatible public helper returning the historical 3-tuple."""
    candidates, alternatives, _screening, rejected = generate_candidates_with_screening(
        sample_id=sample_id,
        before=before,
        after=after,
        max_candidates=max_candidates,
        minimum_similarity=minimum_similarity,
        minimum_identity_confidence=minimum_identity_confidence,
        include_all_alternatives=include_all_alternatives,
    )
    return candidates, alternatives, rejected


def _generate_alternatives(
    sample_id: str,
    disappeared: list[CompoundView],
    appeared: list[CompoundView],
    include_all: bool,
) -> list[AlternativeExplanation]:
    alternatives: list[AlternativeExplanation] = []
    disappeared_types = [
        (ExplanationType.EVAPORATION_OR_VOLATILIZATION, 0.55, ["compound_not_reported_after_storage", "volatile_profile_data"]),
        (ExplanationType.DEGRADATION_TO_UNOBSERVED_PRODUCTS, 0.50, ["compound_not_reported_after_storage"]),
        (ExplanationType.ANALYTICAL_NON_DETECTION, 0.45, ["single_library_based_gc_ms_observation"]),
        (ExplanationType.RELATIVE_AREA_NORMALIZATION, 0.40, ["relative_peak_area_is_compositional"]),
        (ExplanationType.LIBRARY_MISIDENTIFICATION, 0.35, ["tentative_library_identity"]),
    ]
    appeared_types = [
        (ExplanationType.SAMPLE_CONTAMINATION, 0.45, ["compound_newly_reported_after_storage"]),
        (ExplanationType.INSTRUMENT_CARRYOVER, 0.40, ["compound_newly_reported_after_storage"]),
        (ExplanationType.CO_ELUTION, 0.35, ["library_based_peak_assignment"]),
        (ExplanationType.RELATIVE_AREA_NORMALIZATION, 0.40, ["relative_peak_area_is_compositional"]),
        (ExplanationType.LIBRARY_MISIDENTIFICATION, 0.35, ["tentative_library_identity"]),
    ]
    for view in disappeared:
        types = disappeared_types if include_all else disappeared_types[:2]
        for explanation_type, score, basis in types:
            alternatives.append(
                AlternativeExplanation(
                    hypothesis_id="alt-" + stable_hash(f"{sample_id}|{view.name}|loss|{explanation_type.value}")[:18],
                    sample_id=sample_id,
                    compound_id=view.compound_id,
                    compound_name=view.name,
                    explanation_type=explanation_type,
                    direction="disappeared",
                    score=score,
                    basis=basis,
                )
            )
    for view in appeared:
        types = appeared_types if include_all else appeared_types[:2]
        for explanation_type, score, basis in types:
            alternatives.append(
                AlternativeExplanation(
                    hypothesis_id="alt-" + stable_hash(f"{sample_id}|{view.name}|appearance|{explanation_type.value}")[:18],
                    sample_id=sample_id,
                    compound_id=view.compound_id,
                    compound_name=view.name,
                    explanation_type=explanation_type,
                    direction="appeared",
                    score=score,
                    basis=basis,
                )
            )
    return alternatives
