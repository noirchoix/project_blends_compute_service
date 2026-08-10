# Project Blends Compute Service

Artifact-backed computation service for converting the reported Project Blends GC-MS study into a reproducible computational-phytochemistry workflow.

The service preserves the original observations as reported data, resolves compound identities through a PubChem-led hierarchy with explicit adjudication provenance, compares before/after relative-area profiles, evaluates documented food/plant provenance, screens storage-transformation hypotheses through conservative chemistry gates, retrieves bounded reaction evidence, executes supporting DESS and COCONUT-taxonomy lanes when their owning runtimes are available, and exports ChemRAG-ready evidence packets.

## Locked architecture

| Lane | Authority | Runtime treatment |
|---|---|---|
| Compound identity | PubChem-led identity service | Reported GC-MS name → PubChem → ChEBI → NIST → LOTUS/COCONUT → FoodDB → explicit manual adjudication |
| Reaction evidence | `reaction_curation` | Storage-reaction evidence registry and reaction-specific conditions |
| Reaction analogies | `rxn_bridge` / rxnutils | Template retrieval, atom mapping and bond-change evidence |
| Food/plant provenance | `pipeline_fooddb` | Documented occurrence evidence |
| Exploratory food links | `foodchem_ml` | Optional hypothesis ranking; never occurrence evidence |
| DESS support | `rxn_bridge` DESS provider | Lookup of precomputed DESS physics artifacts; not live checkpoint inference |
| COCONUT taxonomy | `rxn_bridge` taxonomy provider | Authoritative `taxonomy.coconut.v1` LightGBM artifact; upstream feature builder + strict artifact SHA-256 verification |
| Release governance | RouteLens patterns | Immutable bundles, SHA-256 verification, SQLite queues, strict acceptance and release locking |
| Publication evidence | ChemRAG | Lane-oriented export and evidence packets |

## Scientific limits

- Inputs are reported GC-MS relative peak-area percentages, not absolute concentrations.
- The source has two timepoints and no documented analytical or biological replicate structure.
- Peak disappearance and appearance do not establish a chemical conversion.
- The GC-MS/library reported name is the analytical identity anchor. Manuscript SMILES are legacy provenance only.
- Manual corrections are never silent: the original reported string remains immutable and the corrected canonical identity is separately adjudicated and hashed.
- Reaction templates, DESS/taxonomy outputs, molecular screening, and quantum calculations assess plausibility or context; none proves that a storage reaction occurred.
- FoodChem ML outputs are always exploratory and never occurrence evidence.

See `docs/SCIENTIFIC_CONTRACT.md`.

## Service modules

```text
project_blends_compute/
├── api/                  FastAPI contract
├── identity/             name-first identity resolution, adjudication and freeze
├── profiles/             compositional analysis and stability metrics
├── provenance/           deterministic FoodDB and optional FoodChem ML
├── reactions/            pre-evidence gating, rxn_bridge and curation evidence
├── storage_curation/     storage_reaction_evidence artifact builder
├── storage_evidence/     full storage-evidence artifact consumer and linkage QC
├── supporting/           DESS and authoritative COCONUT taxonomy adapters
├── quantum/              xTB/ORCA engines and persistent jobs
├── uncertainty/          descriptive evidence-completeness aggregation
├── artifacts/            immutable bundles, manifests and release locks
└── reports/              evidence packets and ChemRAG export
```

RDKit descriptor/conformer/MMFF-UFF work is exported as `molecular_screening`, not as quantum chemistry.

## Installation

Python 3.11 or newer is required.

```bash
python -m venv .venv
source .venv/bin/activate              # Windows: .venv\Scripts\activate
pip install -e ".[artifacts,dev]"
```

`duckdb` and `lightgbm` are core runtime dependencies because the integrated DESS/taxonomy adapters use the upstream artifact-backed providers. `pyarrow` remains optional; without it, table artifacts fall back to CSV/JSONL.

## Configure local artifacts

Copy the path-manifest template and replace paths with the actual local paths:

```bash
cp config/path_manifest.example.json config/path_manifest.local.json
```

The COCONUT taxonomy artifact is resolved through the configured `rxn_artifact_registry`. There is no separate COCO-classifier runtime: the uploaded v1 artifact is the taxonomy model itself. `/ready` reports whether each provider can actually be constructed, not merely whether a directory exists. v0.1.7 also reports the full `storage_evidence` consumer separately from the reaction-candidate lookup lane.

## Canonical Project Blends dataset

The source-preserving digitization is already bundled at:

```text
data/raw/project_blends_reported_v1.json
```

