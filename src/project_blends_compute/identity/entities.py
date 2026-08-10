from __future__ import annotations

from collections import defaultdict
from typing import Any

from project_blends_compute.utils import dedupe_keep_order, normalize_name


_CANONICAL_FIELDS = (
    "inchikey",
    "inchi",
    "canonical_smiles",
    "isomeric_smiles",
    "molecular_formula",
    "exact_mass",
    "formal_charge",
    "pubchem_cid",
    "chebi_id",
    "cas_number",
    "preferred_name",
    "structure_source",
    "isomer_group_id",
    "tautomer_parent_id",
    "stereochemistry_status",
)


def _entity_key(compound: dict[str, Any]) -> str:
    compound_id = compound.get("compound_id")
    if compound_id:
        return str(compound_id)
    inchikey = compound.get("inchikey")
    if inchikey:
        return f"inchikey:{inchikey}"
    return f"reported:{normalize_name(str(compound.get('reported_name') or 'unknown'))}"


def _choose_representative(group: list[dict[str, Any]]) -> dict[str, Any]:
    priority = {"manual_corrected": 3, "resolved": 2, "excluded_unresolved": 1}
    return sorted(
        group,
        key=lambda row: (
            -priority.get(str(row.get("adjudication_status")), 0),
            -float(row.get("resolution_confidence") or 0.0),
            str(row.get("preferred_name") or row.get("reported_name") or "").casefold(),
        ),
    )[0]


def _assert_structure_consistency(key: str, group: list[dict[str, Any]]) -> None:
    """Fail closed if one canonical entity key maps to incompatible structures."""
    for field in ("inchikey", "canonical_smiles", "molecular_formula"):
        values = {str(row[field]) for row in group if row.get(field) not in (None, "")}
        if len(values) > 1:
            raise ValueError(f"Canonical entity collision for {key}: conflicting {field} values: {sorted(values)}")


def build_canonical_entities(compounds: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Collapse source-reported identity labels into one row per canonical chemical entity.

    The identity resolver intentionally operates on reported GC-MS labels because those
    labels are the analytical observations that must remain auditable. Downstream
    molecular inference, however, must run once per resolved molecular entity. This
    function preserves both layers explicitly instead of letting spelling aliases create
    duplicate DESS/taxonomy/RDKit computations.
    """

    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for compound in compounds:
        grouped[_entity_key(compound)].append(compound)

    entities: list[dict[str, Any]] = []
    for key, group in sorted(grouped.items()):
        _assert_structure_consistency(key, group)
        representative = _choose_representative(group)
        reported_names = dedupe_keep_order(
            [str(row.get("reported_name")) for row in group if row.get("reported_name")]
        )
        normalized_names = dedupe_keep_order([normalize_name(name) for name in reported_names])
        adjudication_statuses = dedupe_keep_order(
            [str(row.get("adjudication_status")) for row in group if row.get("adjudication_status")]
        )
        structure_eligible = any(
            bool(row.get("downstream_structure_eligible", row.get("resolved_identity"))) for row in group
        )
        resolved_identity = any(bool(row.get("resolved_identity")) for row in group)
        entity: dict[str, Any] = {
            "compound_id": representative.get("compound_id") or key,
            "reported_names": reported_names,
            "normalized_reported_names": normalized_names,
            "reported_label_count": len(reported_names),
            "preferred_name": representative.get("preferred_name") or (reported_names[0] if reported_names else None),
            "resolved_identity": resolved_identity,
            "downstream_structure_eligible": structure_eligible,
            "resolution_confidence": max(float(row.get("resolution_confidence") or 0.0) for row in group),
            "adjudication_statuses": adjudication_statuses,
            "manual_corrected": any(row.get("adjudication_status") == "manual_corrected" for row in group),
            "source_reported_identity_count": len(group),
            "identity_basis": "canonical_entity_collapsed_from_reported_gc_ms_labels",
        }
        for field in _CANONICAL_FIELDS:
            values = [row.get(field) for row in group if row.get(field) not in (None, "")]
            if values:
                entity[field] = values[0]
        entities.append(entity)
    return entities


def reported_name_crosswalk(compounds: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Create the explicit 53-label -> canonical-entity mapping artifact."""
    return [
        {
            "reported_name": compound.get("reported_name"),
            "normalized_name": compound.get("normalized_name") or normalize_name(str(compound.get("reported_name") or "")),
            "compound_id": compound.get("compound_id"),
            "preferred_name": compound.get("preferred_name"),
            "inchikey": compound.get("inchikey") if compound.get("resolved_identity") else None,
            "resolved_identity": bool(compound.get("resolved_identity")),
            "adjudication_status": compound.get("adjudication_status"),
            "structure_source": compound.get("structure_source"),
        }
        for compound in compounds
    ]


def build_structure_qc(entities: list[dict[str, Any]]) -> dict[str, Any]:
    """Revalidate frozen canonical entities and retain RDKit warnings as QC metadata."""
    from collections import Counter

    from project_blends_compute.identity.chemistry import validate_structure

    records: list[dict[str, Any]] = []
    counts: Counter[str] = Counter()
    for entity in entities:
        validation = validate_structure(
            smiles=entity.get("isomeric_smiles") or entity.get("canonical_smiles"),
            inchi=entity.get("inchi"),
        )
        messages = list(validation.rdkit_messages or [])
        for message in messages:
            counts[str(message.get("code") or "rdkit_message")] += 1
        if messages or validation.stereochemistry_status.value in {"partial", "unspecified", "unknown"}:
            records.append(
                {
                    "compound_id": entity.get("compound_id"),
                    "preferred_name": entity.get("preferred_name"),
                    "reported_names": entity.get("reported_names", []),
                    "parse_valid": validation.parse_valid,
                    "stereochemistry_status": validation.stereochemistry_status.value,
                    "rdkit_messages": messages,
                    "notes": validation.notes or [],
                }
            )
    return {
        "schema_version": "project_blends_structure_qc.v1",
        "canonical_entities_checked": len(entities),
        "entities_with_qc_records": len(records),
        "message_counts": dict(sorted(counts.items())),
        "records": records,
        "policy": "RDKit warnings are captured as structured QC and suppressed from routine console output; warnings do not silently alter source observations",
    }
