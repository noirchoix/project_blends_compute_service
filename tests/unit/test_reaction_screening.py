from project_blends_compute.reactions.candidate_generation import generate_candidates_with_screening


def _row(compound_id: str, name: str, smiles: str, area: float = 50.0):
    return {
        "compound_id": compound_id,
        "reported_compound_name": name,
        "canonical_smiles": smiles,
        "area_percent": area,
        "identity_confidence": 1.0,
        "downstream_structure_eligible": True,
    }


def test_pre_reaction_gate_retains_small_molecule_dehydrogenation_and_audits_pair():
    candidates, alternatives, screening, rejected = generate_candidates_with_screening(
        sample_id="x",
        before=[_row("a", "ethanol", "CCO")],
        after=[_row("b", "acetaldehyde", "CC=O")],
    )
    assert len(screening) == 1
    assert screening[0].transformation_family == "dehydrogenation_or_oxidation"
    assert screening[0].decision == "candidate"
    assert len(candidates) == 1
    assert rejected == 0
    assert alternatives


def test_pre_reaction_gate_rejects_citral_to_eugenol_before_evidence_retrieval():
    candidates, _alternatives, screening, rejected = generate_candidates_with_screening(
        sample_id="blend",
        before=[_row("citral", "Citral", "CC(C)=CCCC(C)=CC=O")],
        after=[_row("eugenol", "Eugenol", "COc1cc(CC=C)ccc1O")],
    )
    assert candidates == []
    assert len(screening) == 1
    assert screening[0].decision == "rejected_pre_evidence"
    assert "formula_delta_not_storage_interpretable" in screening[0].rejection_reasons
    assert rejected == 1


def test_screening_count_preserves_full_disappeared_by_appeared_audit_without_promoting_pairs():
    before = [
        _row("a", "Citral", "CC(C)=CCCC(C)=CC=O"),
        _row("b", "Thymol", "Cc1ccc(C(C)C)c(O)c1"),
    ]
    after = [
        _row("c", "Eugenol", "COc1cc(CC=C)ccc1O"),
        _row("d", "Caryophyllene", "CC1=CCC(C2CC=C(C)CC12)C(C)C"),
    ]
    candidates, _alternatives, screening, rejected = generate_candidates_with_screening(
        sample_id="blend",
        before=before,
        after=after,
    )
    assert len(screening) == 4
    assert rejected == len([row for row in screening if row.decision != "candidate"])
    assert len(candidates) < len(screening)


def test_same_formula_positional_isomer_is_redirected_to_analytical_ambiguity():
    candidates, _alternatives, screening, rejected = generate_candidates_with_screening(
        sample_id="blend_c",
        before=[_row("a", "allyl guaiacol positional isomer", "COc1cccc(CC=C)c1O")],
        after=[_row("b", "Eugenol", "COc1cc(CC=C)ccc1O")],
    )
    assert candidates == []
    assert screening[0].same_formula is True
    assert screening[0].transformation_family == "positional_isomer_or_library_assignment_ambiguity"
    assert screening[0].decision == "redirected_analytical_ambiguity"
    assert "redirect_to_analytical_identity_ambiguity" in screening[0].rejection_reasons
    assert rejected == 1


def test_large_plus_oxygen_pair_requires_high_scaffold_overlap():
    candidates, _alternatives, screening, rejected = generate_candidates_with_screening(
        sample_id="blend_b",
        before=[_row("a", "sesquiterpene", "CC1=CCC2C(C1)CCC2(C)C")],
        after=[_row("b", "weakly-related oxygenate", "CC1CC2CCC(C1)C2CO")],
    )
    assert candidates == []
    assert screening[0].transformation_family in {"oxidation_or_oxygenation", "unclassified_skeletal_change"}
    assert screening[0].decision == "rejected_pre_evidence"
    assert rejected == 1
