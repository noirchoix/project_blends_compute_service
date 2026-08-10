from dataclasses import dataclass
from pathlib import Path

from project_blends_compute.settings import Settings
from project_blends_compute.supporting.service import SupportingEvidenceService


COMPOUND = {
    "compound_id": "cmp-1",
    "reported_names": ["Eugenol"],
    "preferred_name": "Eugenol",
    "canonical_smiles": "COc1cc(CC=C)ccc1O",
    "resolved_identity": True,
    "downstream_structure_eligible": True,
}


@dataclass
class FakeDessEvidence:
    p_physics: float = 0.8
    stability_support: float = 0.7
    candidate_rank_scores: dict | None = None
    uncertainty: float = 0.1
    notes: list | None = None
    meta: dict | None = None


class FakeDess:
    def __init__(self):
        self.calls = 0

    def score_reactants(self, _smiles):
        self.calls += 1
        return FakeDessEvidence(candidate_rank_scores={}, notes=[], meta={"model_version": "test"})


class FakeTaxonomy:
    def __init__(self):
        self.calls = 0

    def predict_taxonomy(self, smiles, top_k=5):
        self.calls += 1
        return {
            "smiles": smiles,
            "canonical_smiles": smiles,
            "chemical_super_class": {
                "label": "Lipids and lipid-like molecules",
                "confidence": 0.9,
                "entropy": 0.2,
                "top5": [
                    {"label": "Lipids and lipid-like molecules", "prob": 0.9},
                    {"label": "Organic oxygen compounds", "prob": 0.1},
                ],
            },
            "chemical_class": {
                "label": "Prenol lipids",
                "confidence": 0.8,
                "entropy": 0.4,
                "top5": [
                    {"label": "Prenol lipids", "prob": 0.8},
                    {"label": "Phenols", "prob": 0.2},
                ],
            },
            "model_version": "lightgbm_coconut_hierarchy_v1",
            "artifact_version": "v1",
            "schema_version": "taxonomy.coconut.v1",
        }


def test_redundant_standalone_coco_classifier_lane_is_removed(tmp_path: Path):
    artifact_dir = tmp_path / "coco"
    artifact_dir.mkdir()
    settings = Settings(
        project_root=tmp_path,
        state_root=tmp_path / "state",
        artifact_root=tmp_path / "artifacts",
        coco_model_artifact_root=artifact_dir,
    )
    service = SupportingEvidenceService(settings)
    result = service.evaluate([COMPOUND])
    assert result["lane_status"] == {"dess": "runtime_unavailable", "taxonomy": "runtime_unavailable"}
    assert "coco_classifier" not in result["lane_status"]
    assert result["removed_redundant_lane"] == "standalone_coco_classifier"
    assert result["evidence"] == []


def test_dess_and_taxonomy_execute_once_per_canonical_entity(tmp_path: Path):
    settings = Settings(project_root=tmp_path, state_root=tmp_path / "state", artifact_root=tmp_path / "artifacts")
    service = SupportingEvidenceService(settings)
    dess_provider = FakeDess()
    taxonomy_provider = FakeTaxonomy()
    dess = service.lanes["dess"]
    dess.provider = dess_provider
    dess.ready = True
    dess.error = None
    taxonomy = service.lanes["taxonomy"]
    taxonomy.provider = taxonomy_provider
    taxonomy.ready = True
    taxonomy.error = None
    taxonomy.artifact_verification = {"status": "matched", "verification_mode": "test_fixture"}

    alias = {**COMPOUND, "reported_names": ["Eugenol alias"]}
    result = service.evaluate([COMPOUND, alias])
    assert result["lane_status"]["dess"] == "executed"
    assert result["lane_status"]["taxonomy"] == "executed"
    assert len(result["evidence"]) == 2
    assert dess_provider.calls == 1
    assert taxonomy_provider.calls == 1
    assert result["execution"]["taxonomy"]["artifact_contract_status"] == "matched"
    taxonomy_row = next(row for row in result["evidence"] if row["lane"] == "taxonomy")
    assert taxonomy_row["result"]["chemical_class"]["confidence_band"] == "high"
    assert taxonomy_row["result"]["chemical_class"]["probability_mass_covered"] == 1.0
