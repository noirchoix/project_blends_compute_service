# Integration matrix

| Service module | Upstream resource | Interface | Failure behaviour |
|---|---|---|---|
| `identity.sources.PubChemSource` | PubChem PUG REST | reported/adjudicated name → identity properties | Per-query fail-closed; unresolved identity remains pending unless explicitly adjudicated |
| `identity.IdentityAdjudicationRegistry` | frozen local adjudications | source-reported name → reviewed canonical identity/exclusion | Invalid structure/hash contract raises; original reported name is never rewritten |
| `identity.build_canonical_entities` | frozen identity registry | 53 reported labels → 49 canonical entities | Conflicting structure fields under one entity key fail closed |
| `identity.sources.ChEBISource` | ChEBI public API | name search → ontology identity | Per-query fail-closed |
| `provenance.FoodDBAdapter` | pipeline_fooddb | DuckDB views or Parquet/CSV exports | Lane unavailable; no inferred occurrence evidence |
| `provenance.FoodChemMLAdapter` | foodchem_ml | `/api/v1/link-predictions` | Disabled unless URL configured; outputs remain exploratory |
| `reactions.RxnBridgeAdapter` | rxn_bridge | artifact registry / template store | Lane unavailable; no template support claimed |
| `reactions.ReactionCurationAdapter` | reaction_curation | benchmark registry + reaction-specific conditions | Lane unavailable; no curated support claimed |
| `supporting.DESSRuntimeLane` | rxn_bridge DESS provider | precomputed `dess_physics` lookup | `runtime_unavailable`/`executed_no_match`; sparse coverage remains explicit |
| `supporting.TaxonomyRuntimeLane` | uploaded COCONUT v1 via rxn_bridge | strict SHA-256 verification → upstream feature builder → LightGBM hierarchy | artifact mismatch fails lane construction; unknown/failed inference is surfaced |
| `supporting.aggregate_taxonomy_profiles` | taxonomy probabilities + GC-MS profile | area × probability aggregation | uncovered area and low-confidence predictions remain explicit |
| `quantum.RDKitDescriptorEngine` | RDKit | **molecular screening** descriptors/conformers/MMFF-UFF | warnings captured as structured QC; never labelled quantum chemistry |
| `quantum.XTBEngine` | xTB executable | persistent SQLite quantum job | Job requires configured executable; queue entry is not a result |
| `quantum.ORCAEngine` | ORCA executable | persistent SQLite quantum job | Not claimed complete until engine execution succeeds |
| `reports.ReportBuilder` | ChemRAG contract | lane-oriented JSON export | Run retains integrated report if downstream consumer is absent |

## Authority boundaries

- Identity databases or explicit reviewed adjudications resolve structures; FoodDB cannot override the canonical identity registry.
- The 53-label analytical layer is not the molecular inference layer; molecular models run once across 49 canonical entities.
- FoodDB documents occurrence; FoodChem ML cannot promote a prediction to occurrence evidence.
- Pre-reaction screening reduces implausible cross-products but does not establish chemistry.
- Same-formula positional-isomer matches are routed to analytical ambiguity, not causal reaction inference, without direct evidence.
- rxn_bridge supplies analogies; reaction_curation supplies direct/condition-linked evidence.
- DESS and COCONUT taxonomy support interpretation but cannot prove a structure or transformation.
- There is no separate COCO classifier lane in Project Blends v0.1.6; the screened uploaded artifact is the COCONUT taxonomy model itself.
- RDKit molecular screening is not quantum chemistry. Completed xTB/ORCA calculations can support plausibility but still cannot prove storage conversion.
