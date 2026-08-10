from __future__ import annotations

from typing import Any


class UncertaintyService:
    """Deterministic uncertainty aggregation with explicit evidence boundaries.

    Scores are descriptive confidence indices, not calibrated probabilities.
    """

    @staticmethod
    def identity_summary(compounds: list[dict[str, Any]]) -> dict[str, Any]:
        total = len(compounds)
        resolved = sum(bool(item.get("resolved_identity")) for item in compounds)
        pending = sum(item.get("adjudication_status") == "unresolved_pending" for item in compounds)
        manual_corrected = sum(item.get("adjudication_status") == "manual_corrected" for item in compounds)
        excluded = sum(item.get("adjudication_status") == "excluded_unresolved" for item in compounds)
        conflicts = sum(bool(item.get("conflict_flags")) for item in compounds)
        mean_conf = (sum(float(item.get("resolution_confidence") or 0.0) for item in compounds) / total) if total else 0.0
        return {
            "total": total,
            "resolved": resolved,
            "unresolved": total - resolved,
            "manual_review_pending": pending,
            "manual_corrected": manual_corrected,
            "excluded_unresolved": excluded,
            "with_conflicts": conflicts,
            "mean_resolution_confidence": round(mean_conf, 6),
            "interpretation": "identity confidence index; library annotations remain tentative without spectra/retention-index/standard confirmation",
        }

    @staticmethod
    def reaction_summary(evaluations: list[dict[str, Any]]) -> dict[str, Any]:
        total = len(evaluations)
        abstained = sum(bool(item.get("abstained")) for item in evaluations)
        by_grade: dict[str, int] = {}
        scores: list[float] = []
        for item in evaluations:
            grade = str(item.get("evidence_grade", "unresolved"))
            by_grade[grade] = by_grade.get(grade, 0) + 1
            scores.append(float(item.get("plausibility_score") or 0.0))
        return {
            "total": total,
            "abstained": abstained,
            "retained": total - abstained,
            "evidence_grade_counts": by_grade,
            "mean_plausibility_score": round(sum(scores) / len(scores), 6) if scores else 0.0,
            "interpretation": "plausibility support only; not proof of in-sample transformation",
        }

    @staticmethod
    def provenance_summary(occurrences: list[dict[str, Any]], unresolved: list[dict[str, Any]]) -> dict[str, Any]:
        samples = sorted({str(item.get("sample_id")) for item in occurrences if item.get("sample_id")})
        documented = sum(bool(item.get("documented_occurrence")) for item in occurrences)
        return {
            "documented_occurrence_records": documented,
            "unresolved_queries": len(unresolved),
            "samples_with_documented_evidence": samples,
            "interpretation": "FoodDB occurrence is source-provenance evidence; FoodChem ML links are excluded from this count",
        }

    def aggregate(
        self,
        *,
        compounds: list[dict[str, Any]],
        profile_metrics: list[dict[str, Any]],
        occurrences: list[dict[str, Any]],
        unresolved_provenance: list[dict[str, Any]],
        reaction_evaluations: list[dict[str, Any]],
        lane_status: dict[str, str],
    ) -> dict[str, Any]:
        identities = self.identity_summary(compounds)
        reactions = self.reaction_summary(reaction_evaluations)
        provenance = self.provenance_summary(occurrences, unresolved_provenance)
        available_lanes = sum(status in {"available", "complete", "enabled", "executed", "executed_no_match", "executed_with_unknowns", "available_not_run", "queued"} for status in lane_status.values())
        lane_fraction = available_lanes / max(1, len(lane_status))
        identity_fraction = identities["resolved"] / max(1, identities["total"])
        reaction_nonabstain = reactions["retained"] / max(1, reactions["total"]) if reactions["total"] else 0.0
        overall = 0.45 * identity_fraction + 0.25 * lane_fraction + 0.15 * reaction_nonabstain + 0.15 * (1.0 if profile_metrics else 0.0)
        return {
            "schema_version": "project_blends_uncertainty.v1",
            "identity": identities,
            "provenance": provenance,
            "reaction": reactions,
            "lane_status": lane_status,
            "descriptive_evidence_completeness": round(overall, 6),
            "not_a_probability": True,
            "claim_boundaries": [
                "relative GC-MS area percentages do not establish absolute concentration changes",
                "two timepoints without documented replicates do not support inferential kinetics",
                "reaction analogies and molecular-screening descriptors support plausibility only; xTB/ORCA count as quantum evidence only after successful execution",
                "predicted food-compound links are exploratory and not occurrence evidence",
            ],
        }
