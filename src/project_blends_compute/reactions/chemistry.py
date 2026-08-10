from __future__ import annotations

from typing import Any

from rdkit import Chem, DataStructs
from rdkit.Chem import rdFingerprintGenerator, rdFMCS

from project_blends_compute.identity.chemistry import validate_structure


_FINGERPRINT_GENERATOR = rdFingerprintGenerator.GetMorganGenerator(radius=2, fpSize=2048)


def mol_from_smiles(smiles: str | None) -> Chem.Mol | None:
    if not smiles:
        return None
    try:
        return Chem.MolFromSmiles(smiles)
    except Exception:
        return None


def tanimoto(smiles_a: str | None, smiles_b: str | None) -> float | None:
    mol_a = mol_from_smiles(smiles_a)
    mol_b = mol_from_smiles(smiles_b)
    if mol_a is None or mol_b is None:
        return None
    fp_a = _FINGERPRINT_GENERATOR.GetFingerprint(mol_a)
    fp_b = _FINGERPRINT_GENERATOR.GetFingerprint(mol_b)
    return float(DataStructs.TanimotoSimilarity(fp_a, fp_b))


def mcs_coverage(smiles_a: str | None, smiles_b: str | None, timeout_s: int = 2) -> float | None:
    mol_a = mol_from_smiles(smiles_a)
    mol_b = mol_from_smiles(smiles_b)
    if mol_a is None or mol_b is None:
        return None
    try:
        result = rdFMCS.FindMCS(
            [mol_a, mol_b],
            timeout=timeout_s,
            ringMatchesRingOnly=True,
            completeRingsOnly=True,
            atomCompare=rdFMCS.AtomCompare.CompareElements,
            bondCompare=rdFMCS.BondCompare.CompareOrder,
        )
    except Exception:
        return None
    if result.canceled or result.numAtoms == 0:
        return 0.0
    denom = max(mol_a.GetNumHeavyAtoms(), mol_b.GetNumHeavyAtoms(), 1)
    return float(result.numAtoms / denom)


def canonical_smiles(smiles: str | None) -> str | None:
    return validate_structure(smiles=smiles).canonical_smiles if smiles else None


def product_similarity(target_smiles: str | None, product_smiles_list: list[str]) -> float | None:
    scores = [score for product in product_smiles_list if (score := tanimoto(target_smiles, product)) is not None]
    return max(scores) if scores else None


def match_score(smiles_a: str | None, smiles_b: str | None, name_equal: bool = False) -> float:
    if smiles_a and smiles_b:
        if canonical_smiles(smiles_a) == canonical_smiles(smiles_b):
            return 1.0
        similarity = tanimoto(smiles_a, smiles_b)
        if similarity is not None:
            return similarity
    return 0.75 if name_equal else 0.0


def molecule_properties(smiles: str | None) -> dict[str, Any]:
    validation = validate_structure(smiles=smiles)
    return {
        "canonical_smiles": validation.canonical_smiles,
        "formula": validation.molecular_formula,
        "exact_mass": validation.exact_mass,
        "inchikey": validation.inchikey,
        "parse_valid": validation.parse_valid,
    }
