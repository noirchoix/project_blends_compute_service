# Changelog

## 0.1.6 - 2026-08-09

- Split the frozen identity layer into 53 source-reported GC-MS labels and 49 unique canonical molecular entities. Downstream DESS, COCONUT taxonomy and RDKit screening now execute once per canonical entity.
- Made `identity/compound_registry.*` canonical-entity keyed and added `identity/reported_identity_registry.*` for the immutable 53-label analytical layer.
- Screened the uploaded COCONUT v1 artifact and locked it as the authoritative supporting taxonomy model (`lightgbm_coconut_hierarchy_v1`, `taxonomy.coconut.v1`).
- Added strict SHA-256 verification for the model, metadata, normalized configuration and class lookup before taxonomy model construction.
- Removed the redundant standalone `coco_classifier` runtime lane and its false runtime warning; legacy COCO config fields remain ignored for compatibility.
- Added confidence-aware full-probability taxonomy outputs and relative-area × probability profile aggregation, including before/after class/superclass shift artifacts.
- Hardened reaction screening: same-formula positional-isomer pairs are redirected to analytical/library-assignment ambiguity, while large-molecule oxidation/dehydrogenation pairs require stronger fingerprint and MCS scaffold preservation.
- On the bundled study, the v2 reaction gate screens 211 pairs to 0 causal candidates, 1 analytical ambiguity and 210 pre-evidence rejections.
- Migrated Morgan fingerprint generation to `rdFingerprintGenerator.GetMorganGenerator` without changing the upstream taxonomy feature contract.
- Captured RDKit structure/standardization messages as structured QC instead of flooding routine console output.
- Actual uploaded-taxonomy-artifact acceptance: 49/49 canonical entities predicted, 0 unknown, 0 failed, 0 contract mismatches, strict file-hash verification matched.
- Test suite: 36 passed.

## 0.1.5 - 2026-08-09

- Added an explicit identity-adjudication registry and freeze semantics: `resolved`, `manual_corrected`, `excluded_unresolved`, and `unresolved_pending`.
- Added researcher-approved manual correction for the remaining Project Blends identity to PubChem CID 14042757 while preserving the original GC-MS/library-reported string verbatim.
- Independently validates adjudicated SMILES/InChI/InChIKey/formula/exact mass with RDKit before allowing downstream structure use.
- Added immutable `compound_registry_freeze_manifest`, `identity_adjudications`, and `excluded_identities` artifacts.
- Added a packaged 53-identity frozen Batch A registry derived from the validated 52/53 v0.1.4 run plus the explicit PubChem CID 14042757 manual adjudication; normal runs consume the freeze before external lookup.
- Added `--refresh-identities` to deliberately bypass the frozen registry for future re-adjudication without silently modifying the packaged freeze.
- Release identity QC now blocks only unresolved-pending identities and blocking cross-source conflicts; scientifically adjudicated exclusions can remain observable without being structure-eligible.
- Replaced DESS/taxonomy/COCO path-presence labels with executable runtime adapters and explicit execution summaries.
- DESS uses the upstream `DESSPhysicsProvider` for precomputed physics-artifact lookup; taxonomy uses the upstream COCONUT LightGBM provider and feature builder.
- COCO/Phytochemistry Classifier inference is delegated to its owning `/api/v1/phyto/predict` runtime; model files without that runtime are reported as `artifact_present_runtime_missing`.
- Added conservative pre-reaction gates for identity, formula delta, connectivity preservation, and storage-chemistry priors. All disappeared×appeared pairs remain auditable in `reaction_screening`, but only gated pairs reach reaction evidence retrieval.
- Renamed RDKit descriptor/conformer/MMFF-UFF work to `molecular_screening`; true `quantum` status now refers only to xTB/ORCA execution.
- Added `--no-molecular-screening`; `--no-quantum` remains a deprecated compatibility alias.
- Added runtime configuration for the standalone COCO classifier and made DuckDB/LightGBM standard dependencies for integrated supporting lanes.
- Test suite: 29 passed.

## 0.1.4 - 2026-08-09

- Added verified lookup-only aliases for the remaining long NIST-style systematic names; database authorities still supply canonical structures.
- Corrected class-weighted profile similarity so missing compound-class annotations are missing evidence rather than a shared `unclassified` pseudo-class.
- Reached 52/53 resolved Project Blends reported names before manual adjudication of the final transcription issue.

## 0.1.3 - 2026-08-09

- Separated peak-level alternative GC-MS/library assignments (`candidate_names`) from compound-level identity synonyms.
- Added conservative name normalization and lookup-only aliases for report transcription artifacts; the original GC-MS name remains immutable.
- Prevented manuscript SMILES from being selected as an identity evidence source.
- Corrected identity conflict semantics so same-source stereoisomer multiplicity is informational and only independent-authority disagreement blocks resolution.
- Added `peak_assignment_ambiguities` artifacts and v3 identity QC/manifest metadata.

## 0.1.2 - 2026-08-09

- Switched Batch A to a name-first identity contract and quarantined legacy manuscript SMILES as provenance only.

## 0.1.1 - 2026-08-09

- Fixed heterogeneous `plant_ratios` Parquet round-trip and made JSONL canonical for profile repository rows.
- Added first-use auto-bootstrap for `project_blends_reported_v1`.
