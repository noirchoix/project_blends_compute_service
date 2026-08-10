from pathlib import Path

import pytest

from project_blends_compute.identity.service import IdentityService
from project_blends_compute.schemas.identity import IdentityResolveItem, IdentityResolveRequest
from project_blends_compute.settings import Settings


@pytest.mark.asyncio
async def test_unresolved_identity_uses_name_key_not_tentative_structure(tmp_path: Path):
    settings = Settings(project_root=tmp_path, state_root=tmp_path / "state", artifact_root=tmp_path / "artifacts")
    settings.ensure_runtime_dirs()
    service = IdentityService(settings, reference_root=tmp_path / "reference")
    request = IdentityResolveRequest(
        items=[
            IdentityResolveItem(reported_name="Caryophyllene", reported_smiles="CC"),
            IdentityResolveItem(reported_name="Humulene", reported_smiles="CC"),
        ],
        online=False,
        sources=[],
    )
    response = await service.resolve(request)
    assert response.unresolved_count == 2
    assert response.compounds[0].compound_id != response.compounds[1].compound_id

@pytest.mark.asyncio
async def test_legacy_reported_smiles_is_provenance_only(tmp_path: Path):
    reference = tmp_path / "reference"
    reference.mkdir(parents=True)
    (reference / "pubchem_cache.json").write_text(
        '''{
          "eugenol": [{
            "source_id": "3314",
            "pubchem_cid": 3314,
            "preferred_name": "Eugenol",
            "canonical_smiles": "COC1=C(C=CC(=C1)CC=C)O",
            "isomeric_smiles": "COC1=C(C=CC(=C1)CC=C)O"
          }]
        }''',
        encoding="utf-8",
    )
    for name in ["chebi_cache.json", "nist_cache.json", "fooddb_identity_cache.json"]:
        (reference / name).write_text("{}", encoding="utf-8")

    settings = Settings(project_root=tmp_path, state_root=tmp_path / "state", artifact_root=tmp_path / "artifacts")
    settings.ensure_runtime_dirs()
    service = IdentityService(settings, reference_root=reference)
    response = await service.resolve(
        IdentityResolveRequest(
            items=[IdentityResolveItem(reported_name="Eugenol", reported_smiles="CC")],
            online=False,
            sources=["pubchem"],
            force_refresh=True,
        )
    )
    compound = response.compounds[0]
    assert compound.resolved_identity is True
    assert compound.structure_source == "pubchem"
    assert compound.canonical_smiles != "CC"
    assert compound.legacy_reported_smiles == ["CC"]
    assert "reported_smiles_disagrees_with_resolved_identity" not in compound.conflict_flags
    assert compound.identity_basis == "reported_gc_ms_name"

@pytest.mark.asyncio
async def test_peak_alternative_names_do_not_contaminate_compound_entity_resolution(tmp_path: Path):
    reference = tmp_path / "reference"
    reference.mkdir(parents=True)
    (reference / "pubchem_cache.json").write_text(
        '''{
          "thymol": [{
            "source_id": "6989",
            "pubchem_cid": 6989,
            "preferred_name": "Thymol",
            "canonical_smiles": "Cc1ccc(C(C)C)c(O)c1"
          }],
          "phenol,2-methyl-5-(1-methylethyl)": [{
            "source_id": "10364",
            "pubchem_cid": 10364,
            "preferred_name": "Carvacrol",
            "canonical_smiles": "Cc1ccc(C(C)C)cc1O"
          }]
        }''',
        encoding="utf-8",
    )
    for name in ["chebi_cache.json", "nist_cache.json", "fooddb_identity_cache.json"]:
        (reference / name).write_text("{}", encoding="utf-8")

    settings = Settings(project_root=tmp_path, state_root=tmp_path / "state", artifact_root=tmp_path / "artifacts")
    settings.ensure_runtime_dirs()
    service = IdentityService(settings, reference_root=reference)
    response = await service.resolve(
        IdentityResolveRequest(
            items=[
                IdentityResolveItem(
                    reported_name="Thymol",
                    candidate_names=["Thymol", "Phenol, 2-methyl-5-(1-methylethyl)"],
                )
            ],
            online=False,
            sources=["pubchem"],
            force_refresh=True,
        )
    )
    compound = response.compounds[0]
    assert compound.resolved_identity is True
    assert compound.preferred_name == "Thymol"
    assert compound.pubchem_cid == 6989
    assert not any(c.preferred_name == "Carvacrol" for c in compound.candidate_identity_set)


