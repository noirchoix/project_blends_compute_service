from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import Field

from .common import ArtifactRef, StrictModel
from .profiles import PeakRecord


class RunStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETE = "complete"
    COMPLETE_WITH_WARNINGS = "complete_with_warnings"
    FAILED = "failed"


class RunRequest(StrictModel):
    run_name: str = "project_blends_full_analysis"
    dataset_id: str | None = "project_blends_reported_v1"
    records: list[PeakRecord] | None = None
    resolve_identities_online: bool = False
    refresh_identities: bool = False
    include_food_provenance: bool = True
    include_reaction_intelligence: bool = True
    include_molecular_screening: bool | None = None
    include_quantum_descriptors: bool = True  # deprecated v0.1.4 alias for molecular screening
    queue_external_quantum: bool = False
    include_exploratory_foodchem_ml: bool = False
    strict_lanes: list[str] = Field(default_factory=list)
    storage_context: dict[str, Any] = Field(default_factory=lambda: {"duration_days": 28, "container": "airtight", "temperature_c": None})
    metadata: dict[str, Any] = Field(default_factory=dict)


class RunSummary(StrictModel):
    run_id: str
    status: RunStatus
    created_at_utc: str
    updated_at_utc: str
    completed_at_utc: str | None = None
    stages: dict[str, dict[str, Any]] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    artifacts: list[ArtifactRef] = Field(default_factory=list)
    report_path: str | None = None
    rag_export_path: str | None = None
    release_locked: bool = False
