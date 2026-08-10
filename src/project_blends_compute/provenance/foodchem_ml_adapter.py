from __future__ import annotations

from typing import Any

import httpx

from project_blends_compute.schemas.provenance import ExploratoryLinkPrediction
from project_blends_compute.settings import Settings


class FoodChemMLAdapter:
    """Optional hypothesis-only client for foodchem_ml GraphSAGE link prediction."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    @property
    def available(self) -> bool:
        return bool(self.settings.foodchem_ml_base_url)

    async def predict(self, *, sample_id: str, ingredient_name: str, food_id: int, top_k: int) -> list[ExploratoryLinkPrediction]:
        if not self.settings.foodchem_ml_base_url:
            return []
        headers: dict[str, str] = {}
        if self.settings.foodchem_ml_api_key:
            headers["X-API-Key"] = self.settings.foodchem_ml_api_key
        url = self.settings.foodchem_ml_base_url.rstrip("/") + "/api/v1/link-predictions"
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                url,
                json={"food_id": food_id, "top_k": top_k, "exclude_known": True, "minimum_probability": 0.0},
                headers=headers,
            )
            response.raise_for_status()
            payload: dict[str, Any] = response.json()
        results: list[ExploratoryLinkPrediction] = []
        for candidate in payload.get("candidates", []):
            compound = candidate.get("compound") or {}
            results.append(
                ExploratoryLinkPrediction(
                    sample_id=sample_id,
                    ingredient_name=ingredient_name,
                    compound_name=compound.get("name"),
                    compound_id=compound.get("id"),
                    probability=float(candidate.get("probability", 0.0)),
                    rank=int(candidate.get("rank", len(results) + 1)),
                    raw=candidate,
                )
            )
        return results
