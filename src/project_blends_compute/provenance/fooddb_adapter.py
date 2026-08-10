from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

import pandas as pd

from project_blends_compute.artifacts.formats import read_table
from project_blends_compute.schemas.common import ProvenanceRecord
from project_blends_compute.schemas.provenance import FoodOccurrenceEvidence, ProvenanceQuery
from project_blends_compute.settings import Settings
from project_blends_compute.utils import normalize_name


INGREDIENT_ALIASES: dict[str, tuple[str, ...]] = {
    "clove": ("clove", "cloves", "syzygium aromaticum", "syzgium aromaticum"),
    "lemongrass": ("lemongrass", "lemon grass", "cymbopogon citratus"),
    "scent leaf": ("scent leaf", "clove basil", "african basil", "ocimum gratissimum"),
    "ginger": ("ginger", "zingiber officinale", "zingiber officinale roscoe"),
}


class FoodDBAdapter:
    """Deterministic reader for pipeline_fooddb curated artifacts.

    This adapter treats documented FoodDB edges as occurrence evidence. It never
    promotes FoodChem ML link predictions into this evidence lane.
    """

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._foods: pd.DataFrame | None = None
        self._compounds: pd.DataFrame | None = None
        self._edges: pd.DataFrame | None = None
        self._load_error: str | None = None

    @property
    def available(self) -> bool:
        try:
            self._ensure_loaded()
            return self._foods is not None and self._compounds is not None and self._edges is not None
        except Exception as exc:
            self._load_error = repr(exc)
            return False

    @property
    def load_error(self) -> str | None:
        return self._load_error

    def _ensure_loaded(self) -> None:
        if self._foods is not None and self._compounds is not None and self._edges is not None:
            return
        if self.settings.fooddb_db_path and self.settings.fooddb_db_path.exists() and importlib.util.find_spec("duckdb"):
            self._load_duckdb(self.settings.fooddb_db_path)
            return
        required = [self.settings.fooddb_food_lookup_path, self.settings.fooddb_compound_lookup_path, self.settings.fooddb_edges_path]
        if not all(path and path.exists() for path in required):
            raise FileNotFoundError("FoodDB curated artifacts are not fully configured")
        self._foods = read_table(self.settings.fooddb_food_lookup_path)  # type: ignore[arg-type]
        self._compounds = read_table(self.settings.fooddb_compound_lookup_path)  # type: ignore[arg-type]
        self._edges = read_table(self.settings.fooddb_edges_path)  # type: ignore[arg-type]
        self._normalize_tables()

    def _load_duckdb(self, db_path: Path) -> None:
        import duckdb  # type: ignore

        connection = duckdb.connect(str(db_path), read_only=True)
        try:
            self._foods = connection.execute("SELECT * FROM curated_food_lookup").fetch_df()
            self._compounds = connection.execute("SELECT * FROM curated_compound_lookup").fetch_df()
            edge_view = "curated_food_compound_content"
            self._edges = connection.execute(f"SELECT * FROM {edge_view}").fetch_df()
        finally:
            connection.close()
        self._normalize_tables()

    def _normalize_tables(self) -> None:
        assert self._foods is not None and self._compounds is not None and self._edges is not None
        foods = self._foods.copy()
        compounds = self._compounds.copy()
        edges = self._edges.copy()
        foods.columns = [str(column).lower() for column in foods.columns]
        compounds.columns = [str(column).lower() for column in compounds.columns]
        edges.columns = [str(column).lower() for column in edges.columns]
        foods["food_id"] = pd.to_numeric(foods.get("food_id", foods.get("id")), errors="coerce").astype("Int64")
        compounds["compound_id"] = pd.to_numeric(compounds.get("compound_id", compounds.get("id")), errors="coerce").astype("Int64")
        edges["food_id"] = pd.to_numeric(edges.get("food_id"), errors="coerce").astype("Int64")
        edges["compound_id"] = pd.to_numeric(edges.get("compound_id", edges.get("source_id")), errors="coerce").astype("Int64")
        foods["_name_norm"] = foods.get("name", pd.Series(index=foods.index, dtype=object)).fillna("").map(normalize_name)
        foods["_scientific_norm"] = foods.get("name_scientific", pd.Series(index=foods.index, dtype=object)).fillna("").map(normalize_name)
        compounds["_name_norm"] = compounds.get("name", pd.Series(index=compounds.index, dtype=object)).fillna("").map(normalize_name)
        if "inchikey" in compounds.columns:
            compounds["_inchikey_norm"] = compounds["inchikey"].fillna("").astype(str).str.upper().str.strip()
        else:
            compounds["_inchikey_norm"] = ""
        if "cas_number" in compounds.columns:
            compounds["_cas_norm"] = compounds["cas_number"].fillna("").astype(str).str.strip()
        else:
            compounds["_cas_norm"] = ""
        self._foods, self._compounds, self._edges = foods, compounds, edges

    def evaluate(self, query: ProvenanceQuery) -> list[FoodOccurrenceEvidence]:
        self._ensure_loaded()
        assert self._foods is not None and self._compounds is not None and self._edges is not None
        food_matches = self._find_foods(query.ingredient_names)
        compound_matches = self._find_compounds(query)
        if food_matches.empty or compound_matches.empty:
            return []
        food_ids = set(int(value) for value in food_matches["food_id"].dropna().tolist())
        compound_ids = set(int(value) for value in compound_matches["compound_id"].dropna().tolist())
        matching_edges = self._edges.loc[
            self._edges["food_id"].isin(food_ids) & self._edges["compound_id"].isin(compound_ids)
        ].copy()
        results: list[FoodOccurrenceEvidence] = []
        for edge in matching_edges.to_dict(orient="records"):
            food_row = food_matches.loc[food_matches["food_id"] == edge["food_id"]].iloc[0].to_dict()
            compound_row = compound_matches.loc[compound_matches["compound_id"] == edge["compound_id"]].iloc[0].to_dict()
            match_basis = compound_row.get("_match_basis", "compound_name") + "+" + food_row.get("_match_basis", "food_name")
            confidence = 0.98 if "inchikey" in match_basis else 0.90 if "scientific" in match_basis else 0.82
            results.append(
                FoodOccurrenceEvidence(
                    sample_id=query.sample_id,
                    ingredient_name=food_row.get("_query_ingredient") or food_row.get("name") or "",
                    food_id=_safe_int(edge.get("food_id")),
                    food_name=_safe_str(food_row.get("name")),
                    food_scientific_name=_safe_str(food_row.get("name_scientific")),
                    compound_id=_safe_int(edge.get("compound_id")),
                    compound_name=_safe_str(compound_row.get("name")),
                    match_basis=match_basis,
                    documented_occurrence=True,
                    standard_content=_safe_float(edge.get("standard_content")),
                    original_content=_safe_float(edge.get("orig_content")),
                    original_unit=_safe_str(edge.get("orig_unit")),
                    preparation_type=_safe_str(edge.get("preparation_type")),
                    citation=_safe_str(edge.get("citation")),
                    citation_type=_safe_str(edge.get("citation_type")),
                    confidence=confidence,
                    provenance=[
                        ProvenanceRecord(
                            source="pipeline_fooddb",
                            source_id=f"food:{edge.get('food_id')}:compound:{edge.get('compound_id')}",
                            evidence_type="documented_food_compound_occurrence",
                            source_quality="curated_database",
                            metadata={"edge_row": {k: _json_scalar(v) for k, v in edge.items()}},
                        )
                    ],
                )
            )
        return results

    def _find_foods(self, ingredient_names: list[str]) -> pd.DataFrame:
        assert self._foods is not None
        frames: list[pd.DataFrame] = []
        for ingredient in ingredient_names:
            normalized = normalize_name(ingredient)
            aliases = {normalized}
            for canonical, values in INGREDIENT_ALIASES.items():
                if normalized == canonical or normalized in {normalize_name(value) for value in values}:
                    aliases.update(normalize_name(value) for value in values)
            exact_scientific = self._foods["_scientific_norm"].isin(aliases)
            exact_name = self._foods["_name_norm"].isin(aliases)
            subset = self._foods.loc[exact_scientific | exact_name].copy()
            if subset.empty:
                subset = self._foods.loc[
                    self._foods["_name_norm"].map(lambda value: any(alias and (alias in value or value in alias) for alias in aliases))
                    | self._foods["_scientific_norm"].map(lambda value: any(alias and (alias in value or value in alias) for alias in aliases))
                ].copy()
            if not subset.empty:
                subset["_query_ingredient"] = ingredient
                subset["_match_basis"] = subset.apply(
                    lambda row: "food_scientific_name" if row["_scientific_norm"] in aliases else "food_name",
                    axis=1,
                )
                frames.append(subset)
        if not frames:
            return self._foods.iloc[0:0].copy()
        return pd.concat(frames, ignore_index=True).drop_duplicates(subset=["food_id"])

    def _find_compounds(self, query: ProvenanceQuery) -> pd.DataFrame:
        assert self._compounds is not None
        if query.inchikey:
            subset = self._compounds.loc[self._compounds["_inchikey_norm"] == query.inchikey.upper().strip()].copy()
            if not subset.empty:
                subset["_match_basis"] = "compound_inchikey"
                return subset
        if query.cas_number:
            subset = self._compounds.loc[self._compounds["_cas_norm"] == query.cas_number.strip()].copy()
            if not subset.empty:
                subset["_match_basis"] = "compound_cas"
                return subset
        normalized = normalize_name(query.compound_name)
        subset = self._compounds.loc[self._compounds["_name_norm"] == normalized].copy()
        if subset.empty:
            subset = self._compounds.loc[
                self._compounds["_name_norm"].map(lambda value: normalized and (normalized in value or value in normalized))
            ].copy()
        if not subset.empty:
            subset["_match_basis"] = "compound_name"
        return subset


def _safe_str(value: Any) -> str | None:
    if value is None or pd.isna(value):
        return None
    text = str(value).strip()
    return text or None


def _safe_float(value: Any) -> float | None:
    try:
        if value is None or pd.isna(value):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _safe_int(value: Any) -> int | None:
    try:
        if value is None or pd.isna(value):
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _json_scalar(value: Any) -> Any:
    if value is None or pd.isna(value):
        return None
    if hasattr(value, "item"):
        try:
            return value.item()
        except Exception:
            pass
    return value