@pytest.mark.asyncio
async def test_same_source_stereoisomer_candidates_are_not_cross_source_conflict(tmp_path: Path):
    reference = tmp_path / "reference"
    reference.mkdir(parents=True)
    (reference / "pubchem_cache.json").write_text(
        '''{
          "fenchol,exo-": [
            {
              "source_id": "71300291",
              "pubchem_cid": 71300291,
              "preferred_name": "Fenchol, exo-",
              "isomeric_smiles": "CC12CC[C@@H](C1)C(C)(C)[C@@H]2O"
            },
            {
              "source_id": "15406",
              "pubchem_cid": 15406,
              "preferred_name": "Fenchol",
              "canonical_smiles": "CC12CCC(C1)C(C)(C)C2O"
            }
          ]
        }''',
        encoding="utf-8",
    )
    for name in ["chebi_cache.json", "nist_cache.json", "fooddb_identity_cache.json"]:
        (reference / name).write_text("{}", encoding="utf-8")
    settings = Settings(project_root=tmp_path, state_root=tmp_path / "state", artifact_root=tmp_path / "artifacts")
    settings.ensure_runtime_dirs()
    service = IdentityService(settings, reference_root=reference)
    response = await service.resolve(
        IdentityResolveRequest(
            items=[IdentityResolveItem(reported_name="Fenchol, exo-")],
            online=False,
            sources=["pubchem"],
            force_refresh=True,
        )
    )
    compound = response.compounds[0]
    assert compound.resolved_identity is True
    assert compound.preferred_name == "Fenchol, exo-"
    assert "cross_source_structure_conflict" not in compound.conflict_flags


def test_reported_smiles_cannot_be_selected_as_identity_evidence(tmp_path: Path):
    settings = Settings(project_root=tmp_path, state_root=tmp_path / "state", artifact_root=tmp_path / "artifacts")
    settings.ensure_runtime_dirs()
    service = IdentityService(settings)
    import pytest
    with pytest.raises(ValueError, match="provenance-only"):
        service._sources(["reported"], online=False)


def test_same_source_candidate_multiplicity_not_counted_as_blocking_conflict(tmp_path: Path):
    settings = Settings(project_root=tmp_path, state_root=tmp_path / "state", artifact_root=tmp_path / "artifacts")
    settings.ensure_runtime_dirs()
    service = IdentityService(settings)
    item = IdentityResolveItem(reported_name="Fenchol, exo-")
    from project_blends_compute.schemas.identity import IdentityCandidate
    a = IdentityCandidate(source="pubchem", source_id="1", preferred_name="Fenchol, exo-", canonical_smiles="CC12CCC(C1)C(C)(C)C2O", isomeric_smiles="CC12CCC(C1)C(C)(C)C2O", inchikey="IAIHUHQCLTYTSF-UHFFFAOYSA-N", molecular_formula="C10H18O", score=0.95, parse_valid=True)
    b = IdentityCandidate(source="pubchem", source_id="2", preferred_name="beta-Fenchyl alcohol", canonical_smiles="CC12CCC(C1)C(C)(C)C2O", isomeric_smiles="C[C@@]12CC[C@@H](C1)C(C)(C)[C@@H]2O", inchikey="IAIHUHQCLTYTSF-QXFUBDJGSA-N", molecular_formula="C10H18O", score=0.94, parse_valid=True)
    resolved = service._adjudicate(item, [a, b], strict=False)
    assert resolved.resolved_identity is True
    assert "cross_source_structure_conflict" not in resolved.conflict_flags
