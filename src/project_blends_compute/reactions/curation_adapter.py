from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pandas as pd

from project_blends_compute.artifacts.formats import read_table
from project_blends_compute.reactions.chemistry import match_score
from project_blends_compute.schemas.common import ProvenanceRecord
from project_blends_compute.schemas.reactions import CuratedReactionEvidence, ReactionCandidate
from project_blends_compute.settings import Settings
from project_blends_compute.utils import normalize_name


class ReactionCurationAdapter:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.reactions: pd.DataFrame | None = None
        self.conditions: pd.DataFrame | None = None
        self.condition_store: Any = None
        self.error: str | None = None
        self._load()

    def _load(self) -> None:
        registry_path = self.settings.reaction_curation_registry
        if registry_path is None or not registry_path.exists():
            self.error = "reaction_curation_registry_not_configured"
            return
        try:
            registry = json.loads(registry_path.read_text(encoding="utf-8"))
            block = registry.get(self.settings.reaction_curation_dataset)
            if not isinstance(block, dict):
                self.error = f"dataset_not_registered:{self.settings.reaction_curation_dataset}"
                return
            active = block.get("active_version")
            entry = (block.get("versions") or {}).get(active)
            if not isinstance(entry, dict):
                self.error = "malformed_reaction_curation_registry_entry"
                return
            reactions_path = self._resolve_path(registry_path, entry.get("reactions_path"))
            conditions_path = self._resolve_path(registry_path, entry.get("conditions_path"))
            self.reactions = read_table(reactions_path)
            self.reactions.columns = [str(column) for column in self.reactions.columns]
            if conditions_path and conditions_path.exists():
                self.conditions = read_table(conditions_path)
            self._try_condition_store(registry_path)
        except Exception as exc:
            self.error = f"reaction_curation_load_failed:{type(exc).__name__}:{exc}"

    def _try_condition_store(self, registry_path: Path) -> None:
        root = self.settings.rxn_bridge_project_root
        if root is None or not root.exists():
            return
        for path in (root, root / "pipelines"):
            text = str(path.resolve())
            if text not in sys.path:
                sys.path.insert(0, text)
        try:
            from reaction_framework.providers.curation_condition_store import ConditionStore, ConditionStoreConfig

            self.condition_store = ConditionStore(
                ConditionStoreConfig(
                    registry_path=registry_path,
                    dataset_names=(self.settings.reaction_curation_dataset,),
                    require_existing_files=True,
                    resolve_relative_to_registry=True,
                )
            )
            self.condition_store.load()
        except Exception:
            self.condition_store = None

    @staticmethod
    def _resolve_path(registry_path: Path, raw: Any) -> Path:
        if not isinstance(raw, str) or not raw.strip():
            return Path("__missing__")
        path = Path(raw)
        return path if path.is_absolute() else (registry_path.parent / path).resolve()

    @property
    def available(self) -> bool:
        return self.reactions is not None

    def evaluate(self, candidate: ReactionCandidate, top_k: int = 20) -> list[CuratedReactionEvidence]:
        if self.reactions is None:
            return []
        scored: list[tuple[float, dict[str, Any], float, float]] = []
        for row in self.reactions.where(self.reactions.notna(), None).to_dict(orient="records"):
            precursor_smiles = row.get("precursor_smiles") or row.get("reactants_smiles")
            product_smiles = row.get("product_smiles") or row.get("products_smiles")
            precursor_name = normalize_name(str(row.get("precursor_name") or ""))
            product_name = normalize_name(str(row.get("product_name") or ""))
            p_score = match_score(
                candidate.precursor_smiles,
                precursor_smiles,
                normalize_name(candidate.precursor_name) == precursor_name and bool(precursor_name),
            )
            q_score = match_score(
                candidate.product_smiles,
                product_smiles,
                normalize_name(candidate.product_name) == product_name and bool(product_name),
            )
            family = normalize_name(str(row.get("transformation_family") or ""))
            family_bonus = 0.10 if family and family == normalize_name(candidate.transformation_family) else 0.0
            score = 0.45 * p_score + 0.45 * q_score + family_bonus
            if score >= 0.45:
                scored.append((score, row, p_score, q_score))
        scored.sort(key=lambda item: item[0], reverse=True)
        results: list[CuratedReactionEvidence] = []
        for score, row, p_score, q_score in scored[:top_k]:
            reaction_id = str(row.get("reaction_id") or row.get("id") or row.get("source_id") or "")
            conditions = self._conditions_for_reaction(reaction_id)
            results.append(
                CuratedReactionEvidence(
                    reaction_id=reaction_id,
                    source_id=_safe_str(row.get("source_id")),
                    source_doi=_safe_str(row.get("source_doi")),
                    transformation_family=_safe_str(row.get("transformation_family")),
                    evidence_directness=_safe_str(row.get("evidence_directness")),
                    precursor_match=p_score,
                    product_match=q_score,
                    conditions=conditions,
                    provenance=[
                        ProvenanceRecord(
                            source="reaction_curation",
                            source_id=reaction_id,
                            source_uri=_safe_str(row.get("source_doi")),
                            evidence_type="curated_storage_reaction",
                            source_quality=_safe_str(row.get("source_type")) or "curated_primary_or_secondary",
                            metadata={"match_score": score},
                        )
                    ],
                    raw={key: _json_scalar(value) for key, value in row.items()},
                )
            )
        return results

    def _conditions_for_reaction(self, reaction_id: str) -> list[dict[str, Any]]:
        if not reaction_id:
            return []
        if self.condition_store is not None:
            try:
                # Reaction-specific context is deliberately primary.
                return self.condition_store.get_for_reaction(self.settings.reaction_curation_dataset, reaction_id)
            except Exception:
                pass
        if self.conditions is None or "reaction_id" not in self.conditions.columns:
            return []
        subset = self.conditions.loc[self.conditions["reaction_id"].astype(str) == reaction_id]
        return [
            {key: _json_scalar(value) for key, value in row.items()}
            for row in subset.where(subset.notna(), None).to_dict(orient="records")
        ]


def _safe_str(value: Any) -> str | None:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    text = str(value).strip()
    return text or None


def _json_scalar(value: Any) -> Any:
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except Exception:
        pass
    if hasattr(value, "item"):
        try:
            return value.item()
        except Exception:
            pass
    return value
