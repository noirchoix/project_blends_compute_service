from __future__ import annotations

from project_blends_compute.profiles.compositional import analyse_profiles, stability_ranking
from project_blends_compute.profiles.repository import ProfileRepository
from project_blends_compute.schemas.profiles import ProfileAnalysisRequest, ProfileAnalysisResponse


class ProfileService:
    def __init__(self, repository: ProfileRepository) -> None:
        self.repository = repository

    def analyse(self, request: ProfileAnalysisRequest) -> ProfileAnalysisResponse:
        if request.records is not None:
            records = request.records
        elif request.dataset_id:
            records = self.repository.load(request.dataset_id)
        else:
            raise ValueError("Provide records or dataset_id")
        metrics = analyse_profiles(
            records,
            identity_key=request.identity_key,
            zero_replacement=request.zero_replacement,
            confidence_weighted=request.confidence_weighted,
        )
        return ProfileAnalysisResponse(
            ok=True,
            metrics=metrics,
            ranking=stability_ranking(metrics),
            method={
                "data_type": "relative_peak_area_compositional_data",
                "metrics": ["weighted_jaccard", "bray_curtis", "jensen_shannon", "aitchison", "class_weighted_jaccard"],
                "zero_replacement": request.zero_replacement,
                "identity_key": request.identity_key,
                "inferential_statistics": "not_applied_no_documented_replicates",
            },
        )
