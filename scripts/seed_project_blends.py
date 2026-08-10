#!/usr/bin/env python
from __future__ import annotations

import argparse
import json
from pathlib import Path

from project_blends_compute.profiles import ProfileRepository
from project_blends_compute.schemas.profiles import PeakRecord, ProfileIngestRequest
from project_blends_compute.settings import Settings


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed the reported Project Blends GC-MS profiles")
    parser.add_argument("--input", type=Path, default=Path("data/raw/project_blends_reported_v1.json"))
    parser.add_argument("--dataset-id", default="project_blends_reported_v1")
    parser.add_argument("--replace", action="store_true")
    args = parser.parse_args()
    settings = Settings()
    settings.ensure_runtime_dirs()
    repo = ProfileRepository(settings.state_root / "profile_datasets")
    payload = json.loads(args.input.read_text(encoding="utf-8"))
    records = [PeakRecord.model_validate(row) for row in payload]
    response = repo.ingest(ProfileIngestRequest(records=records, dataset_id=args.dataset_id, source_document="PROJECT BLENDS.doc", replace=args.replace))
    print(response.model_dump_json(indent=2))


if __name__ == "__main__":
    main()
