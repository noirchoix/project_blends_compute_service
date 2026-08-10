from __future__ import annotations

import json
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import JSONResponse

from project_blends_compute.api.dependencies import get_manager, require_api_key
from project_blends_compute.artifacts.validation import readiness_report, validate_run_for_release
from project_blends_compute.errors import ProjectBlendsError
from project_blends_compute.orchestrator import RunManager
from project_blends_compute.profiles.repository import ProfileRepository
from project_blends_compute.schemas.common import HealthResponse, ReadyResponse
from project_blends_compute.schemas.identity import IdentityResolveRequest, IdentityResolveResponse
from project_blends_compute.schemas.profiles import ProfileAnalysisRequest, ProfileAnalysisResponse, ProfileIngestRequest, ProfileIngestResponse
from project_blends_compute.schemas.provenance import ProvenanceEvaluateRequest, ProvenanceEvaluateResponse
from project_blends_compute.schemas.quantum import QuantumJobRequest, QuantumJobResponse
from project_blends_compute.schemas.reactions import ReactionEvaluateRequest, ReactionEvaluateResponse, ReactionGenerateRequest, ReactionGenerateResponse
from project_blends_compute.schemas.runs import RunRequest, RunSummary
from project_blends_compute.settings import Settings, hydrate_paths_from_manifest
from project_blends_compute.version import __version__


