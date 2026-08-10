from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from project_blends_compute.artifacts.formats import read_table, write_records_table
from project_blends_compute.schemas.profiles import PeakRecord, ProfileIngestRequest, ProfileIngestResponse
from project_blends_compute.utils import sha256_file, utc_now_iso, write_json_atomic


class ProfileRepository:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def ingest(self, request: ProfileIngestRequest) -> ProfileIngestResponse:
        dataset_dir = self.root / request.dataset_id
        if dataset_dir.exists() and not request.replace:
            raise FileExistsError(f"Dataset already exists: {request.dataset_id}")
        dataset_dir.mkdir(parents=True, exist_ok=True)
        records = []
        warnings: list[str] = []
        for index, record in enumerate(request.records):
            payload = record.model_dump(mode="json")
            if not payload.get("peak_id"):
                payload["peak_id"] = f"{request.dataset_id}:{record.sample_id}:{record.timepoint.value}:{index:04d}"
            records.append(payload)
        table_result = write_records_table(
            records,
            dataset_dir / "profile_long",
            prefer_parquet=True,
            write_csv=True,
            write_jsonl=True,
        )
        warnings.extend(table_result.get("warnings", []))

        # Profile rows contain nested dict/list fields (notably plant_ratios). A
        # heterogeneous dict column is encoded by Arrow as a struct whose field
        # set is the union across all rows. On readback this materializes absent
        # plant keys as ``None`` (e.g. clove=None for lemongrass), which is not
        # equivalent to the original mapping and violates PeakRecord's
        # dict[str, float] contract. JSONL preserves row-local mappings exactly,
        # so it is the canonical round-trip representation. Parquet/CSV remain
        # materialized analytical exports.
        jsonl_path = next(
            (file for file in table_result.get("files", []) if str(file).endswith(".jsonl")),
            None,
        )
        primary_path = jsonl_path or table_result["primary"]
        manifest = {
            "schema_version": "project_blends_profile_dataset.v1",
            "dataset_id": request.dataset_id,
            "source_document": request.source_document,
            "created_at_utc": utc_now_iso(),
            "rows": len(records),
            "samples": sorted({record.sample_id for record in request.records}),
            "files": table_result.get("files", []),
            "primary_path": primary_path,
            "primary_sha256": sha256_file(Path(primary_path)),
            "canonical_format": "jsonl" if str(primary_path).endswith(".jsonl") else Path(primary_path).suffix.lstrip("."),
            "warnings": warnings,
        }
        write_json_atomic(dataset_dir / "manifest.json", manifest)
        return ProfileIngestResponse(
            ok=True,
            dataset_id=request.dataset_id,
            rows=len(records),
            samples=manifest["samples"],
            artifact_path=primary_path,
            warnings=warnings,
        )

    @staticmethod
    def _clean_row(row: dict[str, Any]) -> dict[str, Any]:
        """Repair representation-only nulls introduced by tabular formats.

        This intentionally targets ``plant_ratios`` only. A missing plant key
        means the plant is not a component of that sample; it must not be
        represented as ``plant=None``. Other nullable fields retain their nulls
        because those may be scientifically meaningful unknowns.
        """
        ratios = row.get("plant_ratios")
        if isinstance(ratios, dict):
            row["plant_ratios"] = {str(key): value for key, value in ratios.items() if value is not None}
        return row

    def load(self, dataset_id: str) -> list[PeakRecord]:
        dataset_dir = self.root / dataset_id
        manifest_path = dataset_dir / "manifest.json"
        if not manifest_path.exists():
            raise FileNotFoundError(f"Profile dataset not found: {dataset_id}")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

        # Prefer JSONL whenever present, including datasets seeded by v0.1.0
        # whose manifest may still point at Parquet. This makes the fix backward
        # compatible without forcing a reseed.
        files = [Path(file) for file in manifest.get("files", [])]
        jsonl_path = next((path for path in files if path.suffix.lower() == ".jsonl" and path.exists()), None)
        primary_path = jsonl_path or Path(manifest["primary_path"])

        df = read_table(primary_path)
        rows = df.astype(object).where(df.notna(), None).to_dict(orient="records")
        return [PeakRecord.model_validate(self._clean_row(row)) for row in rows]

    def list_datasets(self) -> list[dict]:
        out: list[dict] = []
        for manifest_path in sorted(self.root.glob("*/manifest.json")):
            out.append(json.loads(manifest_path.read_text(encoding="utf-8")))
        return out
