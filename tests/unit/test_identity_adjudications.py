import json
from pathlib import Path

import pytest

from project_blends_compute.identity.service import IdentityService
from project_blends_compute.schemas.identity import IdentityAdjudicationStatus, IdentityResolveItem, IdentityResolveRequest
from project_blends_compute.settings import Settings


REPORTED = "Imidazolo[1,2-a]pyridine, 6-chloro -2-(4-nitrophenyl)-"


@pytest.mark.asyncio
async def test_manual_pubchem_adjudication_resolves_last_project_identity(tmp_path: Path):
    reference = tmp_path / "reference"
    reference.mkdir(parents=True)
    source = Path(__file__).parents[2] / "data" / "reference" / "identity_adjudications.json"
    (reference / "identity_adjudications.json").write_text(source.read_text(encoding="utf-8"), encoding="utf-8")

    settings = Settings(project_root=tmp_path, state_root=tmp_path / "state", artifact_root=tmp_path / "artifacts")
    settings.ensure_runtime_dirs()
    service = IdentityService(settings, reference_root=reference)
    response = await service.resolve(
        IdentityResolveRequest(
            items=[IdentityResolveItem(reported_name=REPORTED, reported_smiles="CLC1CCC(-C2NC3CCCCC3N2)CC1N+[O-]")],
            online=False,
            sources=[],
            force_refresh=True,
        )
    )

    assert response.ok is True
    assert response.unresolved_count == 0
    assert response.unresolved_pending_count == 0
    assert response.excluded_unresolved_count == 0
    assert response.manual_corrected_count == 1
    compound = response.compounds[0]
    assert compound.reported_name == REPORTED
    assert compound.adjudication_status == IdentityAdjudicationStatus.MANUAL_CORRECTED
    assert compound.adjudicated_name == "6-Nitro-2-(4-nitrophenyl)imidazo[1,2-a]pyridine"
    assert compound.pubchem_cid == 14042757
    assert compound.inchikey == "FZIBIOFNSKXAJW-UHFFFAOYSA-N"
    assert compound.molecular_formula == "C13H8N4O4"
    assert compound.resolved_identity is True
    assert compound.downstream_structure_eligible is True
    assert compound.structure_source == "manual_pubchem_adjudication"
    assert compound.legacy_reported_smiles == ["CLC1CCC(-C2NC3CCCCC3N2)CC1N+[O-]"]


@pytest.mark.asyncio
async def test_excluded_unresolved_is_adjudicated_without_becoming_structure_evidence(tmp_path: Path):
    reference = tmp_path / "reference"
    reference.mkdir(parents=True)
    payload = {
        "schema_version": "project_blends.identity_adjudications.v1",
        "records": [
            {
                "adjudication_id": "exclude-001",
                "reported_name": "Unresolvable test peak",
                "status": "excluded_unresolved",
                "adjudicated_name": None,
                "reason": "No defensible structure after manual review.",
                "source": {"authority": "manual_review"},
                "structure": {},
                "review": {"review_status": "approved"},
            }
        ],
    }
    (reference / "identity_adjudications.json").write_text(json.dumps(payload), encoding="utf-8")

    settings = Settings(project_root=tmp_path, state_root=tmp_path / "state", artifact_root=tmp_path / "artifacts")
    settings.ensure_runtime_dirs()
    service = IdentityService(settings, reference_root=reference)
    response = await service.resolve(
        IdentityResolveRequest(
            items=[IdentityResolveItem(reported_name="Unresolvable test peak")],
            online=False,
            sources=[],
            force_refresh=True,
            strict=True,
        )
    )

    assert response.ok is True
    assert response.unresolved_count == 1
    assert response.unresolved_pending_count == 0
    assert response.excluded_unresolved_count == 1
    compound = response.compounds[0]
    assert compound.adjudication_status == IdentityAdjudicationStatus.EXCLUDED_UNRESOLVED
    assert compound.resolved_identity is False
    assert compound.downstream_structure_eligible is False
    assert compound.canonical_smiles is None

@pytest.mark.asyncio
async def test_frozen_batch_a_registry_is_used_without_external_requery(tmp_path: Path):
    reference = tmp_path / "reference"
    reference.mkdir(parents=True)
    source_root = Path(__file__).parents[2] / "data" / "reference"
    (reference / "project_blends_compound_registry_v1.json").write_text(
        (source_root / "project_blends_compound_registry_v1.json").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (reference / "identity_adjudications.json").write_text(
        (source_root / "identity_adjudications.json").read_text(encoding="utf-8"),
        encoding="utf-8",
    )

    settings = Settings(project_root=tmp_path, state_root=tmp_path / "state", artifact_root=tmp_path / "artifacts")
    settings.ensure_runtime_dirs()
    service = IdentityService(settings, reference_root=reference)
    response = await service.resolve(
        IdentityResolveRequest(
            items=[IdentityResolveItem(reported_name="Eugenol")],
            online=True,
            sources=["pubchem"],
            force_refresh=False,
        )
    )
    assert response.compounds[0].pubchem_cid == 3314
    assert response.frozen_registry_used_count == 1
    assert response.frozen_registry_sha256