def create_app(settings: Settings | None = None) -> FastAPI:
    config = hydrate_paths_from_manifest(settings or Settings())
    config.ensure_runtime_dirs()
    manager = RunManager(config)
    app = FastAPI(
        title="Project Blends Compute Service",
        version=__version__,
        description="Artifact-backed identity, compositional-profile, provenance, storage-evidence, gated reaction-intelligence, molecular-screening and quantum-chemistry service.",
    )
    app.state.settings = config
    app.state.manager = manager

    @app.exception_handler(ProjectBlendsError)
    async def _domain_error(_, exc: ProjectBlendsError):
        return JSONResponse(status_code=exc.status_code, content=exc.to_dict())

    @app.exception_handler(ValueError)
    async def _value_error(_, exc: ValueError):
        return JSONResponse(status_code=422, content={"code": "validation_error", "message": str(exc)})

    @app.get("/health", response_model=HealthResponse, tags=["service"])
    def health() -> HealthResponse:
        return HealthResponse(ok=True, service=config.service_name, version=__version__, environment=config.environment)

    @app.get("/ready", response_model=ReadyResponse, tags=["service"])
    def ready() -> ReadyResponse:
        return ReadyResponse.model_validate(readiness_report(config, manager))

    @app.post("/v1/identity/resolve", response_model=IdentityResolveResponse, dependencies=[Depends(require_api_key)], tags=["identity"])
    async def resolve_identity(request: IdentityResolveRequest, service: RunManager = Depends(get_manager)) -> IdentityResolveResponse:
        return await service.identity.resolve(request)

    @app.post("/v1/profiles/ingest", response_model=ProfileIngestResponse, dependencies=[Depends(require_api_key)], tags=["profiles"])
    def ingest_profile(request: ProfileIngestRequest, service: RunManager = Depends(get_manager)) -> ProfileIngestResponse:
        return service.profiles.ingest(request)

    @app.post("/v1/profiles/analyse", response_model=ProfileAnalysisResponse, dependencies=[Depends(require_api_key)], tags=["profiles"])
    def analyse_profile(request: ProfileAnalysisRequest, service: RunManager = Depends(get_manager)) -> ProfileAnalysisResponse:
        return service.profile_service.analyse(request)

    @app.post("/v1/provenance/evaluate", response_model=ProvenanceEvaluateResponse, dependencies=[Depends(require_api_key)], tags=["provenance"])
    async def provenance(request: ProvenanceEvaluateRequest, service: RunManager = Depends(get_manager)) -> ProvenanceEvaluateResponse:
        return await service.provenance.evaluate(request)

    @app.post("/v1/reactions/generate", response_model=ReactionGenerateResponse, dependencies=[Depends(require_api_key)], tags=["reactions"])
    def generate_reactions(request: ReactionGenerateRequest, service: RunManager = Depends(get_manager)) -> ReactionGenerateResponse:
        return service.reactions.generate(request)

    @app.post("/v1/reactions/evaluate", response_model=ReactionEvaluateResponse, dependencies=[Depends(require_api_key)], tags=["reactions"])
    def evaluate_reactions(request: ReactionEvaluateRequest, service: RunManager = Depends(get_manager)) -> ReactionEvaluateResponse:
        return service.reactions.evaluate(request)

    @app.post("/v1/quantum/jobs", response_model=QuantumJobResponse, dependencies=[Depends(require_api_key)], tags=["quantum"])
    def submit_quantum(request: QuantumJobRequest, service: RunManager = Depends(get_manager)) -> QuantumJobResponse:
        return service.quantum.submit(request)

    @app.get("/v1/quantum/jobs/{job_id}", response_model=QuantumJobResponse, dependencies=[Depends(require_api_key)], tags=["quantum"])
    def get_quantum(job_id: str, service: RunManager = Depends(get_manager)) -> QuantumJobResponse:
        try:
            return service.quantum.get(job_id)
        except KeyError as exc:
            raise HTTPException(404, detail={"code": "job_not_found", "job_id": job_id}) from exc

    @app.post("/v1/runs", response_model=RunSummary, dependencies=[Depends(require_api_key)], tags=["runs"])
    async def create_run(request: RunRequest, service: RunManager = Depends(get_manager)) -> RunSummary:
        return await service.execute(request)

    @app.get("/v1/runs", dependencies=[Depends(require_api_key)], tags=["runs"])
    def list_runs(service: RunManager = Depends(get_manager)):
        return {"runs": service.list()}

    @app.get("/v1/runs/{run_id}", response_model=RunSummary, dependencies=[Depends(require_api_key)], tags=["runs"])
    def get_run(run_id: str, service: RunManager = Depends(get_manager)) -> RunSummary:
        try:
            return service.get(run_id)
        except KeyError as exc:
            raise HTTPException(404, detail={"code": "run_not_found", "run_id": run_id}) from exc

    @app.get("/v1/runs/{run_id}/artifacts", dependencies=[Depends(require_api_key)], tags=["runs"])
    def get_artifacts(run_id: str, service: RunManager = Depends(get_manager)):
        try:
            manifest = service.store.load_manifest(run_id)
        except FileNotFoundError as exc:
            raise HTTPException(404, detail={"code": "run_artifacts_not_found", "run_id": run_id}) from exc
        return manifest

    @app.post("/v1/runs/{run_id}/release", dependencies=[Depends(require_api_key)], tags=["runs"])
    def release_run(run_id: str, require_quantum: bool = False, release_id: str | None = None, service: RunManager = Depends(get_manager)):
        acceptance = validate_run_for_release(service.store, run_id, require_quantum=require_quantum)
        if not acceptance.get("strict_pass"):
            raise HTTPException(422, detail={"code": "release_acceptance_failed", "acceptance": acceptance})
        path = service.store.lock_release(run_id, acceptance, release_id)
        return {"ok": True, "release_manifest": str(path), "acceptance": acceptance}

    @app.get("/v1/runs/{run_id}/report", dependencies=[Depends(require_api_key)], tags=["runs"])
    def get_report(run_id: str, service: RunManager = Depends(get_manager)):
        path = service.store.run_dir(run_id) / "reports" / "integrated_report.json"
        if not path.exists():
            raise HTTPException(404, detail={"code": "run_report_not_found", "run_id": run_id})
        return json.loads(path.read_text(encoding="utf-8"))

    return app


app = create_app()
