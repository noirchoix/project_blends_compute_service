from __future__ import annotations

import json
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx

from project_blends_compute.identity.chemistry import validate_structure
from project_blends_compute.identity.names import identity_lookup_variants
from project_blends_compute.schemas.common import ProvenanceRecord
from project_blends_compute.schemas.identity import IdentityCandidate, IdentityResolveItem
from project_blends_compute.utils import normalize_name, utc_now_iso


class IdentitySource(ABC):
    name: str

    @abstractmethod
    async def resolve(self, item: IdentityResolveItem) -> list[IdentityCandidate]:
        raise NotImplementedError


class ReportedStructureSource(IdentitySource):
    name = "reported"

    async def resolve(self, item: IdentityResolveItem) -> list[IdentityCandidate]:
        if not item.reported_smiles:
            return []
        validation = validate_structure(smiles=item.reported_smiles)
        return [
            IdentityCandidate(
                source=self.name,
                preferred_name=item.reported_name,
                canonical_smiles=validation.canonical_smiles,
                isomeric_smiles=validation.isomeric_smiles,
                inchi=validation.inchi,
                inchikey=validation.inchikey,
                molecular_formula=validation.molecular_formula,
                exact_mass=validation.exact_mass,
                formal_charge=validation.formal_charge,
                score=0.30 if validation.parse_valid else 0.0,
                parse_valid=validation.parse_valid,
                validation_notes=validation.notes or [],
                provenance=[
                    ProvenanceRecord(
                        source="project_blends_report",
                        source_id=item.source_row_id,
                        evidence_type="reported_structure",
                        source_quality="primary_experiment_table",
                    )
                ],
                raw={"reported_smiles": item.reported_smiles},
            )
        ]


class JsonCacheSource(IdentitySource):
    def __init__(self, name: str, path: Path | None, *, base_score: float, alias_map: dict[str, list[str]] | None = None) -> None:
        self.name = name
        self.path = path
        self.base_score = base_score
        self.alias_map = alias_map or {}
        self._data: dict[str, list[dict[str, Any]]] | None = None

    def _load(self) -> dict[str, list[dict[str, Any]]]:
        if self._data is not None:
            return self._data
        if self.path is None or not self.path.exists():
            self._data = {}
            return self._data
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        if isinstance(payload, list):
            grouped: dict[str, list[dict[str, Any]]] = {}
            for row in payload:
                if isinstance(row, dict):
                    key = normalize_name(row.get("query") or row.get("reported_name") or row.get("preferred_name") or "")
                    grouped.setdefault(key, []).append(row)
            self._data = grouped
        elif isinstance(payload, dict):
            self._data = {normalize_name(key): value if isinstance(value, list) else [value] for key, value in payload.items()}
        else:
            self._data = {}
        return self._data

    async def resolve(self, item: IdentityResolveItem) -> list[IdentityCandidate]:
        # ``candidate_names`` are alternative GC-MS peak assignments, not synonyms
        # of the compound entity. Query only the anchored reported name plus bounded
        # transcription/formatting variants.
        rows: list[dict[str, Any]] = []
        cache = self._load()
        for query in identity_lookup_variants(item.reported_name, alias_map=self.alias_map):
            rows.extend(cache.get(normalize_name(query), []))
        return [candidate_from_mapping(self.name, row, self.base_score) for row in rows]


class PubChemSource(IdentitySource):
    name = "pubchem"

    def __init__(self, base_url: str, timeout_s: float = 20.0, max_candidates: int = 8, alias_map: dict[str, list[str]] | None = None) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_s = timeout_s
        self.max_candidates = max_candidates
        self.alias_map = alias_map or {}

    async def resolve(self, item: IdentityResolveItem) -> list[IdentityCandidate]:
        queries = identity_lookup_variants(item.reported_name, alias_map=self.alias_map)
        candidates: list[IdentityCandidate] = []
        async with httpx.AsyncClient(timeout=self.timeout_s, follow_redirects=True) as client:
            for query in queries:
                if len(candidates) >= self.max_candidates:
                    break
                try:
                    rows = await self._query_name(client, query)
                except Exception:
                    continue
                for row in rows:
                    candidates.append(candidate_from_mapping(self.name, row, 0.95))
                    if len(candidates) >= self.max_candidates:
                        break
        return candidates

    async def _query_name(self, client: httpx.AsyncClient, query: str) -> list[dict[str, Any]]:
        # Current PUG REST names the stereochemical property `SMILES` and the
        # connectivity-only property `ConnectivitySMILES`. Historical
        # CanonicalSMILES/IsomericSMILES tags are intentionally not requested.
        properties = "Title,IUPACName,ConnectivitySMILES,SMILES,InChI,InChIKey,MolecularFormula,ExactMass,Charge"
        url = f"{self.base_url}/compound/name/{quote(query, safe='')}/property/{properties}/JSON"
        response = await client.get(url)
        response.raise_for_status()
        props = response.json().get("PropertyTable", {}).get("Properties", [])
        rows: list[dict[str, Any]] = []
        for prop in props:
            rows.append(
                {
                    "source_id": str(prop.get("CID")) if prop.get("CID") is not None else None,
                    "pubchem_cid": prop.get("CID"),
                    "preferred_name": prop.get("Title") or prop.get("IUPACName") or query,
                    "canonical_smiles": prop.get("ConnectivitySMILES") or prop.get("SMILES"),
                    "isomeric_smiles": prop.get("SMILES"),
                    "inchi": prop.get("InChI"),
                    "inchikey": prop.get("InChIKey"),
                    "molecular_formula": prop.get("MolecularFormula"),
                    "exact_mass": prop.get("ExactMass"),
                    "formal_charge": prop.get("Charge"),
                    "source_uri": url,
                    "retrieved_at_utc": utc_now_iso(),
                    "query": query,
                }
            )
        return rows


