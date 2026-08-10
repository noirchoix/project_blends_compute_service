# Build validation — v0.1.7

v0.1.7 is the narrow **Storage Evidence Consumer** release.

Validation completed on 2026-08-10:

- Python compile: passed.
- Test suite: **43 passed**.
- `git diff --check`: passed.
- Wheel build without isolation: passed.
- Wheel ZIP integrity: passed.
- Isolated target import: passed.
- `storage_reaction_evidence v1.0.1` manifest SHA-256 verification: passed.
- Curation quality-count verification: passed.
- Full evidence linkage against the frozen 49-entity registry: passed.
- 13/13 literature sources referenced; 27 non-reaction/analytical records; 2 transformation precedents; 2 reaction-specific condition records; 49 identity links.
- Missing source/compound/sample links: 0/0/0.
- Storage evidence packets generated: **29**.
- Actual v0.1.6 profile artifact acceptance confirmed the corrected caryophyllene trajectories and the lemongrass citral/eugenol observations used by the consumer.

A complete sandbox v0.1.7 run produced 53 resolved reported labels, 49 canonical entities, zero causal reaction candidates, 49 RDKit molecular-screening results, and an available storage-evidence lane. The sandbox deliberately did not configure the external FoodDB/rxn_bridge/DESS/taxonomy roots, so the final strict-release check must be repeated on the Windows target runtime where those lanes were already demonstrated in the v0.1.6 run.

See `docs/v0_1_7_validation.json` for machine-readable details.
