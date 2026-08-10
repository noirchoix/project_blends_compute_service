from __future__ import annotations

from project_blends_compute.provenance.foodchem_ml_adapter import FoodChemMLAdapter
from project_blends_compute.provenance.fooddb_adapter import FoodDBAdapter
from project_blends_compute.schemas.provenance import ProvenanceEvaluateRequest, ProvenanceEvaluateResponse


class ProvenanceService:
    def __init__(self, fooddb: FoodDBAdapter, foodchem_ml: FoodChemMLAdapter) -> None:
        self.fooddb = fooddb
        self.foodchem_ml = foodchem_ml

    async def evaluate(self, request: ProvenanceEvaluateRequest) -> ProvenanceEvaluateResponse:
        occurrences = []
        unresolved = []
        lane_status = {
            "pipeline_fooddb": "available" if self.fooddb.available else "unavailable",
            "foodchem_ml": "available" if self.foodchem_ml.available else "disabled",
        }
        if self.fooddb.available:
            for query in request.queries:
                matches = self.fooddb.evaluate(query)
                occurrences.extend(matches)
                if not matches:
                    unresolved.append(query.model_dump(mode="json"))
        else:
            unresolved.extend(query.model_dump(mode="json") for query in request.queries)
        exploratory = []
        # FoodChem ML requires a FoodDB food_id. Only invoke it for matched foods,
        # and never mix its predictions into documented occurrence evidence.
        if request.include_exploratory_predictions and self.foodchem_ml.available:
            seen: set[tuple[str, int]] = set()
            for occurrence in occurrences:
                if occurrence.food_id is None:
                    continue
                key = (occurrence.sample_id, occurrence.food_id)
                if key in seen:
                    continue
                seen.add(key)
                exploratory.extend(
                    await self.foodchem_ml.predict(
                        sample_id=occurrence.sample_id,
                        ingredient_name=occurrence.ingredient_name,
                        food_id=occurrence.food_id,
                        top_k=request.exploratory_top_k,
                    )
                )
        return ProvenanceEvaluateResponse(
            ok=True,
            occurrences=occurrences,
            exploratory_predictions=exploratory,
            unresolved_queries=unresolved,
            lane_status=lane_status,
        )
