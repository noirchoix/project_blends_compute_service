#!/usr/bin/env python
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from docx import Document


TABLE_CONFIG = {
    2: {
        "sample_id": "lemongrass",
        "blend_id": "lemongrass_control",
        "plant_components": ["Cymbopogon citratus"],
        "plant_ratios": {"Cymbopogon citratus": 30.0},
        "source_table": "Table 4.1",
    },
    3: {
        "sample_id": "blend_a",
        "blend_id": "A",
        "plant_components": ["Cymbopogon citratus", "Ocimum gratissimum"],
        "plant_ratios": {"Cymbopogon citratus": 15.0, "Ocimum gratissimum": 15.0},
        "source_table": "Table 4.2",
    },
    4: {
        "sample_id": "blend_b",
        "blend_id": "B",
        "plant_components": ["Cymbopogon citratus", "Ocimum gratissimum", "Syzygium aromaticum", "Zingiber officinale"],
        "plant_ratios": {"Cymbopogon citratus": 7.5, "Ocimum gratissimum": 7.5, "Syzygium aromaticum": 7.5, "Zingiber officinale": 7.5},
        "source_table": "Table 4.3",
    },
    5: {
        "sample_id": "blend_c",
        "blend_id": "C",
        "plant_components": ["Cymbopogon citratus", "Ocimum gratissimum", "Syzygium aromaticum"],
        "plant_ratios": {"Cymbopogon citratus": 10.0, "Ocimum gratissimum": 10.0, "Syzygium aromaticum": 10.0},
        "source_table": "Table 4.4",
    },
}

# These are candidate-name sets explicitly represented in the source table cells.
AMBIGUOUS_ROWS = {
    (3, 1): ["o-Cymene", "p-Cymene"],
    (3, 2): ["p-Cymene", "o-Cymene"],
    (3, 6): ["Thymol", "Phenol, 2-methyl-5-(1-methylethyl)"],
    (4, 1): ["p-Cymene", "Benzene, 1-methyl-3-(1-methylethyl)-", "o-Cymene"],
    (4, 6): ["Phenol, 2-methoxy-3-(2-propenyl)-", "Eugenol"],
    (4, 7): ["Phenol, 2-methoxy-3-(2-propenyl)-", "Eugenol"],
    (4, 14): ["trans-alpha-Bergamotene", "1,3-Cyclohexadiene, 5-(1,5-dimethyl-4-hexenyl)-2-methyl-, [S-(R*,S*)]-"],
    (5, 6): ["Phenol, 2-methoxy-3-(2-propenyl)-", "Eugenol"],
    (5, 17): ["Phenol, 2-methyl-5-(1-methylethyl)", "Thymol"],
}


def clean(value: str) -> str:
    value = value.replace("\u00a0", " ").replace("\u200b", " ")
    value = re.sub(r"\s+", " ", value).strip(" |\t\r\n")
    value = value.replace(".gamma.", "gamma-").replace(".gamma", "gamma-").replace(".alpha.", "alpha-").replace(".beta.", "beta-")
    return value


def maybe_docx(source: Path) -> tuple[Path, Path | None]:
    if source.suffix.lower() == ".docx":
        return source, None
    if source.suffix.lower() != ".doc":
        raise ValueError("Source must be .doc or .docx")
    libreoffice = shutil.which("libreoffice") or shutil.which("soffice")
    if not libreoffice:
        raise RuntimeError("LibreOffice is required to convert the legacy .doc source")
    tmp = Path(tempfile.mkdtemp(prefix="project-blends-doc-"))
    subprocess.run([libreoffice, "--headless", "--convert-to", "docx", "--outdir", str(tmp), str(source)], check=True, capture_output=True)
    target = tmp / (source.stem + ".docx")
    if not target.exists():
        raise RuntimeError("Legacy document conversion did not produce a .docx file")
    return target, tmp


def parse_number(value: str) -> float | None:
    value = clean(value)
    if not value:
        return None
    match = re.search(r"-?\d+(?:\.\d+)?", value)
    return float(match.group(0)) if match else None


