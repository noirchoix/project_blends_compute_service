# Build validation — v0.1.6

Validation completed on 2026-08-09.

- Python compile check: passed.
- Pytest: **36 passed, 0 failed**.
- Wheel build without isolation: passed; SHA-256 `8a1c6b2391558f62fa8cda4a6846744bc191647f33c9c7993d0a3fcdf2d5641a`.
- Wheel integrity: passed; packaged canonical raw data, frozen identity registry and COCONUT taxonomy contract were verified present.
- Canonical identity freeze: **53 source-reported GC-MS labels → 49 unique canonical entities**, with 52 automatic resolutions + one explicit manual correction, no unresolved-pending identities and no excluded identities.
- Structured RDKit QC revalidated all 49 entities: 14 entities retained QC records; routine console warning spam is suppressed while `omitted_undefined_stereo` and `charges_rearranged` messages remain auditable.
- Reaction gate v2: **211 pairs screened → 0 causal candidates, 1 analytical-ambiguity redirect, 210 pre-evidence rejections**. The former low-scaffold-overlap bergamotene +O hypothesis is rejected; the methoxy-allyl-phenol/Eugenol positional-isomer pair is routed to analytical/library-assignment ambiguity.
- The actual uploaded COCONUT v1 model was executed through the upstream `CocoTaxonomyProvider`: **49/49 canonical entities predicted**, zero unknown, zero failed and zero metadata-contract mismatches.
- Strict SHA-256 verification matched all six authoritative COCONUT files (two LightGBM models, two metadata files, normalized config and class lookup) before model construction.
- Morgan fingerprints are generated through `rdFingerprintGenerator.GetMorganGenerator`; the upstream taxonomy feature builder remains the feature-contract owner.
- Probability-weighted taxonomy aggregation produced 1,744 sample/timepoint composition rows, 872 before/after shift rows and 8 sample-timepoint summaries.
- RDKit molecular screening produced **49** results. True xTB/ORCA quantum chemistry was not run.
- The standalone `coco_classifier` lane is removed. The screened uploaded artifact is the authoritative `taxonomy_coconut.v1` model, not a second classifier.
- DESS adapter behavior remains regression-tested, but the user-local DESS artifact registry was not mounted in this build environment; its 49-entity execution should be confirmed in the next Windows run.
- Strict publication release remains intentionally blocked until `storage_reaction_evidence v1` is registered and the target runtime exposes the core evidence lanes required by release policy.

See `docs/v0_1_6_validation.json` for machine-readable details. Historical validation files are retained as versioned evidence and do not represent the v0.1.6 acceptance state.
