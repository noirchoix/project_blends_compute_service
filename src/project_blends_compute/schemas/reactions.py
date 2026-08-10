from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import Field

from .common import EvidenceGrade, ProvenanceRecord, StrictModel


class ExplanationType(StrEnum):
    CHEMICAL_TRANSFORMATION = "chemical_transformation"
    EVAPORATION_OR_VOLATILIZATION = "evaporation_or_volatilization"
    DEGRADATION_TO_UNOBSERVED_PRODUCTS = "degradation_to_unobserved_products"
    ANALYTICAL_NON_DETECTION = "analytical_non_detection"
    LIBRARY_MISIDENTIFICATION = "library_misidentification"
    CO_ELUTION = "co_elution"
    SAMPLE_CONTAMINATION = "sample_contamination"
    INSTRUMENT_CARRYOVER = "instrument_carryover"
    RELATIVE_AREA_NORMALIZATION = "relative_area_normalization"
    UNRESOLVED = "unresolved"


class ReactionCandidate(StrictModel):
    hypothesis_id: str
    sample_id: str
    precursor_compound_id: str | None = None
    product_compound_id: str | None = None
    precursor_name: str
    product_name: str
    precursor_smiles: str | None = None
    product_smiles: str | None = None
    reaction_smiles: str | None = None
    mapped_reaction_smiles: str | None = None
    transformation_family: str
    formula_delta: dict[str, int] = Field(default_factory=dict)
    same_formula: bool = False
    tanimoto_similarity: float | None = Field(default=None, ge=0, le=1)
    mcs_coverage: float | None = Field(default=None, ge=0, le=1)
    heuristic_score: float = Field(ge=0, le=1)
    mapping_status: str = "not_attempted"
    mass_balanced: bool | None = None
    warnings: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ReactionScreeningRecord(StrictModel):
    screening_id: str
    sample_id: str
    precursor_compound_id: str | None = None
    product_compound_id: str | None = None
    precursor_name: str
    product_name: str
    formula_delta: dict[str, int] = Field(default_factory=dict)
    same_formula: bool = False
    tanimoto_similarity: float | None = Field(default=None, ge=0, le=1)
    mcs_coverage: float | None = Field(default=None, ge=0, le=1)
    transformation_family: str
    identity_gate: bool
    formula_gate: bool
    connectivity_gate: bool
    storage_prior_gate: bool
    decision: str
    rejection_reasons: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class AlternativeExplanation(StrictModel):
    hypothesis_id: str
    sample_id: str
    compound_id: str | None = None
    compound_name: str
    explanation_type: ExplanationType
    direction: str
    score: float = Field(ge=0, le=1)
    basis: list[str] = Field(default_factory=list)


class ReactionGenerateRequest(StrictModel):
    before: list[dict[str, Any]]
    after: list[dict[str, Any]]
    sample_id: str
    max_candidates: int = Field(default=200, ge=1, le=2000)
    minimum_similarity: float = Field(default=0.15, ge=0, le=1)
    minimum_identity_confidence: float = Field(default=0.65, ge=0, le=1)
    include_all_alternatives: bool = True


class ReactionGenerateResponse(StrictModel):
    ok: bool
    candidates: list[ReactionCandidate]
    alternatives: list[AlternativeExplanation]
    screening: list[ReactionScreeningRecord] = Field(default_factory=list)
    rejected_pairs: int
    method: dict[str, Any]


class TemplateEvidence(StrictModel):
    template_id: str
    template_family: str
    support_count: int | None = None
    confidence: float = Field(default=0, ge=0, le=1)
    candidate_products: list[str] = Field(default_factory=list)
    product_match_similarity: float | None = Field(default=None, ge=0, le=1)
    mechanism_tags: list[str] = Field(default_factory=list)
    provenance: dict[str, Any] = Field(default_factory=dict)


class CuratedReactionEvidence(StrictModel):
    reaction_id: str
    source_id: str | None = None
    source_doi: str | None = None
    transformation_family: str | None = None
    evidence_directness: str | None = None
    precursor_match: float = Field(default=0, ge=0, le=1)
    product_match: float = Field(default=0, ge=0, le=1)
    conditions: list[dict[str, Any]] = Field(default_factory=list)
    provenance: list[ProvenanceRecord] = Field(default_factory=list)
    raw: dict[str, Any] = Field(default_factory=dict)


class ReactionEvaluation(StrictModel):
    candidate: ReactionCandidate
    template_evidence: list[TemplateEvidence] = Field(default_factory=list)
    curated_evidence: list[CuratedReactionEvidence] = Field(default_factory=list)
    condition_match_score: float | None = Field(default=None, ge=0, le=1)
    evidence_grade: EvidenceGrade
    plausibility_score: float = Field(ge=0, le=1)
    abstained: bool = False
    abstention_reasons: list[str] = Field(default_factory=list)
    claim_boundary: str
    provenance: dict[str, Any] = Field(default_factory=dict)


class ReactionEvaluateRequest(StrictModel):
    candidates: list[ReactionCandidate]
    storage_context: dict[str, Any] = Field(default_factory=dict)
    use_rxn_bridge: bool = True
    use_reaction_curation: bool = True
    strict: bool = False


class ReactionEvaluateResponse(StrictModel):
    ok: bool
    evaluations: list[ReactionEvaluation]
    lane_status: dict[str, str]
    warnings: list[str] = Field(default_factory=list)
