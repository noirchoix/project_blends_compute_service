from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

from rdkit import Chem
from rdkit.Chem import AllChem

from project_blends_compute.quantum.engines.base import EngineResult, QuantumEngine
from project_blends_compute.schemas.quantum import QuantumJobRequest
from project_blends_compute.utils import write_json_atomic, write_text_atomic


def _xyz_from_smiles(smiles: str, name: str) -> str:
    mol = Chem.AddHs(Chem.MolFromSmiles(smiles))
    if mol is None:
        raise ValueError(f"Unparseable SMILES for {name}")
    params = AllChem.ETKDGv3()
    params.randomSeed = 1337
    if AllChem.EmbedMolecule(mol, params) != 0:
        raise RuntimeError(f"Conformer embedding failed for {name}")
    if AllChem.MMFFHasAllMoleculeParams(mol):
        AllChem.MMFFOptimizeMolecule(mol)
    else:
        AllChem.UFFOptimizeMolecule(mol)
    conformer = mol.GetConformer()
    lines = [str(mol.GetNumAtoms()), name]
    for atom in mol.GetAtoms():
        point = conformer.GetAtomPosition(atom.GetIdx())
        lines.append(f"{atom.GetSymbol():2s} {point.x: .10f} {point.y: .10f} {point.z: .10f}")
    return "\n".join(lines) + "\n"


class XTBEngine(QuantumEngine):
    name = "xtb"

    def __init__(self, executable: Path | None) -> None:
        self.executable = executable

    def available(self) -> bool:
        return bool(self.executable and (self.executable.exists() or shutil.which(str(self.executable))))

    def run(self, request: QuantumJobRequest, work_dir: Path) -> EngineResult:
        if not self.available():
            raise RuntimeError("xTB executable is not configured or not found")
        work_dir.mkdir(parents=True, exist_ok=True)
        molecule_results: list[dict[str, Any]] = []
        artifacts: list[Path] = []
        for index, molecule in enumerate(request.molecules):
            molecule_dir = work_dir / f"molecule_{index:03d}"
            molecule_dir.mkdir(parents=True, exist_ok=True)
            xyz = molecule_dir / "input.xyz"
            write_text_atomic(xyz, _xyz_from_smiles(molecule.smiles, molecule.name))
            command = [str(self.executable), str(xyz), "--gfn", "2", "--opt", "normal", "--chrg", str(molecule.charge), "--uhf", str(max(0, molecule.multiplicity - 1))]
            completed = subprocess.run(command, cwd=molecule_dir, capture_output=True, text=True, timeout=3600, check=False)
            stdout = molecule_dir / "xtb.stdout.txt"
            stderr = molecule_dir / "xtb.stderr.txt"
            write_text_atomic(stdout, completed.stdout)
            write_text_atomic(stderr, completed.stderr)
            artifacts.extend([xyz, stdout, stderr])
            energy_match = re.findall(r"TOTAL ENERGY\s+(-?\d+\.\d+)", completed.stdout)
            molecule_results.append(
                {
                    "name": molecule.name,
                    "return_code": completed.returncode,
                    "total_energy_hartree": float(energy_match[-1]) if energy_match else None,
                    "command": command,
                }
            )
        result = {"engine": self.name, "method": request.method or "GFN2-xTB", "molecules": molecule_results}
        result_path = work_dir / "xtb_results.json"
        write_json_atomic(result_path, result)
        artifacts.append(result_path)
        return EngineResult(result=result, artifact_paths=artifacts)


class ORCAEngine(QuantumEngine):
    name = "orca"

    def __init__(self, executable: Path | None) -> None:
        self.executable = executable

    def available(self) -> bool:
        return bool(self.executable and (self.executable.exists() or shutil.which(str(self.executable))))

    def run(self, request: QuantumJobRequest, work_dir: Path) -> EngineResult:
        if not self.available():
            raise RuntimeError("ORCA executable is not configured or not found")
        work_dir.mkdir(parents=True, exist_ok=True)
        molecule_results: list[dict[str, Any]] = []
        artifacts: list[Path] = []
        method = request.method or "r2SCAN-3c"
        for index, molecule in enumerate(request.molecules):
            molecule_dir = work_dir / f"molecule_{index:03d}"
            molecule_dir.mkdir(parents=True, exist_ok=True)
            xyz_text = _xyz_from_smiles(molecule.smiles, molecule.name)
            xyz_lines = xyz_text.splitlines()[2:]
            input_text = (
                f"! {method} Opt TightSCF\n"
                f"%pal nprocs 1 end\n"
                f"* xyz {molecule.charge} {molecule.multiplicity}\n"
                + "\n".join(xyz_lines)
                + "\n*\n"
            )
            input_path = molecule_dir / "job.inp"
            output_path = molecule_dir / "job.out"
            write_text_atomic(input_path, input_text)
            completed = subprocess.run([str(self.executable), str(input_path)], cwd=molecule_dir, capture_output=True, text=True, timeout=14400, check=False)
            write_text_atomic(output_path, completed.stdout + "\n" + completed.stderr)
            artifacts.extend([input_path, output_path])
            match = re.findall(r"FINAL SINGLE POINT ENERGY\s+(-?\d+\.\d+)", completed.stdout)
            molecule_results.append(
                {
                    "name": molecule.name,
                    "return_code": completed.returncode,
                    "final_energy_hartree": float(match[-1]) if match else None,
                    "method": method,
                }
            )
        result = {"engine": self.name, "method": method, "molecules": molecule_results}
        result_path = work_dir / "orca_results.json"
        write_json_atomic(result_path, result)
        artifacts.append(result_path)
        return EngineResult(result=result, artifact_paths=artifacts)
