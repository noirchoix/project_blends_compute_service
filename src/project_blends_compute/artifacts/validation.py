from __future__ import annotations

from pathlib import Path
from typing import Any

from project_blends_compute.artifacts.store import ArtifactStore
from project_blends_compute.schemas.common import LaneStatus, ReadyLane
from project_blends_compute.settings import Settings


REQUIRED_LOGICAL_PREFIXES = {
    "profile_metrics",
    "compound_registry",
    "integrated_report",
    "chemrag_export",
}


def readiness_report(settings: Settings, manager: Any) -> dict[str, Any]:
    supporting = manager.supporting.readiness()
    lanes = [
        ReadyLane(lane="identity", status=LaneStatus.AVAILABLE, required=True, detail="name-first external identity resolution with explicit manual adjudication support"),
        ReadyLane(lane="profiles", status=LaneStatus.AVAILABLE, required=True, detail="deterministic compositional metrics available"),
        ReadyLane(lane="pipeline_fooddb", status=LaneStatus.AVAILABLE if manager.fooddb.available else LaneStatus.UNAVAILABLE, required="pipeline_fooddb" in settings.strict_ready_lanes, detail=manager.fooddb.load_error),
        ReadyLane(lane="foodchem_ml", status=LaneStatus.AVAILABLE if manager.foodchem.available else LaneStatus.DISABLED, required=False, detail="exploratory only; never occurrence evidence"),
        ReadyLane(lane="rxn_bridge", status=LaneStatus.AVAILABLE if manager.rxn_bridge.available else LaneStatus.UNAVAILABLE, required="rxn_bridge" in settings.strict_ready_lanes, detail=manager.rxn_bridge.error),
        ReadyLane(lane="reaction_curation", status=LaneStatus.AVAILABLE if manager.reaction_curation.available else LaneStatus.UNAVAILABLE, required="reaction_curation" in settings.strict_ready_lanes, detail=manager.reaction_curation.error),
        ReadyLane(lane="dess", status=LaneStatus.AVAILABLE if supporting["dess"]["ready"] else LaneStatus.UNAVAILABLE, required="dess" in settings.strict_ready_lanes, detail=supporting["dess"].get("error")),
        ReadyLane(lane="taxonomy", status=LaneStatus.AVAILABLE if supporting["taxonomy"]["ready"] else LaneStatus.UNAVAILABLE, required="taxonomy" in settings.strict_ready_lanes, detail=supporting["taxonomy"].get("error")),
        ReadyLane(lane="rdkit_screening", status=LaneStatus.AVAILABLE, required=True, detail="cheminformatics descriptors, ETKDG conformers, and MMFF/UFF screening; not quantum chemistry"),
        ReadyLane(lane="xtb", status=LaneStatus.AVAILABLE if settings.xtb_executable and settings.xtb_executable.exists() else LaneStatus.UNAVAILABLE, required="xtb" in settings.strict_ready_lanes),
        ReadyLane(lane="orca", status=LaneStatus.AVAILABLE if settings.orca_executable and settings.orca_executable.exists() else LaneStatus.UNAVAILABLE, required="orca" in settings.strict_ready_lanes),
    ]
    ready = all((not lane.required) or lane.status == LaneStatus.AVAILABLE for lane in lanes)
    return {"ready": ready, "lanes": [lane.model_dump(mode="json") for lane in lanes], "effective_paths": settings.effective_paths()}


def validate_run_for_release(store: ArtifactStore, run_id: str, *, require_quantum: bool = False) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    try:
        manifest = store.load_manifest(run_id)
        checks.append({"name": "manifest_integrity", "pass": True})
    except Exception as exc:
        return {"strict_pass": False, "checks": [{"name": "manifest_integrity", "pass": False, "error": repr(exc)}]}

    logical = {str(artifact.get("logical_name", "")) for artifact in manifest.get("artifacts", [])}
    missing = sorted(prefix for prefix in REQUIRED_LOGICAL_PREFIXES if not any(name == prefix or name.startswith(prefix + ".") for name in logical))
    checks.append({"name": "required_artifacts", "pass": not missing, "missing": missing})

    report_path = store.run_dir(run_id) / "reports" / "integrated_report.json"
    report: dict[str, Any] = {}
    if report_path.exists():
        import json
        report = json.loads(report_path.read_text(encoding="utf-8"))
    identity = report.get("identity", {})
    pending = int(identity.get("unresolved_pending_count", identity.get("unresolved_count") or 0) or 0)
    excluded = int(identity.get("excluded_unresolved_count") or 0)
    identity_pass = bool(identity.get("compounds")) and pending == 0 and int(identity.get("conflict_count") or 0) == 0
    checks.append({"name": "identity_qc", "pass": identity_pass, "unresolved_pending_count": pending, "excluded_unresolved_count": excluded, "manual_corrected_count": identity.get("manual_corrected_count"), "conflict_count": identity.get("conflict_count")})
    lane_status = report.get("uncertainty", {}).get("lane_status", {})
    core_lanes = ["pipeline_fooddb", "rxn_bridge", "reaction_curation"]
    unavailable_core = [lane for lane in core_lanes if lane_status.get(lane) != "available"]
    checks.append({"name": "core_evidence_lanes", "pass": not unavailable_core, "unavailable": unavailable_core})
    evaluations = report.get("reactions", {}).get("evaluations", [])
    bad = [e for e in evaluations if not e.get("claim_boundary")]
    checks.append({"name": "reaction_claim_boundaries", "pass": not bad, "missing_count": len(bad)})
    checks.append({"name": "evidence_packets", "pass": bool(report.get("evidence_packets"))})
    if require_quantum:
        checks.append({"name": "quantum_results", "pass": bool(report.get("quantum", {}).get("results")), "queued_jobs_are_not_results": True})
    strict_pass = all(bool(check.get("pass")) for check in checks)
    return {"strict_pass": strict_pass, "run_id": run_id, "checks": checks}
