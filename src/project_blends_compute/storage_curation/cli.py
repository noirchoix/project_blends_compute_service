from __future__ import annotations

import argparse
import json
from pathlib import Path

from project_blends_compute.settings import Settings
from project_blends_compute.storage_curation.builder import StorageReactionCurationBuilder
from project_blends_compute.storage_curation.models import StorageCurationBuildRequest


def main() -> None:
    parser = argparse.ArgumentParser(description="Build reaction_curation-compatible storage reaction evidence artifacts")
    parser.add_argument("--input-json", "--input", dest="input_json", required=True)
    parser.add_argument("--output-root", default="artifacts/reaction_curation")
    parser.add_argument("--registry", default="artifacts/reaction_curation/benchmark_registry.json")
    parser.add_argument("--reaction-curation-project-root", default=None)
    args = parser.parse_args()
    settings = Settings()
    payload = json.loads(Path(args.input_json).read_text(encoding="utf-8"))
    request = StorageCurationBuildRequest.model_validate(payload)
    builder = StorageReactionCurationBuilder(
        Path(args.output_root).resolve(),
        Path(args.registry).resolve(),
        Path(args.reaction_curation_project_root).resolve() if args.reaction_curation_project_root else settings.reaction_curation_project_root,
    )
    response = builder.build(request)
    print(response.model_dump_json(indent=2))


if __name__ == "__main__":
    main()
