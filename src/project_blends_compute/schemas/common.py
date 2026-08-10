from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, validate_assignment=True)


class ClaimClass(StrEnum):
    OBSERVED = "observed"
    DERIVED = "derived"
    LITERATURE_SUPPORTED = "literature_supported"
    COMPUTATIONALLY_SUPPORTED = "computationally_supported"
    HYPOTHESIZED = "hypothesized"
    UNRESOLVED = "unresolved"
    REJECTED = "rejected"


class EvidenceGrade(StrEnum):
    A = "A_direct"
    B = "B_convergent"
    C = "C_plausible"
    D = "D_weak"
    U = "U_unresolved"
    R = "R_rejected"


class LaneStatus(StrEnum):
    AVAILABLE = "available"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"
    DISABLED = "disabled"


class ArtifactRef(StrictModel):
    logical_name: str
    path: str
    sha256: str
    media_type: str
    rows: int | None = None
    schema_version: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ProvenanceRecord(StrictModel):
    source: str
    source_id: str | None = None
    source_uri: str | None = None
    source_version: str | None = None
    retrieved_at_utc: str | None = None
    evidence_type: str | None = None
    source_quality: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class HealthResponse(StrictModel):
    ok: bool
    service: str
    version: str
    environment: str


class ReadyLane(StrictModel):
    lane: str
    status: LaneStatus
    required: bool = False
    detail: str | None = None
    paths: list[str] = Field(default_factory=list)


class ReadyResponse(StrictModel):
    ready: bool
    lanes: list[ReadyLane]
    effective_paths: dict[str, str | None]
