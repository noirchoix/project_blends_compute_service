import pytest

from project_blends_compute.identity.entities import build_canonical_entities, reported_name_crosswalk


def _row(name: str, compound_id: str, inchikey: str = "AAAA-BBBB-C"):
    return {
        "reported_name": name,
        "normalized_name": name.casefold(),
        "compound_id": compound_id,
        "preferred_name": "Canonical",
        "inchikey": inchikey,
        "canonical_smiles": "CCO",
        "molecular_formula": "C2H6O",
        "resolved_identity": True,
        "downstream_structure_eligible": True,
        "resolution_confidence": 1.0,
        "adjudication_status": "resolved",
        "structure_source": "pubchem",
    }


def test_reported_labels_collapse_to_one_canonical_entity():
    compounds = [_row("Caryophyllene", "cmp-1"), _row("Carophyllene", "cmp-1")]
    entities = build_canonical_entities(compounds)
    crosswalk = reported_name_crosswalk(compounds)
    assert len(crosswalk) == 2
    assert len(entities) == 1
    assert entities[0]["reported_label_count"] == 2
    assert entities[0]["reported_names"] == ["Caryophyllene", "Carophyllene"]


def test_canonical_entity_collision_fails_closed():
    a = _row("A", "cmp-1", "AAAA-BBBB-C")
    b = _row("B", "cmp-1", "XXXX-YYYY-Z")
    with pytest.raises(ValueError, match="Canonical entity collision"):
        build_canonical_entities([a, b])
