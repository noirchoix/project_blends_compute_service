from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from project_blends_compute.schemas.quantum import QuantumJobRequest


@dataclass(slots=True)
class EngineResult:
    result: dict[str, Any]
    artifact_paths: list[Path] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


class QuantumEngine(ABC):
    name: str

    @abstractmethod
    def available(self) -> bool:
        raise NotImplementedError

    @abstractmethod
    def run(self, request: QuantumJobRequest, work_dir: Path) -> EngineResult:
        raise NotImplementedError
