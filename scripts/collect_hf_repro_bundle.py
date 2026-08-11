from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

from hf_repro_common import (
    BUNDLE_DIRNAME,
    BUNDLE_SCHEMA,
    EXPECTED_STORAGE_VERSION,
    LOCKED_RUN_ID,
    LOCKED_SERVICE_VERSION,
    build_minimal_rxn_registry,
    collect_fooddb,
    collect_storage_evidence,
    copy_runtime_code,
    file_manifest,
    flatten_path_manifest,
    git_commit_if_available,
    json_load,
    json_write,
    make_dataset_card,
    portable_copy_summary,
    runtime_versions,
    sha256_file,
    utc_now,
    verify_file_manifest,
    audit_bundle,
    write_license_bundle,
    write_runtime_lock,
)


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Collect the exact external runtime artifacts used by Project Blends v0.1.7 into a portable Hugging Face bundle."
    )
    p.add_argument("--path-manifest", default="config/path_manifest.local.json")
    p.add_argument("--output-dir", default=BUNDLE_DIRNAME)
    p.add_argument("--force", action="store_true", help="Replace an existing output directory.")
    return p


def collect(project_root: Path, path_manifest: Path, output_dir: Path, *, force: bool = False) -> dict:
    project_root = project_root.resolve()
    path_manifest = path_manifest if path_manifest.is_absolute() else (project_root / path_manifest)
    output_dir = output_dir if output_dir.is_absolute() else (project_root / output_dir)

    if not path_manifest.exists():
        raise FileNotFoundError(f"Path manifest does not exist: {path_manifest}")
    if output_dir.exists():
        if not force:
            raise FileExistsError(f"Output directory already exists: {output_dir}. Use --force to replace it.")
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True)

    raw_manifest = json_load(path_manifest)
    paths = flatten_path_manifest(raw_manifest)
    for required in ("rxn_bridge_project_root", "rxn_artifact_registry", "reaction_curation_registry"):
        if paths.get(required) in (None, ""):
            raise ValueError(f"Required configured path is missing: {required}")

    rxn_source_root = Path(str(paths["rxn_bridge_project_root"])).expanduser().resolve()
    rxn_source_registry = Path(str(paths["rxn_artifact_registry"])).expanduser().resolve()
    reaction_registry = Path(str(paths["reaction_curation_registry"])).expanduser().resolve()

    records = []
    rxn_destination = output_dir / "rxn_bridge_runtime"
    records.extend(copy_runtime_code(rxn_source_root, rxn_destination))
    portable_rxn_registry, copied = build_minimal_rxn_registry(rxn_source_registry, rxn_destination)
    records.extend(copied)

    rc_destination = output_dir / "reaction_curation_runtime"
    portable_rc_registry, copied = collect_storage_evidence(reaction_registry, rc_destination)
    records.extend(copied)
    active_storage = portable_rc_registry["storage_reaction_evidence"]["active_version"]
    if active_storage != EXPECTED_STORAGE_VERSION:
        raise ValueError(
            f"Expected storage_reaction_evidence {EXPECTED_STORAGE_VERSION} for the frozen publication run; got {active_storage}"
        )

    _, copied = collect_fooddb(paths, output_dir)
    records.extend(copied)

    versions = runtime_versions()
    write_runtime_lock(output_dir, versions)
    write_license_bundle(output_dir, reaction_curation_registry=reaction_registry)
    (output_dir / "README.md").write_text(make_dataset_card(), encoding="utf-8")
    (output_dir / "REDISTRIBUTION_REVIEW.md").write_text(
        "# Redistribution / license review\n\n"
        "Component-level terms are recorded in `ARTIFACT_LICENSES.json` and `LICENSES/`. "
        "The bundle intentionally uses Hugging Face `license: other`; no single license is applied to all artifacts.\n\n"
        "Reviewed upstream terms for this publication bundle:\n"
        "- mapped rxnutils-derived reaction/template artifact: Apache-2.0 notice retained;\n"
        "- DESS-derived serving artifact: DESRES Data Sets License notice/conditions/disclaimer retained;\n"
        "- reaction_curation storage evidence artifact: MIT lineage as declared by the publisher;\n"
        "- COCONUT taxonomy artifact: CC0-1.0;\n"
        "- FoodDB serving artifact: CC BY-NC 4.0 / non-commercial terms; source acknowledgment is required and commercial use/redistribution requires permission.\n\n"
        "Public upload is technically permitted only after the publisher confirms these notices remain attached and "
        "uses `--acknowledge-redistribution-rights`. This tooling does not provide legal advice.\n",
        encoding="utf-8",
    )

    source_info = {
        "project_blends_code_commit": git_commit_if_available(project_root),
        "rxn_bridge_code_commit": git_commit_if_available(rxn_source_root),
        "rxn_artifact_registry_sha256": sha256_file(rxn_source_registry),
        "reaction_curation_registry_sha256": sha256_file(reaction_registry),
        "path_manifest_source": path_manifest.name,
    }
    manifest = {
        "schema_version": BUNDLE_SCHEMA,
        "created_at_utc": utc_now(),
        "service_version": LOCKED_SERVICE_VERSION,
        "locked_run_id": LOCKED_RUN_ID,
        "purpose": "portable external runtime closure for exact Project Blends v0.1.7 non-quantum reproduction",
        "storage_evidence_version": active_storage,
        "rxn_registry_lanes": {
            lane: block["active_version"] for lane, block in portable_rxn_registry.items()
        },
        "source_provenance": source_info,
        "runtime_environment": versions,
        "scientific_exclusions": [
            "raw GC-MS/chromatogram exports are not required for normal reruns because the canonical source-preserving digitization is bundled in the service repository",
            "xTB/ORCA/Psi4 are excluded because the frozen publication run performed no quantum calculations",
            "foodchem_ml is excluded because it is disabled and not part of the evidence lane",
            "uspto_llm_multistep_only is excluded because it was screened and not adopted as a core Project Blends dependency",
        ],
        "copy_summary": portable_copy_summary(records, output_dir),
        "files": [],
    }
    manifest_path = output_dir / "REPRODUCIBILITY_MANIFEST.json"
    json_write(manifest_path, manifest)
    manifest["files"] = file_manifest(output_dir, exclude={"REPRODUCIBILITY_MANIFEST.json"})
    json_write(manifest_path, manifest)
    (output_dir / "REPRODUCIBILITY_MANIFEST.sha256").write_text(
        sha256_file(manifest_path) + "  REPRODUCIBILITY_MANIFEST.json\n", encoding="ascii"
    )

    verification = verify_file_manifest(output_dir, manifest["files"])
    if not verification["pass"]:
        raise RuntimeError(f"Bundle verification failed immediately after collection: {json.dumps(verification, indent=2)}")
    audit = audit_bundle(output_dir)
    if not audit["pass"]:
        raise RuntimeError(f"Bundle audit failed immediately after collection: {json.dumps(audit, indent=2)}")
    return {
        "ok": True,
        "output_dir": str(output_dir),
        "service_version": LOCKED_SERVICE_VERSION,
        "locked_run_id": LOCKED_RUN_ID,
        "storage_evidence_version": active_storage,
        "files": len(manifest["files"]),
        "bytes": sum(int(row["bytes"]) for row in manifest["files"]),
        "manifest_sha256": sha256_file(manifest_path),
        "verification": verification,
        "audit": audit,
    }


def main() -> None:
    args = _build_parser().parse_args()
    project_root = Path(__file__).resolve().parents[1]
    result = collect(project_root, Path(args.path_manifest), Path(args.output_dir), force=args.force)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
