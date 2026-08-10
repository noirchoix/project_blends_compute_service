from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any, Iterable

import pandas as pd

from project_blends_compute.utils import jsonable, write_json_atomic


class TableWriteResult(dict):
    """Dictionary payload describing the materialized table and any fallback."""


def write_records_table(
    records: Iterable[dict[str, Any]],
    path_stem: Path,
    *,
    prefer_parquet: bool = True,
    write_csv: bool = True,
    write_jsonl: bool = False,
) -> TableWriteResult:
    rows = [jsonable(row) for row in records]
    df = pd.DataFrame(rows)
    result: TableWriteResult = TableWriteResult(rows=len(df), files=[], warnings=[])
    path_stem.parent.mkdir(parents=True, exist_ok=True)

    if prefer_parquet:
        parquet_path = path_stem.with_suffix(".parquet")
        try:
            df.to_parquet(parquet_path, index=False)
            result["files"].append(str(parquet_path))
            result["primary"] = str(parquet_path)
            result["format"] = "parquet"
        except Exception as exc:
            result["warnings"].append(f"Parquet unavailable; wrote interoperable fallback: {type(exc).__name__}: {exc}")

    if write_csv or "primary" not in result:
        csv_path = path_stem.with_suffix(".csv")
        df.to_csv(csv_path, index=False, quoting=csv.QUOTE_MINIMAL)
        result["files"].append(str(csv_path))
        if "primary" not in result:
            result["primary"] = str(csv_path)
            result["format"] = "csv"

    if write_jsonl:
        jsonl_path = path_stem.with_suffix(".jsonl")
        with jsonl_path.open("w", encoding="utf-8") as handle:
            for row in rows:
                handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
        result["files"].append(str(jsonl_path))

    schema_path = path_stem.with_name(path_stem.name + ".schema.json")
    schema = {
        "columns": [{"name": str(col), "dtype": str(dtype)} for col, dtype in df.dtypes.items()],
        "rows": len(df),
    }
    write_json_atomic(schema_path, schema)
    result["files"].append(str(schema_path))
    return result


def read_table(path: Path) -> pd.DataFrame:
    if not path.exists():
        alternatives = [path.with_suffix(".parquet"), path.with_suffix(".csv"), path.with_suffix(".jsonl"), path.with_suffix(".pkl")]
        path = next((candidate for candidate in alternatives if candidate.exists()), path)
    if not path.exists():
        raise FileNotFoundError(path)
    suffix = path.suffix.lower()
    if suffix == ".parquet":
        return pd.read_parquet(path)
    if suffix == ".csv":
        return pd.read_csv(path)
    if suffix == ".jsonl":
        return pd.read_json(path, lines=True)
    if suffix in {".pkl", ".pickle"}:
        return pd.read_pickle(path)
    if suffix == ".json":
        payload = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(payload, list):
            return pd.DataFrame(payload)
        if isinstance(payload, dict) and isinstance(payload.get("records"), list):
            return pd.DataFrame(payload["records"])
    raise ValueError(f"Unsupported table format: {path}")
