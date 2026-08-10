# storage_reaction_evidence v1.0.1 integration contract

Project Blends v0.1.7 consumes the complete registered `storage_reaction_evidence` artifact family rather than treating the reaction table as the entire evidence corpus.

The active artifact is resolved through `reaction_curation_registry` and must provide an immutable `manifest.json` plus a passing `quality_report.json`. Manifest SHA-256 values are verified before evidence is used.

The runtime consumes:

- `storage_reactions.*` — condition-bounded transformation precedents only;
- `storage_condition_context.*` — reaction-specific experimental context;
- `storage_evidence_sources.*` — source metadata and DOI linkage;
- `nonreaction_explanations.*` — volatility, degradation, analytical, contamination/carryover and compositional alternatives;
- `identity_linkage.*` — linkage to the frozen Project Blends canonical entity registry.

## Claim boundary

Storage evidence is interpretive support. It does not convert GC-MS peak appearance/disappearance or relative peak-area changes into causal precursor-to-product reactions. Low-compatibility synthesis, forced-degradation or biological precedents remain explicitly condition-mismatched.

## v1.0.1 expected corpus

- 13 literature sources
- 27 non-reaction / analytical explanation records
- 2 transformation-precedent records
- 2 reaction-specific condition records
- 49 canonical identity-linkage rows

The v1.0.1 patch corrects two caryophyllene-retention records so the 11.67→4.74% and 13.74→6.43% trajectories link to `(-)-Caryophyllene`, not `Caryophyllene oxide`.
