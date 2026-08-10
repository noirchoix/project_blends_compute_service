from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import Field, model_validator

from .common import StrictModel


class Timepoint(StrEnum):
    BEFORE_STORAGE = "before_storage"
    AFTER_STORAGE = "after_storage"


class PeakRecord(StrictModel):
    peak_id: str | None = None
    sample_id: str
    blend_id: str
    timepoint: Timepoint
    storage_days: int = Field(default=0, ge=0)
    plant_components: list[str] = Field(default_factory=list)
    plant_ratios: dict[str, float] = Field(default_factory=dict)
    reported_compound_name: str
    candidate_names: list[str] = Field(default_factory=list)
    retention_time_min: float | None = Field(default=None, ge=0)
    area_percent: float = Field(ge=0)
    library_match_quality: float | None = Field(default=None, ge=0, le=100)
    reported_smiles: str | None = None
    reported_class: str | None = None
    compound_id: str | None = None
    inchikey: str | None = None
    canonical_smiles: str | None = None
    identity_confidence: float | None = Field(default=None, ge=0, le=1)
    source_table: str | None = None
    source_row: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _storage_consistency(self) -> "PeakRecord":
        if self.timepoint == Timepoint.BEFORE_STORAGE and self.storage_days != 0:
            raise ValueError("before_storage records must use storage_days=0")
        if self.timepoint == Timepoint.AFTER_STORAGE and self.storage_days == 0:
            self.storage_days = 28
        return self


class ProfileIngestRequest(StrictModel):
    records: list[PeakRecord]
    dataset_id: str = "project_blends_reported_v1"
    source_document: str | None = None
    replace: bool = False


class ProfileIngestResponse(StrictModel):
    ok: bool
    dataset_id: str
    rows: int
    samples: list[str]
    artifact_path: str
    warnings: list[str] = Field(default_factory=list)


class ProfileAnalysisRequest(StrictModel):
    dataset_id: str | None = None
    records: list[PeakRecord] | None = None
    zero_replacement: float = Field(default=1e-6, gt=0)
    identity_key: str = "compound_id"
    confidence_weighted: bool = True


class ProfileMetric(StrictModel):
    sample_id: str
    blend_id: str
    before_count: int
    after_count: int
    shared_count: int
    weighted_jaccard: float
    bray_curtis_dissimilarity: float
    jensen_shannon_divergence: float
    aitchison_distance: float
    class_weighted_jaccard: float | None = None
    source_area_before: float
    source_area_after: float
    confidence_weighted_retention: float | None = None
    added_compounds: list[str] = Field(default_factory=list)
    removed_compounds: list[str] = Field(default_factory=list)
    shared_compounds: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class ProfileAnalysisResponse(StrictModel):
    ok: bool
    metrics: list[ProfileMetric]
    ranking: list[dict[str, Any]]
    method: dict[str, Any]
    artifacts: list[dict[str, Any]] = Field(default_factory=list)
