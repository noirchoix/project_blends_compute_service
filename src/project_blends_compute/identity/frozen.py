from __future__ import annotations

import json
from pathlib import Path

from project_blends_compute.identity.chemistry import validate_structure
from project_blends_compute.schemas.identity import CanonicalCompound, IdentityAdjudicationStatus
from project_blends_compute.utils import canonical_json_bytes, normalize_name, stable_hash


class FrozenIdentityRegistry:
    """Read-only, structure-validated compound registry for a locked Batch A release."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.schema_version = "project_blends.frozen_compound_registry.v1"
        self.registry_version: str | None = None
        self.content_sha256 = stable_hash("{}")
        self.metadata: dict[str, object] = {}
        self._records: dict[str, CanonicalCompound] = {}
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("frozen compound registry root must be a JSON object")
        self.schema_version = str(payload.get("schema_version") or self.schema_version)
        self.registry_version = str(payload.get("registry_version") or "") or None
        records = payload.get("records") or []
        if not isinstance(records, list):
            raise ValueError("frozen compound registry records must be a list")
        self.content_sha256 = stable_hash(canonical_json_bytes(payload).decode("utf-8"))
        self.metadata = {key: value for key, value in payload.items() if key != "records"}

        for raw in records:
            compound = CanonicalCompound.model_validate(raw)
            key = normalize_name(compound.reported_name)
            if key in self._records:
                raise ValueError(f"duplicate frozen identity for reported name: {compound.reported_name}")
            self._validate_record(compound)
            self._records[key] = compound

    @staticmethod
    def _validate_record(compound: CanonicalCompound) -> None:
        if compound.adjudication_status == IdentityAdjudicationStatus.UNRESOLVED_PENDING:
            raise ValueError(f"frozen registry cannot contain unresolved-pending identity: {compound.reported_name}")
        if compound.adjudication_status == IdentityAdjudicationStatus.EXCLUDED_UNRESOLVED:
            if compound.downstream_structure_eligible or compound.resolved_identity:
                raise ValueError(f"excluded unresolved identity cannot be structure eligible: {compound.reported_name}")
            return
        if not compound.resolved_identity or not compound.downstream_structure_eligible:
            raise ValueError(f"resolved frozen identity must be downstream-eligible: {compound.reported_name}")
        validation = validate_structure(
            smiles=compound.isomeric_smiles or compound.canonical_smiles,
            inchi=compound.inchi,
        )
        if not validation.parse_valid:
            raise ValueError(f"frozen identity structure is invalid: {compound.reported_name}")
        if compound.inchikey and validation.inchikey != compound.inchikey:
            raise ValueError(f"frozen identity InChIKey mismatch: {compound.reported_name}")
        if compound.molecular_formula and validation.molecular_formula != compound.molecular_formula:
            raise ValueError(f"frozen identity formula mismatch: {compound.reported_name}")
        if compound.inchi and validation.inchi != compound.inchi:
            raise ValueError(f"frozen identity InChI mismatch: {compound.reported_name}")

    def get(self, reported_name: str) -> CanonicalCompound | None:
        compound = self._records.get(normalize_name(reported_name))
        return compound.model_copy(deep=True) if compound is not None else None

    def all(self) -> list[CanonicalCompound]:
        return [compound.model_copy(deep=True) for compound in self._records.values()]

    def __len__(self) -> int:
        return len(self._records)