class ChEBISource(IdentitySource):
    name = "chebi"

    def __init__(self, base_url: str, timeout_s: float = 20.0, max_candidates: int = 5, alias_map: dict[str, list[str]] | None = None) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_s = timeout_s
        self.max_candidates = max_candidates
        self.alias_map = alias_map or {}

    async def resolve(self, item: IdentityResolveItem) -> list[IdentityCandidate]:
        # ChEBI public deployments have changed endpoint shapes over time. Query the
        # reported name and conservative formatting aliases; fail closed on schema or
        # transport errors.
        candidates: list[IdentityCandidate] = []
        async with httpx.AsyncClient(timeout=self.timeout_s, follow_redirects=True) as client:
            for query in identity_lookup_variants(item.reported_name, alias_map=self.alias_map):
                if len(candidates) >= self.max_candidates:
                    break
                url = f"{self.base_url}/compounds/?search={quote(query, safe='')}&size={self.max_candidates}"
                try:
                    response = await client.get(url)
                    response.raise_for_status()
                    payload = response.json()
                except Exception:
                    continue
                rows = payload.get("results") or payload.get("items") or payload.get("content") or []
                if not isinstance(rows, list):
                    continue
                for row in rows:
                    if not isinstance(row, dict):
                        continue
                    mapped = {
                        "source_id": row.get("chebi_accession") or row.get("chebiId") or row.get("id"),
                        "chebi_id": row.get("chebi_accession") or row.get("chebiId") or row.get("id"),
                        "preferred_name": row.get("name") or row.get("chebiAsciiName") or query,
                        "canonical_smiles": row.get("smiles"),
                        "inchi": row.get("inchi"),
                        "inchikey": row.get("inchikey") or row.get("inchiKey"),
                        "molecular_formula": row.get("formula"),
                        "source_uri": url,
                        "retrieved_at_utc": utc_now_iso(),
                        "query": query,
                    }
                    candidates.append(candidate_from_mapping(self.name, mapped, 0.85))
                    if len(candidates) >= self.max_candidates:
                        break
        return candidates


def candidate_from_mapping(source: str, row: dict[str, Any], base_score: float) -> IdentityCandidate:
    smiles = row.get("isomeric_smiles") or row.get("canonical_smiles") or row.get("smiles")
    inchi = row.get("inchi")
    validation = validate_structure(smiles=smiles, inchi=inchi)
    canonical = validation.canonical_smiles or row.get("canonical_smiles") or row.get("smiles")
    isomeric = validation.isomeric_smiles or row.get("isomeric_smiles") or canonical
    provenance = ProvenanceRecord(
        source=source,
        source_id=str(row.get("source_id") or row.get("pubchem_cid") or row.get("chebi_id") or "") or None,
        source_uri=row.get("source_uri"),
        retrieved_at_utc=row.get("retrieved_at_utc"),
        evidence_type="identity_database_record",
        source_quality="curated_database" if source in {"pubchem", "chebi", "nist"} else "supporting_database",
        metadata={"query": row.get("query")},
    )
    notes = list(validation.notes or [])
    source_inchikey = row.get("inchikey")
    source_formula = row.get("molecular_formula")
    source_structure_mismatch = False
    if validation.parse_valid and source_inchikey and validation.inchikey and str(source_inchikey) != str(validation.inchikey):
        notes.append("source_inchikey_disagrees_with_structure")
        source_structure_mismatch = True
    if validation.parse_valid and source_formula and validation.molecular_formula and str(source_formula) != str(validation.molecular_formula):
        notes.append("source_formula_disagrees_with_structure")
        source_structure_mismatch = True
    score = base_score if validation.parse_valid and not source_structure_mismatch else min(base_score, 0.35)
    return IdentityCandidate(
        source=source,
        source_id=provenance.source_id,
        preferred_name=row.get("preferred_name") or row.get("name"),
        canonical_smiles=canonical,
        isomeric_smiles=isomeric,
        inchi=validation.inchi or inchi,
        inchikey=validation.inchikey or row.get("inchikey"),
        molecular_formula=validation.molecular_formula or row.get("molecular_formula"),
        exact_mass=validation.exact_mass or _safe_float(row.get("exact_mass")),
        formal_charge=validation.formal_charge if validation.parse_valid else _safe_int(row.get("formal_charge")),
        cas_number=row.get("cas_number"),
        pubchem_cid=_safe_int(row.get("pubchem_cid")),
        chebi_id=row.get("chebi_id"),
        score=score,
        parse_valid=validation.parse_valid,
        validation_notes=notes,
        provenance=[provenance],
        raw=row,
    )


def _safe_float(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _safe_int(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None
