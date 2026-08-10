from __future__ import annotations

from typing import Any

from pydantic import Field

from .common import ProvenanceRecord, StrictModel


class ProvenanceQuery(StrictModel):
    sample_id: str
    ingredient_names: list[str]
    compound_id: str | None = None
    compound_name: str
    inchikey: str | None = None
    cas_number: str | None = None


class ProvenanceEvaluateRequest(StrictModel):
    queries: list[ProvenanceQuery]
    include_exploratory_predictions: bool = False
    exploratory_top_k: int = Field(default=10, ge=1, le=100)


class FoodOccurrenceEvidence(StrictModel):
    sample_id: str
    ingredient_name: str
    food_id: int | None = None
    food_name: str | None = None
    food_scientific_name: str | None = None
    compound_id: int | None = None
    compound_name: str | None = None
    match_basis: str
    documented_occurrence: bool
    standard_content: float | None = None
    original_content: float | None = None
    original_unit: str | None = None
    preparation_type: str | None = None
    citation: str | None = None
    citation_type: str | None = None
    confidence: float = Field(ge=0, le=1)
    provenance: list[ProvenanceRecord] = Field(default_factory=list)


class ExploratoryLinkPrediction(StrictModel):
    sample_id: str
    ingredient_name: str
    compound_name: str | None = None
    compound_id: int | None = None
    probability: float = Field(ge=0, le=1)
    rank: int = Field(ge=1)
    label: str = "exploratory_prediction"
    evidence_warning: str = "not_occurrence_evidence"
    raw: dict[str, Any] = Field(default_factory=dict)


class ProvenanceEvaluateResponse(StrictModel):
    ok: bool
    occurrences: list[FoodOccurrenceEvidence]
    exploratory_predictions: list[ExploratoryLinkPrediction] = Field(default_factory=list)
    unresolved_queries: list[dict[str, Any]] = Field(default_factory=list)
    lane_status: dict[str, str] = Field(default_factory=dict)
