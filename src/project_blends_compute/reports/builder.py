from __future__ import annotations

from typing import Any

from project_blends_compute.utils import utc_now_iso


class ReportBuilder:
    def integrated_report(
        self,
        *,
        run_id: str,
        request: dict[str, Any],
        identity: dict[str, Any],
        profiles: dict[str, Any],
        provenance: dict[str, Any],
        reactions: dict[str, Any],
        supporting: dict[str, Any],
        molecular_screening: dict[str, Any],
        quantum: dict[str, Any],
        uncertainty: dict[str, Any],
        evidence_packets: list[dict[str, Any]],
        warnings: list[str],
    ) -> dict[str, Any]:
        return {
            "schema_version": "project_blends_integrated_report.v2",
            "run_id": run_id,
            "generated_at_utc": utc_now_iso(),
            "scientific_contract": {
                "observational_foundation": "reported initial and four-week GC-MS relative-area profiles",
                "authoritative_identity": "PubChem-led cross-source registry with manual-review abstention",
                "authoritative_reaction_curation": "reaction_curation storage-reaction evidence",
                "authoritative_food_provenance": "pipeline_fooddb documented occurrence",
                "rxn_bridge_role": "analogous mapped reaction/template evidence",
                "foodchem_ml_role": "exploratory candidate ranking only; never occurrence evidence",
                "routelens_role": "artifact, queue, release and acceptance governance patterns only",
                "taxonomy_role": "authoritative supporting COCONUT taxonomy.coconut.v1 LightGBM model; probability-aware classification only, never identity confirmation",
                "molecular_screening_role": "RDKit descriptors/conformers/molecular-mechanics screening; explicitly not quantum chemistry",
                "quantum_role": "xTB/ORCA evidence only after successful external-engine execution",
            },
            "request": request,
            "identity": identity,
            "profiles": profiles,
            "provenance": provenance,
            "reactions": reactions,
            "supporting_evidence": supporting,
            "molecular_screening": molecular_screening,
            "quantum": quantum,
            "uncertainty": uncertainty,
            "evidence_packets": evidence_packets,
            "warnings": warnings,
        }

    def rag_export(self, report: dict[str, Any]) -> dict[str, Any]:
        """Compact lane-oriented export compatible with ChemRAG ingestion."""
        return {
            "schema_version": "project_blends_chemrag_export.v2",
            "run_id": report["run_id"],
            "generated_at_utc": report["generated_at_utc"],
            "sections": {
                "identity_qc": report["identity"],
                "profile_analysis": report["profiles"],
                "food_provenance": report["provenance"],
                "reaction_intelligence": report["reactions"],
                "supporting_evidence": report["supporting_evidence"],
                "molecular_screening": report["molecular_screening"],
                "quantum_chemistry": report["quantum"],
                "uncertainty": report["uncertainty"],
                "evidence_packets": report["evidence_packets"],
            },
            "claim_policy": {
                "allowed_classes": ["OBSERVED", "DERIVED", "LITERATURE_SUPPORTED", "COMPUTATIONALLY_SUPPORTED", "HYPOTHESIZED", "UNRESOLVED", "REJECTED"],
                "reaction_claims_require_direct_or_condition_matched_support": True,
                "exploratory_predictions_are_not_evidence": True,
            },
        }

    def markdown_summary(self, report: dict[str, Any]) -> str:
        metrics = report.get("profiles", {}).get("metrics", [])
        lines = [
            "# Project Blends integrated computation report",
            "",
            f"Run: `{report['run_id']}`",
            "",
            "## Scientific boundary",
            "",
            "The analysis compares reported GC-MS relative-area compositions. It does not infer absolute concentration, kinetics, or causal chemical conversion from peak appearance/disappearance alone.",
            "",
            "## Profile summary",
            "",
        ]
        for metric in metrics:
            lines.append(
                f"- **{metric['sample_id']}**: weighted Jaccard {float(metric['weighted_jaccard']):.4f}; "
                f"Bray-Curtis {float(metric['bray_curtis_dissimilarity']):.4f}; shared compounds {metric['shared_count']}."
            )
        lines.extend(["", "## Lane status", ""])
        for lane, status in report.get("uncertainty", {}).get("lane_status", {}).items():
            lines.append(f"- `{lane}`: {status}")
        screening = report.get("reactions", {}).get("screening_summary", {})
        if screening:
            lines.extend(["", "## Reaction pre-evidence screening", ""])
            lines.append(
                f"- Screened pairs: {screening.get('screened_pairs', 0)}; "
                f"retained candidates: {screening.get('candidate_pairs', 0)}; "
                f"rejected before evidence retrieval: {screening.get('rejected_pre_evidence', 0)}."
            )
        lines.extend(["", "## Warnings", ""])
        warnings = report.get("warnings") or ["None"]
        for warning in warnings:
            lines.append(f"- {warning}")
        return "\n".join(lines) + "\n"