def extract(source: Path) -> tuple[list[dict], dict]:
    docx, _ = maybe_docx(source)
    document = Document(docx)
    records: list[dict] = []
    for table_index, config in TABLE_CONFIG.items():
        table = document.tables[table_index]
        previous_record_index: int | None = None
        for row_index, row in enumerate(table.rows[1:], start=1):
            cells = [clean(cell.text) for cell in row.cells]
            serial = cells[0]
            # Source Table 4.3 has a blank continuation row adding Eugenol as a second candidate identity.
            if table_index == 4 and row_index == 8 and not serial and previous_record_index is not None:
                prior = records[previous_record_index]
                prior["candidate_names"] = sorted(set(prior.get("candidate_names", []) + [cells[2]]))
                prior["metadata"].setdefault("continuation_rows", []).append(f"{config['source_table']} row {row_index}")
                continue
            before = parse_number(cells[3])
            after = parse_number(cells[4])
            if before is None and after is None:
                continue
            candidate_names = AMBIGUOUS_ROWS.get((table_index, row_index), [])
            reported_name = candidate_names[0] if candidate_names else clean(cells[2])
            quality = parse_number(cells[5])
            base = {
                "sample_id": config["sample_id"],
                "blend_id": config["blend_id"],
                "plant_components": config["plant_components"],
                "plant_ratios": config["plant_ratios"],
                "reported_compound_name": reported_name,
                "candidate_names": candidate_names,
                "retention_time_min": parse_number(cells[1]),
                "library_match_quality": quality,
                "reported_smiles": clean(cells[6]) or None,
                "reported_class": clean(cells[7]) if len(cells) > 7 else None,
                "source_table": config["source_table"],
                "source_row": f"{config['source_table']} row {row_index}",
                "metadata": {
                    "reported_name_verbatim": clean(cells[2]),
                    "reported_quality_verbatim": clean(cells[5]),
                    "reported_smiles_verbatim": clean(cells[6]),
                    "identity_status": "tentative_library_annotation",
                    "source_preserved_without_silent_correction": True,
                },
            }
            if before is not None:
                record = {**base, "timepoint": "before_storage", "storage_days": 0, "area_percent": before}
                record["peak_id"] = f"project_blends_reported_v1:{config['sample_id']}:before:{row_index:03d}"
                records.append(record)
                previous_record_index = len(records) - 1
            if after is not None:
                record = {**base, "timepoint": "after_storage", "storage_days": 28, "area_percent": after}
                record["peak_id"] = f"project_blends_reported_v1:{config['sample_id']}:after:{row_index:03d}"
                records.append(record)
                previous_record_index = len(records) - 1
    metadata = {
        "schema_version": "project_blends_experiment_metadata.v1",
        "study_title": "Effect of storage on levels of volatile compounds in n-hexane extracts from selected medicinal plant blends",
        "timepoints": ["before_storage", "after_storage_28_days"],
        "storage": {"duration_days": 28, "container": "airtight", "temperature_c": None, "light_exposure": None, "headspace": None, "oxygen_exposure": None},
        "analytical_basis": "GC-MS NIST 14 library comparison and reported relative peak-area percentages",
        "documented_replicates": None,
        "absolute_quantitation": False,
        "raw_spectra_available_in_source_document": False,
        "claim_boundary": "profile observations do not independently establish chemical transformations",
        "record_count": len(records),
    }
    return records, metadata


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("--output", type=Path, default=Path("data/raw/project_blends_reported_v1.json"))
    parser.add_argument("--metadata-output", type=Path, default=Path("data/raw/project_blends_experiment_metadata.json"))
    args = parser.parse_args()
    records, metadata = extract(args.source)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(records, indent=2, ensure_ascii=False), encoding="utf-8")
    args.metadata_output.parent.mkdir(parents=True, exist_ok=True)
    args.metadata_output.write_text(json.dumps(metadata, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({"ok": True, "records": len(records), "output": str(args.output), "metadata": str(args.metadata_output)}, indent=2))


if __name__ == "__main__":
    main()
