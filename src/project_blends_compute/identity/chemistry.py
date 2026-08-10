from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from rdkit import Chem
from rdkit.Chem import Descriptors, rdMolDescriptors
from rdkit.Chem.MolStandardize import rdMolStandardize

from project_blends_compute.rdkit_qc import capture_rdkit_messages
from project_blends_compute.schemas.identity import StereochemistryStatus


@dataclass(slots=True)
class StructureValidation:
    parse_valid: bool
    canonical_smiles: str | None = None
    isomeric_smiles: str | None = None
    inchi: str | None = None
    inchikey: str | None = None
    molecular_formula: str | None = None
    exact_mass: float | None = None
    formal_charge: int | None = None
    heavy_atom_count: int | None = None
    stereochemistry_status: StereochemistryStatus = StereochemistryStatus.UNKNOWN
    tautomer_parent_smiles: str | None = None
    notes: list[str] | None = None
    rdkit_messages: list[dict[str, str]] | None = None


def validate_structure(smiles: str | None = None, inchi: str | None = None) -> StructureValidation:
    notes: list[str] = []
    with capture_rdkit_messages() as rdkit_messages:
        mol = None
        if smiles and str(smiles).strip():
            try:
                mol = Chem.MolFromSmiles(str(smiles).strip())
            except Exception as exc:
                notes.append(f"smiles_parse_error:{type(exc).__name__}")
        if mol is None and inchi and str(inchi).startswith("InChI="):
            try:
                mol = Chem.MolFromInchi(str(inchi).strip())
            except Exception as exc:
                notes.append(f"inchi_parse_error:{type(exc).__name__}")
        if mol is None:
            notes.extend(f"rdkit_{row['level']}:{row['code']}" for row in rdkit_messages)
            return StructureValidation(
                parse_valid=False,
                notes=notes or ["unparseable_structure"],
                rdkit_messages=list(rdkit_messages),
            )

        try:
            Chem.SanitizeMol(mol)
        except Exception as exc:
            notes.append(f"sanitize_warning:{type(exc).__name__}")

        canonical = Chem.MolToSmiles(mol, canonical=True, isomericSmiles=False)
        isomeric = Chem.MolToSmiles(mol, canonical=True, isomericSmiles=True)
        try:
            resolved_inchi = Chem.MolToInchi(mol)
            inchikey = Chem.InchiToInchiKey(resolved_inchi)
        except Exception as exc:
            resolved_inchi = None
            inchikey = None
            notes.append(f"inchi_generation_warning:{type(exc).__name__}")
        formula = rdMolDescriptors.CalcMolFormula(mol)
        exact_mass = float(Descriptors.ExactMolWt(mol))
        formal_charge = int(Chem.GetFormalCharge(mol))
        heavy_atom_count = int(mol.GetNumHeavyAtoms())

        stereo = _stereochemistry_status(mol, canonical, isomeric)
        tautomer_parent = None
        try:
            enumerator = rdMolStandardize.TautomerEnumerator()
            parent = enumerator.Canonicalize(mol)
            tautomer_parent = Chem.MolToSmiles(parent, canonical=True, isomericSmiles=True)
        except Exception as exc:
            notes.append(f"tautomer_warning:{type(exc).__name__}")

        notes.extend(f"rdkit_{row['level']}:{row['code']}" for row in rdkit_messages)

    return StructureValidation(
        parse_valid=True,
        canonical_smiles=canonical,
        isomeric_smiles=isomeric,
        inchi=resolved_inchi,
        inchikey=inchikey,
        molecular_formula=formula,
        exact_mass=exact_mass,
        formal_charge=formal_charge,
        heavy_atom_count=heavy_atom_count,
        stereochemistry_status=stereo,
        tautomer_parent_smiles=tautomer_parent,
        notes=list(dict.fromkeys(notes)),
        rdkit_messages=list(rdkit_messages),
    )


def _stereochemistry_status(mol: Chem.Mol, canonical: str, isomeric: str) -> StereochemistryStatus:
    centers = Chem.FindMolChiralCenters(mol, includeUnassigned=True)
    double_bond_stereo_possible = any(
        bond.GetBondType() == Chem.BondType.DOUBLE
        and bond.GetBeginAtom().GetDegree() > 1
        and bond.GetEndAtom().GetDegree() > 1
        for bond in mol.GetBonds()
    )
    if not centers and not double_bond_stereo_possible:
        return StereochemistryStatus.NOT_APPLICABLE
    assigned = [label for _, label in centers if label != "?"]
    unassigned = [label for _, label in centers if label == "?"]
    has_markers = canonical != isomeric or any(token in isomeric for token in ("@", "/", "\\"))
    if unassigned and assigned:
        return StereochemistryStatus.PARTIAL
    if unassigned and not has_markers:
        return StereochemistryStatus.UNSPECIFIED
    if has_markers:
        return StereochemistryStatus.SPECIFIED
    return StereochemistryStatus.UNKNOWN


def formula_atom_counts(formula: str | None) -> dict[str, int]:
    if not formula:
        return {}
    counts: dict[str, int] = {}
    for element, raw_count in re.findall(r"([A-Z][a-z]?)(\d*)", formula):
        counts[element] = counts.get(element, 0) + int(raw_count or 1)
    return counts


def formula_delta(before: str | None, after: str | None) -> dict[str, int]:
    left = formula_atom_counts(before)
    right = formula_atom_counts(after)
    elements = sorted(set(left) | set(right))
    return {element: right.get(element, 0) - left.get(element, 0) for element in elements if right.get(element, 0) != left.get(element, 0)}


def connectivity_block(inchikey: str | None) -> str | None:
    if not inchikey:
        return None
    token = str(inchikey).strip().upper()
    return token.split("-")[0] if "-" in token else token[:14]


def is_probable_inchikey(value: str | None) -> bool:
    if not value:
        return False
    token = value.strip().upper()
    return len(token) == 27 and token[14] == "-" and token[25] == "-"


def structure_to_dict(validation: StructureValidation) -> dict[str, Any]:
    return {
        "parse_valid": validation.parse_valid,
        "canonical_smiles": validation.canonical_smiles,
        "isomeric_smiles": validation.isomeric_smiles,
        "inchi": validation.inchi,
        "inchikey": validation.inchikey,
        "molecular_formula": validation.molecular_formula,
        "exact_mass": validation.exact_mass,
        "formal_charge": validation.formal_charge,
        "stereochemistry_status": validation.stereochemistry_status.value,
        "tautomer_parent_smiles": validation.tautomer_parent_smiles,
        "notes": validation.notes or [],
        "rdkit_messages": validation.rdkit_messages or [],
    }