Normal execution does **not** require the legacy Word document, LibreOffice, or a manual seed step. The built-in dataset auto-materializes on first use:

```bash
project-blends-run \
  --path-manifest config/path_manifest.local.json \
  run \
  --dataset-id project_blends_reported_v1
```

`python scripts/extract_project_blends.py ...` is retained only as a provenance/reconstruction utility if the canonical digitization must be regenerated from the original `.doc` source.

## Batch A canonical-entity freeze — v0.1.6

The identity contract distinguishes:

```text
resolved
manual_corrected
excluded_unresolved
unresolved_pending
```

`excluded_unresolved` is a scientifically adjudicated exclusion and does not block the identity freeze; `unresolved_pending` does. For the Project Blends built-in adjudication, the original reported string

```text
Imidazolo[1,2-a]pyridine, 6-chloro -2-(4-nitrophenyl)-
```

is preserved verbatim, while the researcher-approved canonical correction is separately recorded as PubChem CID 14042757, `6-Nitro-2-(4-nitrophenyl)imidazo[1,2-a]pyridine`. The adjudication registry validates the supplied SMILES, InChI, InChIKey, molecular formula and exact mass independently with RDKit before downstream use.

The packaged `data/reference/project_blends_compound_registry_v1.json` is the frozen Batch A registry. Normal integrated runs consume it first, so publication analyses do not drift when external databases change. `--online-identities` is only needed for identities not covered by the freeze. To deliberately bypass the freeze for a future re-adjudication, use both `--refresh-identities` and the desired online/cache source configuration; a refresh does not silently rewrite the packaged freeze.

The 53 source-reported GC-MS identity labels collapse to **49 unique canonical molecular entities**. `identity/reported_identity_registry.*` and `identity/reported_name_crosswalk.*` preserve the 53-label analytical layer; `identity/compound_registry.*` is one row per canonical entity. DESS, taxonomy and RDKit screening run once per canonical entity.

Batch A emits:

```text
identity/compound_registry.*              # 49 canonical entities
identity/reported_identity_registry.*     # 53 source-reported labels
identity/compound_registry_freeze_manifest.json
identity/identity_adjudications.json
identity/excluded_identities.*
identity/reported_name_crosswalk.*
identity/isomer_groups.*
identity/identity_candidates.*
identity/identity_conflicts.*
identity/manual_review_queue.*
identity/peak_assignment_ambiguities.*
identity/identity_qc_report.json
identity/identity_manifest.json
```

## Batch B pre-reaction gating — v0.1.6

The service no longer promotes every disappeared × appeared cross-product into reaction intelligence. Every pair is retained in an auditable screening table, but only pairs passing all four gates proceed to `rxn_bridge`/`reaction_curation`:

```text
identity/structure gate
→ formula-delta gate
→ connectivity-preservation gate
→ conservative storage-chemistry-prior gate
→ reaction candidate
```

Same-formula positional-isomer matches are now redirected to `reactions/reaction_analytical_ambiguities.*` rather than promoted as causal transformations. Large-molecule +O/-H candidates must preserve substantially more scaffold connectivity before promotion. In the bundled study this v2 gate screens 211 disappeared×appeared pairs to **0 causal reaction candidates**, **1 analytical-ambiguity record**, and 210 pre-evidence rejections. This is a scientifically valid abstention, not a failed analysis.

## Supporting evidence execution

DESS and taxonomy are loaded through the configured `rxn_bridge` artifact registry and execute once per canonical entity. DESS performs lookup against the precomputed `dess_physics` artifacts and reports sparse coverage truthfully.

The authoritative taxonomy artifact is the uploaded COCONUT v1 hierarchy model:

```text
artifact_version        v1
model_version           lightgbm_coconut_hierarchy_v1
feature_schema_version  taxonomy.coconut.v1
features                42 tabular + 2048-bit Morgan radius-2 = 2090
```

Project Blends does not duplicate its feature engineering. The upstream `CocoTaxonomyProvider` remains the feature-contract owner; v0.1.6 only replaces its deprecated Morgan API call with the equivalent `rdFingerprintGenerator.GetMorganGenerator` call in memory. Before model construction, the six authoritative model/metadata/config files are verified against packaged SHA-256 values.

Taxonomy output is confidence-aware. Top-1 predictions with confidence >=0.80 are marked high-confidence; lower-confidence predictions retain their full probability vectors. Profile-level chemical-class composition is calculated as `relative GC-MS area × model probability`, so uncertain classifications are not silently hardened into categorical facts.

