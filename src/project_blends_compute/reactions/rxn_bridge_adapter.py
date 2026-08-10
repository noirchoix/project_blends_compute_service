from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from project_blends_compute.reactions.chemistry import product_similarity
from project_blends_compute.schemas.reactions import ReactionCandidate, TemplateEvidence
from project_blends_compute.settings import Settings


class RxnBridgeAdapter:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.provider: Any = None
        self.error: str | None = None
        self._initialize()

    def _initialize(self) -> None:
        root = self.settings.rxn_bridge_project_root
        if root is None or not root.exists():
            self.error = "rxn_bridge_project_root_not_configured"
            return
        for path in (root, root / "pipelines"):
            text = str(path.resolve())
            if text not in sys.path:
                sys.path.insert(0, text)
        try:
            from reaction_framework.providers.rxnutils_provider import RxnUtilsEvidenceProvider, RxnUtilsProviderConfig
            from reaction_framework.providers.template_store import TemplateStore, TemplateStoreConfig

            if self.settings.rxn_artifact_registry and self.settings.rxn_artifact_registry.exists():
                from reaction_framework.providers.artifact_registry import ArtifactRegistry, RegistryConfig

                registry = ArtifactRegistry(
                    RegistryConfig(
                        registry_path=self.settings.rxn_artifact_registry,
                        require_existing_files=True,
                        resolve_relative_to_registry=False,
                    )
                )
                self.provider = RxnUtilsEvidenceProvider.from_registry(
                    registry,
                    cfg=RxnUtilsProviderConfig(max_candidates_per_query=64, max_products_per_template=12, fallback_to_echo_transform=False),
                    include_fallback_templates=False,
                )
            elif self.settings.rxn_template_artifact_root and self.settings.rxn_template_artifact_root.exists():
                store = TemplateStore(
                    TemplateStoreConfig(
                        artifact_root=str(self.settings.rxn_template_artifact_root),
                        include_fallback_templates=False,
                    )
                )
                self.provider = RxnUtilsEvidenceProvider(store)
            else:
                self.error = "rxn_template_artifacts_not_configured"
        except Exception as exc:
            self.error = f"rxn_bridge_initialization_failed:{type(exc).__name__}:{exc}"

    @property
    def available(self) -> bool:
        return self.provider is not None

    def evaluate(self, candidate: ReactionCandidate) -> list[TemplateEvidence]:
        if not self.provider or not candidate.precursor_smiles:
            return []
        try:
            summary = self.provider.summarize(
                [candidate.precursor_smiles],
                mechanism_hints=[candidate.transformation_family],
                batch_meta={"hypothesis_id": candidate.hypothesis_id, "sample_id": candidate.sample_id},
            )
        except Exception as exc:
            self.error = f"rxn_bridge_query_failed:{type(exc).__name__}:{exc}"
            return []
        evidence: list[TemplateEvidence] = []
        for hit in summary.evidence_hits:
            similarity = product_similarity(candidate.product_smiles, list(hit.candidate_products))
            evidence.append(
                TemplateEvidence(
                    template_id=hit.template_id,
                    template_family=hit.template_family,
                    support_count=hit.support_count,
                    confidence=float(hit.confidence),
                    candidate_products=list(hit.candidate_products),
                    product_match_similarity=similarity,
                    mechanism_tags=list(hit.mechanism_tags),
                    provenance=dict(hit.provenance),
                )
            )
        evidence.sort(key=lambda item: ((item.product_match_similarity or 0.0) * item.confidence), reverse=True)
        return evidence[:20]
