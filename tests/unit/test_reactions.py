from project_blends_compute.reactions.candidate_generation import generate_candidates


def test_candidate_generator_preserves_alternative_explanations():
    before = [{"compound_id": "a", "reported_compound_name": "ethanol", "canonical_smiles": "CCO", "area_percent": 100, "identity_confidence": 1.0}]
    after = [{"compound_id": "b", "reported_compound_name": "acetaldehyde", "canonical_smiles": "CC=O", "area_percent": 100, "identity_confidence": 1.0}]
    candidates, alternatives, rejected = generate_candidates(sample_id="x", before=before, after=after)
    assert candidates
    assert any(candidate.transformation_family in {"dehydrogenation_or_oxidation", "structurally_related_transformation"} for candidate in candidates)
    assert alternatives
    assert any(item.explanation_type.value == "analytical_non_detection" for item in alternatives)
    assert rejected >= 0
