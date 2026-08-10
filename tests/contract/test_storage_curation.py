from pathlib import Path

from project_blends_compute.storage_curation.builder import StorageReactionCurationBuilder
from project_blends_compute.storage_curation.models import (
    EvidenceDirectness,
    EvidenceModality,
    StorageCurationBuildRequest,
    StorageEvidenceSource,
    StorageReactionRecord,
)


def test_storage_curation_writes_compatible_registry(tmp_path: Path):
    builder = StorageReactionCurationBuilder(tmp_path / "artifacts", tmp_path / "benchmark_registry.json")
    request = StorageCurationBuildRequest(
        version="v1",
        reactions=[
            StorageReactionRecord(
                reaction_id="r1",
                source_id="s1",
                source_type="primary_article",
                precursor_name="ethanol",
                product_name="acetaldehyde",
                precursor_smiles="CCO",
                product_smiles="CC=O",
                transformation_family="oxidation",
                evidence_directness=EvidenceDirectness.DIRECT,
                experimental_or_computational=EvidenceModality.EXPERIMENTAL,
                temperature_c=25,
                duration_h=24,
            )
        ],
        sources=[StorageEvidenceSource(source_id="s1", source_type="primary_article", primary_source=True)],
    )
    response = builder.build(request)
    assert response.ok
    assert Path(response.registry_path).exists()
    assert Path(response.artifacts["conditions"]).exists()
