from project_blends_compute.identity.names import identity_lookup_variants


def test_lookup_variants_repair_project_blends_transcription_artifacts():
    assert "gamma-Terpinene" in identity_lookup_variants("gamma--Terpinene")
    assert "Caryophyllene" in identity_lookup_variants("Carophyllene")
    assert "gamma-Muurolene" in identity_lookup_variants("gamma--Muurolene")
    assert "Dehydroelsholtzia ketone" in identity_lookup_variants("Dehydroelasholtza ketone")


def test_lookup_variants_include_compact_systematic_form():
    name = "7-Isopropyl-1,1,4a-trimethyl-1,2,3 ,4,4a,9,10,10a-octahydrophenanthre ne"
    variants = identity_lookup_variants(name)
    assert any("octahydrophenanthrene" in value for value in variants)


def test_verified_alias_rescue_for_long_nist_style_names():
    cases = {
        "Naphthalene, decahydro-4a-methyl-1 -methylene-7-(1-methylethenyl)-, [4aR-(4aalpha-,7alpha-,8abeta-)]": "beta-Selinene",
        "Naphthalene,1,2,3,5,6,7,8,8a-octahydro-1,8a-dimethyl-7-(1-methylethenyl)-, [1R-(1α,7β,8aα)]-": "Valencene",
        "Benzene,1-(1,5-dimethyl-4-hexenyl)-4-methyl-": "alpha-Curcumene",
        "Tricyclo[2.2.1.0(2,6)]heptane, 1,3 ,3-trimethyl-": "Cyclofenchene",
        "1H-Cyclopropa[a]naphthalene, 1a,2, 3,3a,4,5,6,7b-octahydro-1,1,3a,7-t etramethyl-, [1aR-(1aalpha-,3a.al pha.,7balpha-)]-": "beta-Maaliene",
        "Naphthalene, 1,2,3,5,6,8a-hexahydr o-4,7-dimethyl-1-(1-methylethyl)-, (1S-cis)-": "delta-Cadinene",
        "1H-Cycloprop[e]azulen-7-ol, decahy dro-1,1,7-trimethyl-4-methylene-, [1ar-(1aalpha-,4aalpha-,7beta-, 7abeta-,7balpha-)]-": "Spathulenol",
        "1H-Cyclopropa[a]naphthalene, 1a,2, 6,7,7a,7b-hexahydro-1,1,7,7a-tetra methyl-, [1aR-(1aalpha-,7alpha-, 7aalpha-,7balpha-)]-": "1,2,9,10-Tetradehydroaristolane",
        "Naphthalene, 1,2,3,5,6,7,8,8a-octa hydro-1,8a-dimethyl-7-(1-methyleth enyl)-, [1S-(1alpha-,7alpha-,8a. alpha.)]-": "Eremophilene",
        "Neoisolongifolene, 8,9-dehydro-4,4-Dimethyl-3-(3-methylbut-3-enylidene)-2-methylenebicyclo[4.1.0]heptane": "Neoisolongifolene,8,9-dehydro-",
        "1,3-Cyclohexadiene, 5-(1,5-dimethy l-4-hexenyl)-2-methyl-, [S-(R*,S*) ]-": "Zingiberene",
        "(4aS,4bR,10aS)-7-Isopropyl-1,1,4a- trimethyl-1,2,3,4,4a,5,6,9,10,10a-deca hydrophenanthrene": "Abieta-7,13-diene",
    }
    for reported, expected in cases.items():
        normalized = {value.replace(" ", "") for value in identity_lookup_variants(reported)}
        assert expected.replace(" ", "") in normalized
