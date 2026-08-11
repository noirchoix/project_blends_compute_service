# Hugging Face artifact reproducibility — Project Blends v0.1.7

## Purpose

The Git repository contains the frozen computation code and the canonical Project Blends source-preserving digitization. The publication run also depends on external artifact-backed lanes that were originally addressed with machine-specific Windows paths. This workflow packages the **runtime closure** of those lanes into one Hugging Face dataset repository and reconstructs `config/path_manifest.local.json` automatically after download.

This is an operational/reproducibility layer only. It does not change the v0.1.7 scientific algorithms or claim boundaries.

## What must be distributed for the frozen publication run

Exact artifact-backed inference requires the exact runtime artifacts actually consumed:

- `storage_reaction_evidence v1.0.1` curated files and benchmark registry;
- the active mapped `uspto_templates` runtime artifact required for `rxn_bridge` readiness;
- the DESS serving DuckDB used for precomputed lookup;
- the exact COCONUT taxonomy inference artifact, including both LightGBM model files, metadata, normalized config, and class lookup;
- the FoodDB serving DuckDB and/or the three curated fallback Parquet tables;
- the small `rxn_bridge` provider-code snapshot needed by v0.1.7 dynamic imports.

The **original training datasets and training pipelines are not required to reproduce the frozen inference run**. They are required only if a reproducer wants to retrain/rebuild those upstream artifacts. Likewise, xTB/ORCA binaries are not required because the publication run contains no quantum calculations.

## What is deliberately not packaged

- raw GC-MS/chromatogram files (normal runs consume the canonical 75-row digitization bundled in Git);
- the original `.doc` source (needed only to reconstruct the digitization provenance);
- FoodChem ML (disabled/non-evidence lane);
- xTB, ORCA or Psi4 (not used in the locked publication run);
- the screened `uspto_llm_multistep_only` artifact (not adopted as a core dependency);
- upstream training caches, notebooks, virtual environments, or superseded models.

## 1. Assemble `data_hf` on the validated machine

From the Project Blends v0.1.7 repository with the working local artifact manifest:

```bash
pip install -e ".[artifacts,dev,repro]"
python scripts/collect_hf_repro_bundle.py \
  --path-manifest config/path_manifest.local.json \
  --output-dir data_hf
```

The collector fails closed if a required lane/path is missing. It writes:

```text
data_hf/
├── REPRODUCIBILITY_MANIFEST.json
├── REPRODUCIBILITY_MANIFEST.sha256
├── README.md
├── REDISTRIBUTION_REVIEW.md
├── environment/
│   ├── runtime_versions.json
│   └── requirements-repro.lock.txt
├── rxn_bridge_runtime/
│   ├── reaction_framework/
│   ├── pipelines/
│   └── data/rxn_artifacts/
│       ├── registry/artifact_registry.json
│       ├── uspto_templates/...
│       ├── dess_physics/...
│       └── taxonomy_coconut/...
├── reaction_curation_runtime/
│   └── data/rxn_artifacts/reaction_curation/
│       ├── benchmark_registry.json
│       └── curated/storage_reaction_evidence/v1.0.1/...
└── fooddb/
    ├── serving.duckdb
    ├── curated_food_lookup.parquet
    ├── curated_compound_lookup.parquet
    └── curated_food_compound_content.parquet
```

Every distributed file is SHA-256 listed, and the manifest itself has a detached SHA-256 file. Original absolute Windows paths are not published.

## 2. Review redistribution rights

Before a **public** upload, inspect `data_hf/REDISTRIBUTION_REVIEW.md` and verify the redistribution terms for the upstream COCONUT-derived model, DESS artifact, FoodDB-derived tables and mapped USPTO/template artifact. Technical reproducibility does not itself grant redistribution rights.

A private Hugging Face repository is the safe default until that review is complete.

## 3. Upload to Hugging Face

Authenticate with Hugging Face (`hf auth login` or an `HF_TOKEN`), then:

```bash
python scripts/upload_hf_repro_bundle.py \
  --repo-id <user-or-org>/project-blends-v017-artifacts \
  --bundle-dir data_hf
```

The script creates a **private dataset repository by default**, verifies the local SHA-256 manifest, uploads the folder with the resumable large-folder API, and prints the resulting immutable Hub commit SHA.

After redistribution rights are cleared, a public upload can be created with:

```bash
python scripts/upload_hf_repro_bundle.py \
  --repo-id <user-or-org>/project-blends-v017-artifacts \
  --bundle-dir data_hf \
  --public \
  --acknowledge-redistribution-rights
```

## 4. Clean-room reproduction

On another machine:

```bash
git clone https://github.com/noirchoix/project_blends_compute_service.git
cd project_blends_compute_service
# Checkout the reproducibility-tooling tag/commit recorded with the HF bundle,
# recommended tag name: v0.1.7-repro1
git checkout v0.1.7-repro1

python -m venv .venv
source .venv/bin/activate        # Windows Git Bash
pip install -e ".[artifacts,repro]"

python scripts/bootstrap_hf_repro.py \
  --repo-id <user-or-org>/project-blends-v017-artifacts \
  --revision <PINNED_HF_COMMIT_SHA>

# Recommended before executing the scientific run when these exact versions
# are available for the reproducer's Python/platform:
pip install -r data_hf/environment/requirements-repro.lock.txt
```

The bootstrap downloads into repository-local `data_hf/`, verifies the detached manifest digest and every listed file hash, checks the current Project Blends Git commit against the commit recorded by the bundle when both are available, and writes `config/path_manifest.local.json` from the cloned repository root. The generated manifest uses local absolute paths because the current frozen settings loader does not reinterpret arbitrary path-manifest values relative to the Git root; no user-specific path editing is required.

Then run:

```bash
project-blends-run \
  --path-manifest config/path_manifest.local.json \
  run \
  --dataset-id project_blends_reported_v1

project-blends-run \
  --path-manifest config/path_manifest.local.json \
  validate <NEW_RUN_ID>
```

The publication target is structural/scientific reproduction of frozen run `pb-20260810T160809-be9a0fcb65`: 53 reported labels → 49 canonical entities, 211 reaction pairs → 0 causal candidates, 13/27/2 storage-evidence counts, 49/49 taxonomy predictions, and a strict non-quantum validation pass.

## 5. Offline test before upload

The same bootstrap can test a local bundle without the Hub:

```bash
python scripts/bootstrap_hf_repro.py \
  --source-dir /path/to/copied/data_hf \
  --local-dir data_hf_cleanroom \
  --path-manifest config/path_manifest.cleanroom.json
```

This is useful for validating a clean-room artifact bundle before publication.

## Reproducibility levels

There are two distinct levels:

1. **Frozen-run reproduction** — requires the exact code plus the runtime artifacts listed above. This is what the publication needs.
2. **Upstream rebuild/retraining reproduction** — additionally requires original training datasets, upstream build pipelines, training seeds/configs, environments and licenses. This is substantially larger and is not necessary to verify the claims in the frozen Project Blends publication run.
