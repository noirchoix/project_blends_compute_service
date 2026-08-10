from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="PB_", env_file=".env", extra="ignore")

    service_name: str = "project-blends-compute"
    environment: str = "development"
    api_key: str | None = None
    project_root: Path = Field(default_factory=lambda: Path.cwd())
    artifact_root: Path = Field(default_factory=lambda: Path("artifacts"))
    state_root: Path = Field(default_factory=lambda: Path("state"))
    path_manifest: Path | None = None

    project_blends_source_document: Path | None = None
    raw_gc_ms_export_directory: Path | None = None
    raw_chromatogram_directory: Path | None = None
    nist_library_match_export_directory: Path | None = None
    existing_peak_tables_path: Path | None = None

    pubchem_base_url: str = "https://pubchem.ncbi.nlm.nih.gov/rest/pug"
    chebi_base_url: str = "https://www.ebi.ac.uk/chebi/backend/api/public"
    identity_online_enabled: bool = False
    identity_timeout_s: float = 20.0
    identity_max_candidates: int = 8

    reaction_curation_dataset: str = "storage_reaction_evidence"
    reaction_curation_project_root: Path | None = None
    reaction_curation_registry: Path | None = None
    rxn_bridge_project_root: Path | None = None
    rxn_artifact_registry: Path | None = None
    rxn_template_artifact_root: Path | None = None

    fooddb_db_path: Path | None = None
    fooddb_food_lookup_path: Path | None = None
    fooddb_compound_lookup_path: Path | None = None
    fooddb_edges_path: Path | None = None
    foodchem_ml_base_url: str | None = None
    foodchem_ml_api_key: str | None = None

    dess_artifact_root: Path | None = None
    taxonomy_artifact_root: Path | None = None
    # Deprecated v0.1.5 compatibility fields. Project Blends v0.1.6 has no
    # standalone COCO classifier lane; COCONUT taxonomy is the authoritative
    # supporting model and is resolved through rxn_artifact_registry.
    coco_model_artifact_root: Path | None = None
    coco_classifier_project_root: Path | None = None
    coco_classifier_base_url: str | None = None
    coco_classifier_timeout_s: float = 30.0

    rxnutils_source_root: Path | None = None
    rxnmapper_python: str | None = None
    xtb_executable: Path | None = None
    orca_executable: Path | None = None
    psi4_executable: Path | None = None

    quantum_default_engine: str = "xtb"
    quantum_worker_poll_s: float = 1.0
    quantum_stale_after_minutes: int = 60
    max_reaction_candidates: int = 200
    strict_ready_lanes: tuple[str, ...] = ()

    @field_validator("artifact_root", "state_root", mode="after")
    @classmethod
    def _resolve_runtime_paths(cls, value: Path, info: Any) -> Path:
        project_root = info.data.get("project_root", Path.cwd())
        return value if value.is_absolute() else (project_root / value).resolve()

    def ensure_runtime_dirs(self) -> None:
        self.artifact_root.mkdir(parents=True, exist_ok=True)
        self.state_root.mkdir(parents=True, exist_ok=True)

    def load_path_manifest(self) -> dict[str, Any]:
        if self.path_manifest is None:
            return {}
        path = self.path_manifest if self.path_manifest.is_absolute() else (self.project_root / self.path_manifest)
        if not path.exists():
            return {}
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}

    def effective_paths(self) -> dict[str, str | None]:
        keys = [
            "project_blends_source_document",
            "raw_gc_ms_export_directory",
            "raw_chromatogram_directory",
            "nist_library_match_export_directory",
            "existing_peak_tables_path",
            "reaction_curation_project_root",
            "reaction_curation_registry",
            "rxn_bridge_project_root",
            "rxn_artifact_registry",
            "rxn_template_artifact_root",
            "fooddb_db_path",
            "fooddb_food_lookup_path",
            "fooddb_compound_lookup_path",
            "fooddb_edges_path",
            "dess_artifact_root",
            "taxonomy_artifact_root",
            "rxnutils_source_root",
            "xtb_executable",
            "orca_executable",
            "psi4_executable",
        ]
        return {key: str(getattr(self, key)) if getattr(self, key) is not None else None for key in keys}


settings = Settings()


def hydrate_paths_from_manifest(config: Settings) -> Settings:
    """Apply flat or sectioned local path-manifest values without guessing paths."""
    payload = config.load_path_manifest()
    if not payload:
        return config
    flattened: dict[str, Any] = {}
    for key, value in payload.items():
        if isinstance(value, dict):
            flattened.update(value)
        else:
            flattened[key] = value
    for key in config.effective_paths():
        raw = flattened.get(key)
        if raw not in (None, ""):
            setattr(config, key, Path(str(raw)).expanduser())
    scalar_keys = {
        "foodchem_ml_base_url": str,
    }
    for key, caster in scalar_keys.items():
        raw = flattened.get(key)
        if raw not in (None, ""):
            setattr(config, key, caster(raw))
    return config
