from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from project_blends_compute.artifacts.validation import validate_run_for_release
from project_blends_compute.orchestrator import RunManager
from project_blends_compute.schemas.runs import RunRequest
from project_blends_compute.settings import Settings, hydrate_paths_from_manifest


def main() -> None:
    parser = argparse.ArgumentParser(description="Project Blends computation-service CLI")
    parser.add_argument("--path-manifest", type=Path, default=None)
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="Run the integrated deterministic workflow")
    run.add_argument("--dataset-id", default="project_blends_reported_v1")
    run.add_argument("--online-identities", action="store_true")
    run.add_argument("--refresh-identities", action="store_true", help="Bypass the frozen Batch A registry and re-resolve identities")
    run.add_argument("--no-fooddb", action="store_true")
    run.add_argument("--no-reactions", action="store_true")
    run.add_argument("--no-molecular-screening", action="store_true", help="Disable RDKit descriptor/conformer screening")
    run.add_argument("--no-quantum", action="store_true", help="Deprecated alias for --no-molecular-screening")
    run.add_argument("--queue-external-quantum", action="store_true")
    run.add_argument("--include-foodchem-ml", action="store_true")
    status = sub.add_parser("status")
    status.add_argument("run_id")
    validate = sub.add_parser("validate")
    validate.add_argument("run_id")
    validate.add_argument("--require-quantum", action="store_true")
    release = sub.add_parser("release")
    release.add_argument("run_id")
    release.add_argument("--release-id", default=None)
    args = parser.parse_args()

    settings = hydrate_paths_from_manifest(Settings(path_manifest=args.path_manifest))
    manager = RunManager(settings)
    if args.command == "run":
        request = RunRequest(
            dataset_id=args.dataset_id,
            resolve_identities_online=args.online_identities,
            refresh_identities=args.refresh_identities,
            include_food_provenance=not args.no_fooddb,
            include_reaction_intelligence=not args.no_reactions,
            include_molecular_screening=not (args.no_molecular_screening or args.no_quantum),
            include_quantum_descriptors=not (args.no_molecular_screening or args.no_quantum),
            queue_external_quantum=args.queue_external_quantum,
            include_exploratory_foodchem_ml=args.include_foodchem_ml,
        )
        print(asyncio.run(manager.execute(request)).model_dump_json(indent=2))
    elif args.command == "status":
        print(manager.get(args.run_id).model_dump_json(indent=2))
    elif args.command == "validate":
        print(json.dumps(validate_run_for_release(manager.store, args.run_id, require_quantum=args.require_quantum), indent=2))
    elif args.command == "release":
        acceptance = validate_run_for_release(manager.store, args.run_id)
        path = manager.store.lock_release(args.run_id, acceptance, args.release_id)
        print(json.dumps({"ok": True, "release_manifest": str(path)}, indent=2))


if __name__ == "__main__":
    main()
