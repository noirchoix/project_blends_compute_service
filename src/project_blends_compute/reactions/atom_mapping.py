from __future__ import annotations

import importlib.util
from dataclasses import dataclass

from rdkit import Chem
from rdkit.Chem import rdFMCS


@dataclass(slots=True)
class MappingResult:
    status: str
    mapped_reaction_smiles: str | None
    backend: str
    confidence: float
    diagnostics: list[str]


class AtomMapper:
    def __init__(self) -> None:
        self._rxnmapper_available = importlib.util.find_spec("rxnmapper") is not None
        self._mapper = None

    def map(self, reaction_smiles: str | None) -> MappingResult:
        if not reaction_smiles or ">>" not in reaction_smiles:
            return MappingResult("invalid", None, "none", 0.0, ["reaction_smiles_missing_or_invalid"])
        if self._rxnmapper_available:
            try:
                if self._mapper is None:
                    from rxnmapper import RXNMapper  # type: ignore

                    self._mapper = RXNMapper()
                mapped = self._mapper.get_attention_guided_atom_maps([reaction_smiles])[0]
                return MappingResult(
                    "mapped",
                    mapped.get("mapped_rxn"),
                    "rxnmapper",
                    float(mapped.get("confidence", 0.0)),
                    [],
                )
            except Exception as exc:
                fallback = self._mcs_map(reaction_smiles)
                fallback.diagnostics.insert(0, f"rxnmapper_failed:{type(exc).__name__}")
                return fallback
        return self._mcs_map(reaction_smiles)

    def _mcs_map(self, reaction_smiles: str) -> MappingResult:
        left, right = reaction_smiles.split(">>", 1)
        if "." in left or "." in right:
            return MappingResult("unmapped", None, "rdkit_mcs", 0.0, ["mcs_fallback_supports_single_precursor_single_product_only"])
        reactant = Chem.MolFromSmiles(left)
        product = Chem.MolFromSmiles(right)
        if reactant is None or product is None:
            return MappingResult("unmapped", None, "rdkit_mcs", 0.0, ["unparseable_molecule"])
        result = rdFMCS.FindMCS(
            [reactant, product],
            timeout=5,
            ringMatchesRingOnly=True,
            completeRingsOnly=True,
            atomCompare=rdFMCS.AtomCompare.CompareElements,
            bondCompare=rdFMCS.BondCompare.CompareOrder,
        )
        if result.canceled or result.numAtoms == 0:
            return MappingResult("unmapped", None, "rdkit_mcs", 0.0, ["no_common_substructure"])
        query = Chem.MolFromSmarts(result.smartsString)
        r_match = reactant.GetSubstructMatch(query)
        p_match = product.GetSubstructMatch(query)
        for map_number, (r_idx, p_idx) in enumerate(zip(r_match, p_match), start=1):
            reactant.GetAtomWithIdx(r_idx).SetAtomMapNum(map_number)
            product.GetAtomWithIdx(p_idx).SetAtomMapNum(map_number)
        next_map = result.numAtoms + 1
        for mol in (reactant, product):
            for atom in mol.GetAtoms():
                if atom.GetAtomMapNum() == 0:
                    atom.SetAtomMapNum(next_map)
                    next_map += 1
        mapped = f"{Chem.MolToSmiles(reactant, canonical=True)}>>{Chem.MolToSmiles(product, canonical=True)}"
        coverage = result.numAtoms / max(reactant.GetNumHeavyAtoms(), product.GetNumHeavyAtoms(), 1)
        return MappingResult(
            "mapped_fallback",
            mapped,
            "rdkit_mcs",
            float(coverage),
            ["fallback_mapping_is_structural_alignment_not_rxnmapper_attention_mapping"],
        )
