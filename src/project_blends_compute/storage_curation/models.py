from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import Field

from project_blends_compute.schemas.common import StrictModel
from project_blends_compute.schemas.reactions import ExplanationType


class EvidenceDirectness(StrEnum):
    DIRECT = "direct"
    ANALOGOUS = "analogous"
    COMPUTATIONAL = "computational"
    REVIEW_SUMMARY = "review_summary"
    UNRESOLVED = "unresolved"


class EvidenceModality(StrEnum):
    EXPERIMENTAL = "experimental"
    COMPUTATIONAL = "computational"
    MIXED = "mixed"


class StorageReactionRecord(StrictModel):
    reaction_id: str
    source_id: str
    source_doi: str | None = None
    source_type: str
    precursor_compound_id: str | None = None
    product_compound_id: str | None = None
    precursor_name: str | None = None
    product_name: str | None = None
    precursor_smiles: str
    product_smiles: str
    mapped_reaction_smiles: str | None = None
    transformation_family: str
    evidence_directness: EvidenceDirectness
    experimental_or_computational: EvidenceModality
    matrix: str | None = None
    solvent: str | None = None
    temperature_c: float | None = None
    duration_h: float | None = None
    oxygen_exposure: str | None = None
    atmosphere: str | None = None
    light_exposure: str | None = None
    ph: float | None = Field(default=None, alias="pH")
    water_activity: float | None = None
    humidity: float | None = None
    container_material: str | None = None
    headspace: str | None = None
    catalyst_or_initiator: str | None = None
    analytical_method: str | None = None
    reported_yield: float | None = None
    reported_abundance_change: str | None = None
    identification_level: str | None = None
    notes: str | None = None
    provenance: dict[str, Any] = Field(default_factory=dict)


class StorageEvidenceSource(StrictModel):
    source_id: str
    doi: str | None = None
    title: str | None = None
    authors: str | None = None
    year: int | None = None
    journal: str | None = None
    source_type: str
    primary_source: bool = False
    retrieval_date_utc: str | None = None
    notes: str | None = None


class NonReactionExplanationRecord(StrictModel):
    hypothesis_id: str
    sample_id: str
    compound_id: str | None = None
    compound_name: str
    explanation_type: ExplanationType
    direction: str
    evidence_source_ids: list[str] = Field(default_factory=list)
    notes: str | None = None


class IdentityLinkageRecord(StrictModel):
    compound_id: str
    preferred_name: str
    inchikey: str | None = None
    canonical_smiles: str | None = None
    source_ids: list[str] = Field(default_factory=list)


class StorageCurationBuildRequest(StrictModel):
    dataset_name: str = "storage_reaction_evidence"
    version: str = "v1"
    reactions: list[StorageReactionRecord]
    sources: list[StorageEvidenceSource] = Field(default_factory=list)
    nonreaction_explanations: list[NonReactionExplanationRecord] = Field(default_factory=list)
    identity_linkage: list[IdentityLinkageRecord] = Field(default_factory=list)
    overwrite_registry_version: bool = False


class StorageCurationBuildResponse(StrictModel):
    ok: bool
    dataset_name: str
    version: str
    output_dir: str
    registry_path: str
    artifacts: dict[str, str]
    quality_report: dict[str, Any]
