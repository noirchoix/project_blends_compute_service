from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import Field

from .common import StrictModel


class QuantumJobStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class QuantumTask(StrEnum):
    DESCRIPTORS = "descriptors"
    CONFORMER_SEARCH = "conformer_search"
    GEOMETRY_OPTIMIZATION = "geometry_optimization"
    SINGLE_POINT = "single_point"
    THERMOCHEMISTRY = "thermochemistry"
    REACTION_ENERGY = "reaction_energy"


class QuantumMolecule(StrictModel):
    compound_id: str | None = None
    name: str
    smiles: str
    charge: int = 0
    multiplicity: int = Field(default=1, ge=1)


class QuantumJobRequest(StrictModel):
    task: QuantumTask = QuantumTask.DESCRIPTORS
    engine: str = "rdkit"
    molecules: list[QuantumMolecule]
    reaction_smiles: str | None = None
    solvent: str | None = "n-hexane"
    temperature_k: float = Field(default=298.15, gt=0)
    method: str | None = None
    basis: str | None = None
    max_conformers: int = Field(default=20, ge=1, le=1000)
    metadata: dict[str, Any] = Field(default_factory=dict)


class QuantumJobResponse(StrictModel):
    job_id: str
    status: QuantumJobStatus
    task: QuantumTask
    engine: str
    created_at_utc: str
    updated_at_utc: str
    result: dict[str, Any] | None = None
    error: dict[str, Any] | None = None
    attempts: int = 0
    artifact_paths: list[str] = Field(default_factory=list)
