# Uploaded resource integration audit

The service was built against the uploaded source snapshots. The upstream projects remain external dependencies; this repository contains adapters and bounded extensions rather than vendored copies.

| Resource | SHA-256 | Applied role |
|---|---|---|
| `PROJECT BLENDS.doc` | `82be236157813bb3ea48a01c15f9c886f420212b4e8840dde3d18055a448d9cf` | Source-of-truth reported profiles and method metadata |
| `reaction_curation-main.zip` | `69b0f5d0a02822d66bf01ae0557bc79ab5a66180c997b29f9012752bfcbd04a8` | Authoritative reaction evidence registry and condition context |
| `rxn_bridge-main.zip` | `3cfc966d1ca58322d6c748e719c1c29aab0033dbf697e0962e21936928840aba` | Artifact-backed template retrieval, reaction summaries and curation adapters |
| `pipeline_fooddb-main.zip` | `cb277c3f1c64382b1176e83272258cef956936efa57da5a191f933b1f03481a6` | Authoritative documented food/plant–compound provenance |
| `foodchem_ml-main.zip` | `62a1307e6f09ae8513008e2f2d21a6185b48f92e376b4cd7b2ec0d200a49036c` | Optional exploratory candidate-link predictions only |
| `RouteLens-feature-artifact-backed-inference-service.zip` | `f60a7e3536d1eacb707b20a50d23e8db39311f58b5dfc0a8ee016d0c8a8d1bc5` | Artifact immutability, release locking, acceptance and persistent queue patterns |
| `chemrag-main.zip` | `20ef0d7dbdf8cb073fc8c8e8ff005f37e2081f5b0e2be29067c787bc1832039e` | Downstream lane-oriented evidence export and publication context |

## Direct integration surfaces

### reaction_curation

- `reaction_curation/schemas.py`: dataset-kind extension point.
- `reaction_curation/benchmark_registry.py`: authoritative registry writer.
- Existing curated reaction and condition-context artifact contracts.
- The new `storage_reaction_evidence` builder writes reaction-specific condition rows first and preserves non-reaction explanations.

### rxn_bridge

- `reaction_framework/providers/rxnutils_provider.py`
- `reaction_framework/providers/template_store.py`
- `reaction_framework/providers/artifact_registry.py`
- `reaction_framework/providers/curation_condition_store.py`

The runtime retrieves reaction-specific conditions through `ConditionStore.get_for_reaction(...)`; signature-level context is secondary.

### pipeline_fooddb

The adapter reads `curated_food_lookup`, `curated_compound_lookup`, and `curated_food_compound_content` from DuckDB or exported tables. It does not use FoodDB as a canonical structure authority because the uploaded SQL contains dataset-specific structure-field remapping.

### foodchem_ml

The adapter calls its link-prediction API only when explicitly enabled. Every output is labelled `exploratory_prediction` and `not_occurrence_evidence`.

### RouteLens

Only infrastructure patterns were adapted:

- immutable SHA-256-verified artifact bundles;
- atomic staging-to-release installation;
- SQLite-backed resumable work queue;
- strict readiness and acceptance gates;
- immutable release locking;
- abstention when applicability or evidence is insufficient.

Route/step ontology, patent splits and route-quality models were not imported.

### ChemRAG

Each completed run exports `rag/latest_subsystem_rag_export.json`, containing identity QC, profile analysis, food provenance, reaction intelligence, computational chemistry, uncertainty and evidence packets.
