# Source-preserving Project Blends data

- `project_blends_reported_v1.json`: nested canonical ingest representation.
- `project_blends_reported_v1.csv`: flat interchange copy; nested values are JSON strings.
- `project_blends_experiment_metadata.json`: documented and unknown method/storage metadata.
- `blend_definitions.json`: plant composition and reported mass ratios.

The rows were digitized from the reported Chapter 4 tables. Reported names, library quality values and SMILES are retained as source fields and are not treated as corrected identities. The extraction records source table/row identifiers and places ambiguous names in `candidate_names`.
