from __future__ import annotations

from pathlib import Path

from project_blends_compute.jobs import SQLiteJobQueue
from project_blends_compute.quantum.engines.external import ORCAEngine, XTBEngine
from project_blends_compute.quantum.engines.rdkit_engine import RDKitDescriptorEngine
from project_blends_compute.schemas.quantum import QuantumJobRequest, QuantumJobResponse, QuantumJobStatus, QuantumTask
from project_blends_compute.settings import Settings


class QuantumService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.queue = SQLiteJobQueue(settings.state_root / "quantum_jobs.sqlite3", table="quantum_jobs")
        self.work_root = settings.artifact_root / "quantum_jobs"
        self.work_root.mkdir(parents=True, exist_ok=True)
        self.engines = {
            "rdkit": RDKitDescriptorEngine(),
            "xtb": XTBEngine(settings.xtb_executable),
            "orca": ORCAEngine(settings.orca_executable),
        }

    def submit(self, request: QuantumJobRequest) -> QuantumJobResponse:
        if request.engine not in self.engines:
            raise ValueError(f"Unsupported quantum engine: {request.engine}")
        job_id = self.queue.submit("quantum", request.model_dump(mode="json"), max_attempts=2)
        return self.get(job_id)

    def get(self, job_id: str) -> QuantumJobResponse:
        row = self.queue.get(job_id)
        if not row:
            raise KeyError(job_id)
        return QuantumJobResponse(
            job_id=job_id,
            status=QuantumJobStatus(row["status"]),
            task=QuantumTask(row["payload"]["task"]),
            engine=row["payload"]["engine"],
            created_at_utc=row["created_at_utc"],
            updated_at_utc=row["updated_at_utc"],
            result=row.get("result"),
            error=row.get("error"),
            attempts=int(row.get("attempts", 0)),
            artifact_paths=row.get("artifact_paths") or [],
        )

    def run_job(self, job: dict, worker_id: str) -> None:
        job_id = job["job_id"]
        request = QuantumJobRequest.model_validate(job["payload"])
        engine = self.engines[request.engine]
        if not engine.available():
            self.queue.fail(job_id, {"code": "engine_unavailable", "engine": request.engine}, retryable=False)
            return
        work_dir = self.work_root / job_id
        try:
            result = engine.run(request, work_dir)
            self.queue.complete(job_id, result.result, [str(path) for path in result.artifact_paths])
        except Exception as exc:
            self.queue.fail(job_id, {"code": "engine_execution_failed", "type": type(exc).__name__, "message": str(exc)}, retryable=False)

    def run_inline(self, request: QuantumJobRequest, work_dir: Path) -> dict:
        if request.engine not in self.engines:
            raise ValueError(f"Unsupported quantum engine: {request.engine}")
        engine = self.engines[request.engine]
        if not engine.available():
            raise RuntimeError(f"Quantum engine unavailable: {request.engine}")
        result = engine.run(request, work_dir)
        return {"result": result.result, "artifact_paths": [str(path) for path in result.artifact_paths], "warnings": result.warnings}