There is **no standalone `coco_classifier` lane** in v0.1.6. The previously configured COCO runtime/path keys are deprecated compatibility settings and are ignored by the Project Blends supporting pipeline.

## Molecular screening versus quantum chemistry

RDKit screening is now explicitly separate:

```text
molecular_screening/
  rdkit_descriptor_results.*
  summary.json
```

It includes 2D descriptors, ETKDG conformers, MMFF94/UFF screening and heuristic susceptibility features. These are **not quantum chemistry**, reaction barriers, kinetics or shelf-life predictions.

True quantum work is reserved for external engines:

```text
quantum/
  queued_jobs.*
  summary.json
```

`--queue-external-quantum` queues GFN2-xTB jobs only when the configured xTB executable exists. ORCA is reported as available/not-run until a targeted calculation is explicitly submitted. A queued job is never counted as a completed quantum result.

To disable RDKit molecular screening:

```bash
project-blends-run ... run --no-molecular-screening
```

`--no-quantum` remains a deprecated compatibility alias for that old behavior.

## Storage evidence consumption — v0.1.7

`storage_reaction_evidence v1.0.1` is now a first-class runtime evidence source rather than only a registry prerequisite for reaction-candidate lookup. The consumer resolves the active dataset version from `reaction_curation_registry`, verifies the immutable artifact manifest by SHA-256, requires a passing curation QC report, and consumes all five artifact families:

```text
storage_reactions.*
storage_condition_context.*
storage_evidence_sources.*
nonreaction_explanations.*
identity_linkage.*
```

For the frozen v1.0.1 corpus the expected counts are **13 literature sources, 27 non-reaction/analytical records, 2 transformation precedents, 2 reaction-specific condition records and 49 canonical identity links**. Runtime linkage QC requires the 49 curated identities to match the frozen Project Blends canonical registry and checks every source, compound and sample reference before evidence packets are emitted.

The consumer runs even when pre-reaction gating yields zero causal candidates. This is intentional: volatility, degradation to unobserved products, analytical non-detection, contamination/carryover, library ambiguity and relative-area redistribution remain scientifically meaningful evidence without a precursor→product hypothesis. Transformation precedents retain condition compatibility and cannot override the conservative reaction gate.

The integrated run now writes:

```text
storage_evidence/source_evidence.*
storage_evidence/nonreaction_evidence.*
storage_evidence/transformation_precedents.*
storage_evidence/condition_compatibility.*
storage_evidence/sample_evidence.*
storage_evidence/compound_evidence.*
storage_evidence/storage_evidence_summary.json
```

Storage evidence also enters `reports/evidence_packets.json` and the ChemRAG export with source IDs/DOIs, sample and compound linkage, evidence type, directness, condition compatibility, claim class, support/contradiction role and an explicit claim boundary. No numerical confidence is invented for literature records that were not probabilistically calibrated.

The curation source used to build the current artifact is retained at:

```text
data/curation/storage_reaction_evidence_v1_0_1.source.json
```

To build a future immutable version, use:

```bash
project-blends-curate-storage \
  --input-json data/curation/storage_reaction_evidence_v1_0_1.source.json \
  --output-root /path/to/reaction_curation/data/rxn_artifacts/reaction_curation/curated \
  --registry /path/to/reaction_curation/data/rxn_artifacts/reaction_curation/benchmark_registry.json \
  --reaction-curation-project-root /path/to/reaction_curation
```

Reaction-specific condition context remains primary. Signature-level condition context is broader support only. See `docs/STORAGE_EVIDENCE_V1_0_1.md`.

## API and release

Principal endpoints remain under `/v1/*`; OpenAPI is available at `/docs`. Each finalized run is immutable and SHA-256 registered under `artifacts/runs/<run_id>/`.

Strict release validation requires an identity freeze with no `unresolved_pending` identities or blocking cross-source identity conflicts; the core FoodDB, rxn_bridge, reaction_curation **and storage_evidence** lanes; a consumed storage-evidence corpus with passing linkage QC and emitted storage evidence packets; reaction claim boundaries; and evidence packets. `--require-quantum` additionally requires **completed** quantum results; queued jobs do not satisfy the gate.

```bash
project-blends-run validate <run_id>
project-blends-run release <run_id>
```

## Tests

```bash
pytest -q
```

v0.1.7 adds acceptance tests for full `storage_reaction_evidence v1.0.1` artifact consumption, manifest SHA-256 verification, 49-entity linkage QC, non-causal evidence-packet boundaries, corrected caryophyllene semantic linkage, ChemRAG propagation and strict release consumption checks. The complete suite contains 43 tests.
