from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from project_blends_compute.identity.chemistry import validate_structure
from project_blends_compute.schemas.common import ProvenanceRecord
from project_blends_compute.schemas.identity import IdentityCandidate
from project_blends_compute.utils import canonical_json_bytes, normalize_name, stable_hash


@dataclass(frozen=True, slots=True)
class IdentityAdjudication:
    adjudication_id: str
    reported_name: str
    status: str
    adjudicated_name: str | None
    reason: str | None
    source: dict[str, Any]
    structure: dict[str, Any]
    review: dict[str, Any]


class IdentityAdjudicationRegistry:
    """Read-only manual identity adjudications with structure-level validation.

    The registry never rewrites the experimental reported name. A manual correction
    supplies a separately versioned canonical identity for downstream structure-based
    computation and retains the original GC-MS/library annotation as provenance.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self.schema_version = "project_blends.identity_adjudications.v1"
        self._records: dict[str, IdentityAdjudication] = {}
        self.content_sha256 = stable_hash("{}")
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("identity adjudication registry root must be a JSON object")
        self.schema_version = str(payload.get("schema_version") or self.schema_version)
        records = payload.get("records") or []
        if not isinstance(records, list):
            raise ValueError("identity adjudication registry records must be a list")
        self.content_sha256 = stable_hash(canonical_json_bytes(payload).decode("utf-8"))
        for raw in records:
            if not isinstance(raw, dict):
                continue
            record = IdentityAdjudication(
                adjudication_id=str(raw["adjudication_id"]),
                reported_name=str(raw["reported_name"]),
                status=str(raw["status"]),
                adjudicated_name=str(raw["adjudicated_name"]) if raw.get("adjudicated_name") else None,
                reason=str(raw["reason"]) if raw.get("reason") else None,
                source=dict(raw.get("source") or {}),
                structure=dict(raw.get("structure") or {}),
                review=dict(raw.get("review") or {}),
            )
            key = normalize_name(record.reported_name)
            if key in self._records:
                raise ValueError(f"duplicate identity adjudication for reported name: {record.reported_name}")
            self._records[key] = record

    def get(self, reported_name: str) -> IdentityAdjudication | None:
        return self._records.get(normalize_name(reported_name))

    def all(self) -> list[IdentityAdjudication]:
        return list(self._records.values())

    def candidate(self, record: IdentityAdjudication) -> IdentityCandidate | None:
        if record.status != "manual_corrected":
            return None
        structure = record.structure
        validation = validate_structure(smiles=structure.get("smiles"), inchi=structure.get("inchi"))
        if not validation.parse_valid:
            raise ValueError(f"manual adjudication {record.adjudication_id} contains an invalid structure")

        expected_inchikey = str(structure.get("inchikey") or "") or None
        expected_formula = str(structure.get("molecular_formula") or "") or None
        expected_inchi = str(structure.get("inchi") or "") or None
        if expected_inchikey and validation.inchikey != expected_inchikey:
            raise ValueError(
                f"manual adjudication {record.adjudication_id} InChIKey mismatch: "
                f"expected {expected_inchikey}, calculated {validation.inchikey}"
            )
        if expected_formula and validation.molecular_formula != expected_formula:
            raise ValueError(
                f"manual adjudication {record.adjudication_id} formula mismatch: "
                f"expected {expected_formula}, calculated {validation.molecular_formula}"
            )
        if expected_inchi and validation.inchi != expected_inchi:
            raise ValueError(f"manual adjudication {record.adjudication_id} InChI mismatch")

        expected_mass = structure.get("exact_mass")
        if expected_mass is not None and validation.exact_mass is not None:
            if abs(float(expected_mass) - float(validation.exact_mass)) > 1e-5:
                raise ValueError(
                    f"manual adjudication {record.adjudication_id} exact-mass mismatch: "
                    f"expected {expected_mass}, calculated {validation.exact_mass}"
                )

        source = record.source
        provenance = ProvenanceRecord(
            source=str(source.get("authority") or "manual_adjudication"),
            source_id=str(source.get("source_id") or "") or None,
            source_uri=source.get("source_uri"),
            evidence_type=str(source.get("evidence_type") or "manual_identity_adjudication"),
            source_quality="official_database_manual_adjudication",
            metadata={
                "adjudication_id": record.adjudication_id,
                "reported_name_preserved": record.reported_name,
                "adjudicated_name": record.adjudicated_name,
                "reason": record.reason,
            },
        )
        return IdentityCandidate(
            source="manual_pubchem_adjudication",
            source_id=str(source.get("source_id") or source.get("pubchem_cid") or "") or None,
            preferred_name=record.adjudicated_name or structure.get("preferred_name"),
            canonical_smiles=validation.canonical_smiles,
            isomeric_smiles=validation.isomeric_smiles,
            inchi=validation.inchi,
            inchikey=validation.inchikey,
            molecular_formula=validation.molecular_formula,
            exact_mass=validation.exact_mass,
            formal_charge=validation.formal_charge,
            pubchem_cid=int(source["pubchem_cid"]) if source.get("pubchem_cid") is not None else None,
            score=0.99,
            parse_valid=True,
            validation_notes=["manual_adjudication_structure_validated_with_rdkit"],
            provenance=[provenance],
            raw={
                "adjudication_id": record.adjudication_id,
                "reported_name": record.reported_name,
                "adjudicated_name": record.adjudicated_name,
                "source": source,
                "structure": structure,
                "review": record.review,
            },
        )
