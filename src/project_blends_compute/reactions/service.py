from __future__ import annotations

from project_blends_compute.reactions.atom_mapping import AtomMapper
from project_blends_compute.reactions.candidate_generation import generate_candidates_with_screening
from project_blends_compute.reactions.curation_adapter import ReactionCurationAdapter
from project_blends_compute.reactions.plausibility import evaluate_candidate
from project_blends_compute.reactions.rxn_bridge_adapter import RxnBridgeAdapter
from project_blends_compute.schemas.reactions import (
    ReactionEvaluateRequest,
    ReactionEvaluateResponse,
    ReactionGenerateRequest,
    ReactionGenerateResponse,
)


class ReactionService:
    def __init__(self, rxn_bridge: RxnBridgeAdapter, curation: ReactionCurationAdapter) -> None:
        self.rxn_bridge = rxn_bridge
        self.curation = curation
        self.mapper = AtomMapper()

    def generate(self, request: ReactionGenerateRequest) -> ReactionGenerateResponse:
        candidates, alternatives, screening, rejected = generate_candidates_with_screening(
            sample_id=request.sample_id,
            before=request.before,
            after=request.after,
            max_candidates=request.max_candidates,
            minimum_similarity=request.minimum_similarity,
            minimum_identity_confidence=request.minimum_identity_confidence,
            include_all_alternatives=request.include_all_alternatives,
        )
        mapped_candidates = []
        for candidate in candidates:
            result = self.mapper.map(candidate.reaction_smiles)
            candidate.mapped_reaction_smiles = result.mapped_reaction_smiles
            candidate.mapping_status = result.status
            candidate.warnings.extend(result.diagnostics)
            mapped_candidates.append(candidate)
        return ReactionGenerateResponse(
            ok=True,
            candidates=mapped_candidates,
            alternatives=alternatives,
            screening=screening,
            rejected_pairs=rejected,
            method={
                "candidate_policy": "disappeared_to_appeared_pairs_with_conservative_storage_pre_evidence_gating_v2",
                "filters": ["identity_and_structure_gate", "formula_delta_gate", "tanimoto_and_mcs_connectivity_gate", "same_formula_analytical_ambiguity_redirect", "storage_chemistry_prior_gate"],
                "minimum_identity_confidence": request.minimum_identity_confidence,
                "mapping": "rxnmapper_when_available_else_rdkit_mcs_fallback",
                "causal_status": "hypothesis_generation_only",
            },
        )

    def evaluate(self, request: ReactionEvaluateRequest) -> ReactionEvaluateResponse:
        evaluations = []
        warnings: list[str] = []
        lane_status = {
            "rxn_bridge": "available" if self.rxn_bridge.available else "unavailable",
            "reaction_curation": "available" if self.curation.available else "unavailable",
        }
        if request.strict and request.use_rxn_bridge and not self.rxn_bridge.available:
            raise RuntimeError(self.rxn_bridge.error or "rxn_bridge unavailable")
        if request.strict and request.use_reaction_curation and not self.curation.available:
            raise RuntimeError(self.curation.error or "reaction_curation unavailable")
        if self.rxn_bridge.error:
            warnings.append(self.rxn_bridge.error)
        if self.curation.error:
            warnings.append(self.curation.error)
        for candidate in request.candidates:
            templates = self.rxn_bridge.evaluate(candidate) if request.use_rxn_bridge else []
            curated = self.curation.evaluate(candidate) if request.use_reaction_curation else []
            evaluations.append(evaluate_candidate(candidate, templates, curated, request.storage_context))
        return ReactionEvaluateResponse(ok=True, evaluations=evaluations, lane_status=lane_status, warnings=warnings)
