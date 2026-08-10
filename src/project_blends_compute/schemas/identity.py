from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import Field

from .common import ProvenanceRecord, StrictModel


class StereochemistryStatus(StrEnum):
    SPECIFIED = "specified"
    UNSPECIFIED = "unspecified"
    PARTIAL = "partial"
    NOT_APPLICABLE = "not_applicable"
    AMBIGUOUS_MIXTURE = "ambiguous_mixture"
    UNKNOWN = "unknown"


class ManualReviewStatus(StrEnum):
    NOT_REQUIRED = "not_required"
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class IdentityAdjudicationStatus(StrEnum):
    RESOLVED = "resolved"
    MANUAL_CORRECTED = "manual_corrected"
    EXCLUDED_UNRESOLVED = "excluded_unresolved"
    UNRESOLVED_PENDING = "unresolved_pending"


class IdentityCandidate(StrictModel):
    source: str
    source_id: str | None = None
    preferred_name: str | None = None
    canonical_smiles: str | None = None
    isomeric_smiles: str | None = None
    inchi: str | None = None
    inchikey: str | None = None
    molecular_formula: str | None = None
    exact_mass: float | None = None
    formal_charge: int | None = None
    cas_number: str | None = None
    pubchem_cid: int | None = None
    chebi_id: str | None = None
    score: float = Field(default=0.0, ge=0.0, le=1.0)
    parse_valid: bool | None = None
    validation_notes: list[str] = Field(default_factory=list)
    provenance: list[ProvenanceRecord] = Field(default_factory=list)
    raw: dict[str, Any] = Field(default_factory=dict)


class CanonicalCompound(StrictModel):
    compound_id: str
    preferred_name: str | None = None
    reported_name: str
    normalized_name: str
    pubchem_cid: int | None = None
    chebi_id: str | None = None
    cas_number: str | None = None
    inchi: str | None = None
    inchikey: str | None = None
    canonical_smiles: str | None = None
    isomeric_smiles: str | None = None
    molecular_formula: str | None = None
    exact_mass: float | None = None
    formal_charge: int | None = None
    stereochemistry_status: StereochemistryStatus = StereochemistryStatus.UNKNOWN
    isomer_group_id: str | None = None
    tautomer_parent_id: str | None = None
    structure_source: str | None = None
    source_retrieved_at: str | None = None
    resolution_method: str
    resolution_confidence: float = Field(ge=0.0, le=1.0)
    manual_review_status: ManualReviewStatus
    reported_smiles: str | None = None
    legacy_reported_smiles: list[str] = Field(default_factory=list)
    reported_smiles_valid: bool | None = None
    identity_basis: str = "reported_name"
    adjudication_status: IdentityAdjudicationStatus = IdentityAdjudicationStatus.UNRESOLVED_PENDING
    adjudication_id: str | None = None
    adjudicated_name: str | None = None
    adjudication_notes: list[str] = Field(default_factory=list)
    adjudication_source: dict[str, Any] = Field(default_factory=dict)
    downstream_structure_eligible: bool = False
    resolved_identity: bool = False
    candidate_identity_set: list[IdentityCandidate] = Field(default_factory=list)
    conflict_flags: list[str] = Field(default_factory=list)
    provenance: list[ProvenanceRecord] = Field(default_factory=list)


class IdentityResolveItem(StrictModel):
    reported_name: str
    reported_smiles: str | None = None
    legacy_reported_smiles: list[str] = Field(default_factory=list)
    reported_formula: str | None = None
    retention_time_min: float | None = None
    library_match_quality: float | None = Field(default=None, ge=0, le=100)
    source_row_id: str | None = None
    candidate_names: list[str] = Field(default_factory=list)


class IdentityResolveRequest(StrictModel):
    items: list[IdentityResolveItem]
    online: bool | None = None
    sources: list[str] = Field(default_factory=lambda: ["pubchem", "chebi", "nist", "fooddb"])
    force_refresh: bool = False
    strict: bool = False


class IdentityResolveResponse(StrictModel):
    ok: bool
    compounds: list[CanonicalCompound]
    unresolved_count: int
    unresolved_pending_count: int = 0
    excluded_unresolved_count: int = 0
    manual_corrected_count: int = 0
    conflict_count: int
    manual_review_count: int
    adjudication_registry_sha256: str | None = None
    frozen_registry_sha256: str | None = None
    frozen_registry_used_count: int = 0
    artifacts: list[dict[str, Any]] = Field(default_factory=list)
