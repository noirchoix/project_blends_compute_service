from __future__ import annotations

import hashlib
import json
import sys
from dataclasses import asdict, is_dataclass
from importlib.resources import files
from typing import Any

from project_blends_compute.rdkit_qc import capture_rdkit_messages
from project_blends_compute.settings import Settings
from project_blends_compute.supporting.taxonomy_compat import install_modern_morgan_generator


EXECUTED_STATES = {"executed", "executed_no_match", "executed_with_unknowns", "executed_with_failures", "executed_no_input"}


def _eligible_compounds(compounds: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return one structure-eligible row per canonical molecular entity."""
    unique: dict[str, dict[str, Any]] = {}
    for compound in compounds:
        if not compound.get("downstream_structure_eligible", compound.get("resolved_identity")):
            continue
        if not (compound.get("isomeric_smiles") or compound.get("canonical_smiles")):
            continue
        key = str(compound.get("compound_id") or compound.get("inchikey") or compound.get("canonical_smiles"))
        unique.setdefault(key, compound)
    return list(unique.values())


def _as_payload(value: Any) -> dict[str, Any]:
    if is_dataclass(value):
        return asdict(value)
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return value
    return {"value": repr(value)}


def _reported_names(compound: dict[str, Any]) -> list[str]:
    names = compound.get("reported_names")
    if isinstance(names, list) and names:
        return [str(name) for name in names]
    name = compound.get("reported_name")
    return [str(name)] if name else []


def _preferred_name(compound: dict[str, Any]) -> str | None:
    return compound.get("preferred_name") or (_reported_names(compound)[0] if _reported_names(compound) else None)


def _load_taxonomy_contract() -> dict[str, Any]:
    path = files("project_blends_compute").joinpath("resources", "reference", "taxonomy_coconut_v1_contract.json")
    return json.loads(path.read_text(encoding="utf-8"))



_TAXONOMY_REGISTRY_KEYS = {
    "metadata_superclass.json": "metadata_superclass",
    "metadata_class.json": "metadata_class",
    "config.normalized.json": "config_normalized",
    "classes_lookup.json": "classes_lookup",
    "model_superclass.txt": "model_superclass",
    "model_class.txt": "model_class",
}


def _sha256_file(path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _verify_taxonomy_artifact_files(registry: Any, contract: dict[str, Any]) -> dict[str, Any]:
    expected = contract.get("files") or {}
    checked: list[dict[str, Any]] = []
    mismatches: list[dict[str, Any]] = []
    for filename, registry_key in _TAXONOMY_REGISTRY_KEYS.items():
        expected_hash = expected.get(filename)
        if not expected_hash:
            continue
        path = registry.require_path("taxonomy_coconut", registry_key)
        actual_hash = _sha256_file(path)
        row = {
            "filename": filename,
            "registry_key": registry_key,
            "path": str(path),
            "expected_sha256": expected_hash,
            "actual_sha256": actual_hash,
            "match": actual_hash == expected_hash,
        }
        checked.append(row)
        if not row["match"]:
            mismatches.append(row)
    return {
        "status": "matched" if checked and not mismatches else "mismatch" if mismatches else "not_checked",
        "verification_mode": "sha256_strict",
        "checked_files": checked,
        "mismatches": mismatches,
    }

def _taxonomy_contract_matches(payload: dict[str, Any], contract: dict[str, Any]) -> bool:
    return bool(
        payload.get("model_version") == contract.get("model_version")
        and payload.get("artifact_version") == contract.get("artifact_version")
        and payload.get("schema_version") == contract.get("feature_schema_version")
    )


def _normalize_taxonomy_probabilities(payload: dict[str, Any]) -> dict[str, Any]:
    """Preserve full provider probabilities while keeping a compact top-5 view."""
    for level in ("chemical_super_class", "chemical_class"):
        node = payload.get(level)
        if not isinstance(node, dict):
            continue
        full = list(node.get("top5") or [])
        node["probabilities"] = full
        node["top5"] = full[:5]
        node["probability_mass_covered"] = float(sum(float(row.get("prob") or 0.0) for row in full))
        confidence = float(node.get("confidence") or 0.0)
        node["confidence_band"] = "high" if confidence >= 0.80 else "uncertain"
    return payload


class _RxnBridgeRuntime:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.registry: Any | None = None
        self.error: str | None = None
        self.ready = False
        root = settings.rxn_bridge_project_root
        registry_path = settings.rxn_artifact_registry
        if root is None or not root.exists():
            self.error = "rxn_bridge_project_root_missing"
            return
        if registry_path is None or not registry_path.exists():
            self.error = "rxn_artifact_registry_missing"
            return
        root_text = str(root.resolve())
        if root_text not in sys.path:
            sys.path.insert(0, root_text)
        try:
            from reaction_framework.providers.artifact_registry import ArtifactRegistry

            self.registry = ArtifactRegistry(registry_path)
            self.ready = True
        except Exception as exc:
            self.error = f"{type(exc).__name__}:{exc}"


class DESSRuntimeLane:
    name = "dess"

    def __init__(self, runtime: _RxnBridgeRuntime) -> None:
        self.provider: Any | None = None
        self.error: str | None = runtime.error
        self.ready = False
        if not runtime.ready or runtime.registry is None:
            return
        try:
            from reaction_framework.providers.dess_provider import DESSPhysicsProvider

            self.provider = DESSPhysicsProvider.from_registry(runtime.registry, lane="dess_physics")
            self.ready = True
        except Exception as exc:
            self.error = f"{type(exc).__name__}:{exc}"

    def evaluate(self, compounds: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        eligible = _eligible_compounds(compounds)
        if not self.ready or self.provider is None:
            return [], {
                "status": "runtime_unavailable",
                "input_compounds": len(eligible),
                "matched": 0,
                "no_match": 0,
                "failed": 0,
                "error": self.error,
                "execution_mode": "precomputed_artifact_lookup",
            }
        evidence: list[dict[str, Any]] = []
        matched = 0
        no_match = 0
        failures = 0
        for compound in eligible:
            smiles = compound.get("isomeric_smiles") or compound.get("canonical_smiles")
            try:
                result = self.provider.score_reactants([smiles])
                payload = _as_payload(result)
                has_match = payload.get("p_physics") is not None or payload.get("stability_support") is not None
                if has_match:
                    matched += 1
                    evidence.append(
                        {
                            "lane": self.name,
                            "compound_id": compound.get("compound_id"),
                            "reported_names": _reported_names(compound),
                            "preferred_name": _preferred_name(compound),
                            "canonical_smiles": smiles,
                            "evidence_type": "precomputed_dess_physics_support",
                            "execution_mode": "precomputed_artifact_lookup",
                            "result": payload,
                            "claim_boundary": "advisory precomputed DESS physics evidence; not a storage-reaction observation or live checkpoint inference",
                        }
                    )
                else:
                    no_match += 1
            except Exception:
                failures += 1
        if not eligible:
            status = "executed_no_input"
        elif failures and (matched or no_match):
            status = "executed_with_failures"
        elif failures and not matched and not no_match:
            status = "execution_failed"
        else:
            status = "executed" if matched else "executed_no_match"
        return evidence, {
            "status": status,
            "input_compounds": len(eligible),
            "matched": matched,
            "no_match": no_match,
            "failed": failures,
            "error": self.error,
            "execution_mode": "precomputed_artifact_lookup",
        }


class TaxonomyRuntimeLane:
    name = "taxonomy"

    def __init__(self, runtime: _RxnBridgeRuntime) -> None:
        self.provider: Any | None = None
        self.error: str | None = runtime.error
        self.ready = False
        self.contract = _load_taxonomy_contract()
        self.artifact_verification: dict[str, Any] = {"status": "not_checked", "verification_mode": "sha256_strict"}
        self.morgan_compat: dict[str, Any] = {"status": "not_attempted"}
        if not runtime.ready or runtime.registry is None:
            return
        try:
            self.artifact_verification = _verify_taxonomy_artifact_files(runtime.registry, self.contract)
            if self.artifact_verification.get("status") != "matched":
                raise ValueError("taxonomy_coconut_v1_artifact_sha256_mismatch")
            # The upstream feature builder remains authoritative. Only its deprecated
            # Morgan API call is upgraded to the equivalent generator API in memory.
            self.morgan_compat = install_modern_morgan_generator()
            from reaction_framework.providers.coco_provider import CocoTaxonomyProvider

            self.provider = CocoTaxonomyProvider.from_registry(runtime.registry, lane="taxonomy_coconut")
            self.ready = True
        except Exception as exc:
            self.error = f"{type(exc).__name__}:{exc}"

    def evaluate(self, compounds: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        eligible = _eligible_compounds(compounds)
        contract_summary = {
            "artifact_version": self.contract.get("artifact_version"),
            "model_version": self.contract.get("model_version"),
            "feature_schema_version": self.contract.get("feature_schema_version"),
            "uploaded_artifact_sha256": self.contract.get("uploaded_artifact_sha256"),
        }
        if not self.ready or self.provider is None:
            return [], {
                "status": "runtime_unavailable",
                "input_compounds": len(eligible),
                "predicted": 0,
                "unknown": 0,
                "failed": 0,
                "contract_mismatches": 0,
                "error": self.error,
                "execution_mode": "lightgbm_coconut_taxonomy_inference",
                "artifact_contract": contract_summary,
                "artifact_file_verification": self.artifact_verification,
                "morgan_compatibility": self.morgan_compat,
            }
        evidence: list[dict[str, Any]] = []
        predicted = 0
        unknown = 0
        failures = 0
        contract_mismatches = 0
        rdkit_qc: list[dict[str, Any]] = []
        for compound in eligible:
            smiles = compound.get("isomeric_smiles") or compound.get("canonical_smiles")
            try:
                # 256 exceeds both known class counts (20 and 198), so the upstream
                # provider returns the full probability distributions in one model run.
                with capture_rdkit_messages() as messages:
                    result = self.provider.predict_taxonomy(smiles, top_k=256)
                rdkit_qc.extend({"compound_id": compound.get("compound_id"), **row} for row in messages)
                payload = _normalize_taxonomy_probabilities(_as_payload(result))
                if not _taxonomy_contract_matches(payload, self.contract):
                    contract_mismatches += 1
                    failures += 1
                    continue
                super_label = (payload.get("chemical_super_class") or {}).get("label")
                class_label = (payload.get("chemical_class") or {}).get("label")
                is_unknown = super_label in {None, "unknown"} and class_label in {None, "unknown"}
                unknown += int(is_unknown)
                predicted += int(not is_unknown)
                evidence.append(
                    {
                        "lane": self.name,
                        "compound_id": compound.get("compound_id"),
                        "reported_names": _reported_names(compound),
                        "preferred_name": _preferred_name(compound),
                        "canonical_smiles": smiles,
                        "evidence_type": "coconut_taxonomy_model_prediction",
                        "execution_mode": "lightgbm_coconut_taxonomy_inference",
                        "result": payload,
                        "claim_boundary": "model-derived chemical taxonomy; supporting classification only and not compound identity confirmation",
                    }
                )
            except Exception:
                failures += 1
        if not eligible:
            status = "executed_no_input"
        elif contract_mismatches:
            status = "execution_failed" if not predicted and not unknown else "executed_with_failures"
        elif failures and (predicted or unknown):
            status = "executed_with_failures"
        elif failures and not predicted and not unknown:
            status = "execution_failed"
        else:
            status = "executed" if predicted and not unknown else "executed_with_unknowns"
        return evidence, {
            "status": status,
            "input_compounds": len(eligible),
            "predicted": predicted,
            "unknown": unknown,
            "failed": failures,
            "contract_mismatches": contract_mismatches,
            "error": self.error,
            "execution_mode": "lightgbm_coconut_taxonomy_inference",
            "artifact_contract": contract_summary,
            "artifact_file_verification": self.artifact_verification,
            "artifact_contract_status": "matched" if contract_mismatches == 0 and predicted + unknown > 0 and self.artifact_verification.get("status") == "matched" else "not_verified",
            "morgan_compatibility": self.morgan_compat,
            "rdkit_qc_messages": rdkit_qc,
        }


class SupportingEvidenceService:
    """Execute bounded supporting lanes without promoting them to primary evidence."""

    def __init__(self, settings: Settings) -> None:
        runtime = _RxnBridgeRuntime(settings)
        self.lanes = {
            "dess": DESSRuntimeLane(runtime),
            "taxonomy": TaxonomyRuntimeLane(runtime),
        }

    def readiness(self) -> dict[str, dict[str, Any]]:
        result: dict[str, dict[str, Any]] = {}
        for name, lane in self.lanes.items():
            result[name] = {
                "ready": bool(getattr(lane, "ready", False)),
                "error": getattr(lane, "error", None),
            }
        return result

    def evaluate(self, compounds: list[dict[str, Any]]) -> dict[str, Any]:
        evidence: list[dict[str, Any]] = []
        lane_status: dict[str, str] = {}
        execution: dict[str, dict[str, Any]] = {}
        warnings: list[str] = []
        for name, lane in self.lanes.items():
            rows, summary = lane.evaluate(compounds)
            evidence.extend(rows)
            execution[name] = summary
            lane_status[name] = str(summary.get("status") or "runtime_unavailable")
            if summary.get("error") and lane_status[name] not in EXECUTED_STATES:
                warnings.append(f"{name}:{summary['error']}")
            if int(summary.get("contract_mismatches") or 0):
                warnings.append(f"{name}:authoritative_artifact_contract_mismatch")
        return {
            "schema_version": "project_blends_supporting_evidence.v3",
            "lane_status": lane_status,
            "execution": execution,
            "evidence": evidence,
            "warnings": sorted(set(warnings)),
            "removed_redundant_lane": "standalone_coco_classifier",
            "claim_boundary": "DESS and COCONUT taxonomy outputs are supporting computational evidence only; they never replace identity, documented occurrence, or direct reaction evidence",
        }
