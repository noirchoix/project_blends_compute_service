from __future__ import annotations

import math
from pathlib import Path
from typing import Any

from rdkit import Chem
from rdkit.Chem import AllChem, Crippen, Descriptors, Lipinski, rdMolDescriptors

from project_blends_compute.quantum.engines.base import EngineResult, QuantumEngine
from project_blends_compute.rdkit_qc import capture_rdkit_messages
from project_blends_compute.schemas.quantum import QuantumJobRequest
from project_blends_compute.utils import write_json_atomic


class RDKitDescriptorEngine(QuantumEngine):
    name = "rdkit"

    def available(self) -> bool:
        return True

    def run(self, request: QuantumJobRequest, work_dir: Path) -> EngineResult:
        work_dir.mkdir(parents=True, exist_ok=True)
        molecules: list[dict[str, Any]] = []
        warnings: list[str] = []
        with capture_rdkit_messages() as rdkit_messages:
            for molecule in request.molecules:
                mol = Chem.MolFromSmiles(molecule.smiles)
                if mol is None:
                    molecules.append({"name": molecule.name, "smiles": molecule.smiles, "status": "unparseable"})
                    continue
                descriptors = self._descriptors(mol)
                conformers = self._conformer_energies(mol, request.max_conformers)
                susceptibility = self._susceptibility(mol, descriptors)
                molecules.append(
                    {
                        "compound_id": molecule.compound_id,
                        "name": molecule.name,
                        "smiles": molecule.smiles,
                        "status": "complete",
                        "descriptors": descriptors,
                        "conformer_summary": conformers,
                        "storage_susceptibility_proxy": susceptibility,
                    }
                )
        result = {
            "engine": self.name,
            "task": request.task.value,
            "method": "RDKit 2D descriptors + ETKDG conformer generation + MMFF/UFF energies",
            "molecules": molecules,
            "claim_boundary": "Descriptor and force-field outputs are screening features; they are not DFT reaction barriers or experimental stability constants.",
            "warnings": warnings,
            "rdkit_qc_messages": rdkit_messages,
        }
        path = work_dir / "rdkit_results.json"
        write_json_atomic(path, result)
        return EngineResult(result=result, artifact_paths=[path], warnings=warnings)

    @staticmethod
    def _descriptors(mol: Chem.Mol) -> dict[str, Any]:
        return {
            "molecular_weight": float(Descriptors.MolWt(mol)),
            "exact_mass": float(Descriptors.ExactMolWt(mol)),
            "logp": float(Crippen.MolLogP(mol)),
            "tpsa": float(rdMolDescriptors.CalcTPSA(mol)),
            "hbd": int(Lipinski.NumHDonors(mol)),
            "hba": int(Lipinski.NumHAcceptors(mol)),
            "num_rings": int(rdMolDescriptors.CalcNumRings(mol)),
            "num_aromatic_rings": int(rdMolDescriptors.CalcNumAromaticRings(mol)),
            "fraction_csp3": float(rdMolDescriptors.CalcFractionCSP3(mol)),
            "rotatable_bonds": int(Lipinski.NumRotatableBonds(mol)),
            "formal_charge": int(Chem.GetFormalCharge(mol)),
            "heavy_atoms": int(mol.GetNumHeavyAtoms()),
            "heteroatoms": int(Lipinski.NumHeteroatoms(mol)),
        }

    @staticmethod
    def _conformer_energies(mol: Chem.Mol, max_conformers: int) -> dict[str, Any]:
        hydrogens = Chem.AddHs(mol)
        count = min(max_conformers, max(1, min(50, 3 * Lipinski.NumRotatableBonds(mol) + 5)))
        params = AllChem.ETKDGv3()
        params.randomSeed = 1337
        ids = list(AllChem.EmbedMultipleConfs(hydrogens, numConfs=count, params=params))
        energies: list[float] = []
        forcefield = "MMFF94"
        if AllChem.MMFFHasAllMoleculeParams(hydrogens):
            results = AllChem.MMFFOptimizeMoleculeConfs(hydrogens, maxIters=500)
            energies = [float(energy) for _, energy in results]
        else:
            forcefield = "UFF"
            results = AllChem.UFFOptimizeMoleculeConfs(hydrogens, maxIters=500)
            energies = [float(energy) for _, energy in results]
        finite = [energy for energy in energies if math.isfinite(energy)]
        return {
            "requested": count,
            "embedded": len(ids),
            "forcefield": forcefield,
            "minimum_energy_kcal_mol": min(finite) if finite else None,
            "energy_range_kcal_mol": (max(finite) - min(finite)) if len(finite) > 1 else 0.0 if finite else None,
        }

    @staticmethod
    def _susceptibility(mol: Chem.Mol, descriptors: dict[str, Any]) -> dict[str, Any]:
        patterns = {
            "phenolic_oh": Chem.MolFromSmarts("[c:1][OH:2]"),
            "aldehyde": Chem.MolFromSmarts("[CX3H1](=O)[#6]"),
            "allylic_carbon": Chem.MolFromSmarts("[C,c]-[C;X3]=[C;X3]"),
            "non_aromatic_double_bond": Chem.MolFromSmarts("[C;!a]=[C;!a]"),
            "epoxide": Chem.MolFromSmarts("[O;r3]1[C;r3][C;r3]1"),
            "conjugated_diene": Chem.MolFromSmarts("C=C-C=C"),
        }
        counts = {name: len(mol.GetSubstructMatches(pattern)) if pattern is not None else 0 for name, pattern in patterns.items()}
        raw = (
            0.20 * min(counts["phenolic_oh"], 2)
            + 0.22 * min(counts["aldehyde"], 1)
            + 0.12 * min(counts["allylic_carbon"], 3)
            + 0.10 * min(counts["non_aromatic_double_bond"], 4)
            + 0.08 * min(counts["conjugated_diene"], 2)
            + 0.05 * min(descriptors["rotatable_bonds"], 5)
            + 0.03 * max(0.0, descriptors["logp"])
        )
        score = max(0.0, min(1.0, raw / 1.5))
        return {
            "score": score,
            "features": counts,
            "interpretation": "higher scores prioritize compounds for targeted oxidation/volatilization study",
            "not_a_measure_of": ["experimental_shelf_life", "activation_barrier", "absolute_oxidation_rate"],
        }
