# Scientific contract

1. The source observations are reported GC-MS relative peak-area percentages before storage and after four weeks.
2. A disappearance/appearance pair is not automatically a reaction.
3. The GC-MS/library reported name is the analytical identity anchor. Canonical structures are external-database or explicitly adjudicated identities, not manuscript-SMILES reconstructions.
4. The 53 reported identity labels are preserved as analytical observations, while downstream molecular computation is performed on 49 unique canonical chemical entities.
5. FoodDB documented occurrence supports source plausibility only; it does not identify the origin of a particular chromatographic peak.
6. FoodChem ML outputs are hypotheses, never occurrence evidence.
7. rxn_bridge templates are analogous synthetic-reaction evidence and may be out of distribution for ambient storage chemistry.
8. reaction_curation reaction-specific conditions are primary; signature-level conditions are broader support only.
9. DESS physics artifacts and the authoritative COCONUT `taxonomy.coconut.v1` model are supporting computational evidence and cannot confirm analytical identity or a storage reaction.
10. Taxonomy predictions retain confidence, entropy and probability vectors. Probability-weighted profile aggregation is model-assisted composition evidence, not measured class abundance.
11. RDKit descriptors, ETKDG conformers and MMFF/UFF energies are molecular/cheminformatics screening, not quantum chemistry. True quantum-chemistry claims require completed xTB/ORCA calculations.
12. No inferential p-values, kinetic constants, or shelf-life claims are produced without documented replicates and appropriate time-series data.
13. Every publication claim must be labelled as observed, derived, literature-supported, computationally supported, hypothesized, unresolved or rejected.

## Batch A identity and canonical-entity policy

The canonical SMILES printed in the undergraduate report were manually attributed after the GC-MS/library identification and are treated as untrusted legacy annotations. They are retained for provenance and error auditing only and MUST NOT be used to rank, accept, reject, cache, or generate a canonical identity.

Canonical structures are obtained name-first from external identity authorities. Conservative lookup variants may repair punctuation, Greek-token, spelling, or Word line-wrap transcription artifacts, but the reported analytical string remains immutable. Peak-level `candidate_names` are alternative library assignments and are not compound synonyms.

Manual identity corrections are explicit adjudications, never silent substitutions. The final transcription issue preserves the reported `Imidazolo...6-chloro...` source string while mapping the reviewed canonical identity to PubChem CID 14042757, 6-Nitro-2-(4-nitrophenyl)imidazo[1,2-a]pyridine.

The frozen publication model has two linked layers:

```text
reported_identity_registry / reported_name_crosswalk: 53 source labels
                                      ↓
compound_registry:                    49 canonical entities
```

Reaction intelligence, DESS, taxonomy, molecular screening and quantum jobs MUST consume only canonical `downstream_structure_eligible` entities. Legacy `reported_smiles` is prohibited as a fallback.

## Batch B pre-evidence reaction policy

Every disappeared×appeared pair is retained as an audit record, but only a bounded subset may be promoted to reaction evidence retrieval. Promotion requires accepted structure identity, an interpretable formula delta, preserved connectivity, and a conservative storage-chemistry prior. This is a screening rule, not evidence that a reaction occurred.

Same-formula positional-isomer pairs are analytical/library-assignment ambiguities unless direct storage-reaction evidence establishes the transformation. Larger +O/-H oxidation/dehydrogenation hypotheses require strong scaffold preservation and cannot pass merely because the elemental delta resembles oxidation. The current bundled data therefore legitimately yield zero causal reaction candidates after pre-evidence gating.

Non-reaction explanations—including volatilization, degradation to unobserved products, non-detection, library misidentification, co-elution, contamination/carryover and relative-area normalization—remain first-class alternatives.
