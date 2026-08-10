# API examples

## Resolve identities

```json
POST /v1/identity/resolve
{
  "items": [
    {
      "reported_name": "Citral",
      "reported_smiles": "CC(C)=CCCC(C)=CC=O",
      "candidate_names": ["geranial", "neral"]
    }
  ],
  "online": true,
  "sources": ["pubchem", "chebi", "nist", "lotus_coconut", "fooddb"]
}
```

## Analyse profile records

```json
POST /v1/profiles/analyse
{
  "records": [
    {
      "sample_id": "blend_a",
      "blend_id": "A",
      "timepoint": "before_storage",
      "storage_days": 0,
      "reported_compound_name": "Citral",
      "area_percent": 19.03
    },
    {
      "sample_id": "blend_a",
      "blend_id": "A",
      "timepoint": "after_storage",
      "storage_days": 28,
      "reported_compound_name": "Eugenol",
      "area_percent": 24.60
    }
  ]
}
```

## Start an integrated run

```json
POST /v1/runs
{
  "dataset_id": "project_blends_reported_v1",
  "resolve_identities_online": true,
  "refresh_identities": false,
  "include_food_provenance": true,
  "include_reaction_intelligence": true,
  "include_molecular_screening": true,
  "queue_external_quantum": false,
  "include_exploratory_foodchem_ml": false,
  "strict_lanes": ["pipeline_fooddb", "rxn_bridge", "reaction_curation"],
  "storage_context": {
    "duration_days": 28,
    "container": "airtight",
    "temperature_c": null,
    "light_exposure": null,
    "oxygen_exposure": null
  }
}
```

The legacy `include_quantum_descriptors` field remains accepted only as a deprecated alias for `include_molecular_screening`.

## Queue targeted xTB work

```json
POST /v1/quantum/jobs
{
  "task": "conformer_search",
  "engine": "xtb",
  "molecules": [
    {
      "compound_id": "cmp-example",
      "name": "example",
      "smiles": "CCO"
    }
  ],
  "solvent": "n-hexane",
  "temperature_k": 298.15
}
```
