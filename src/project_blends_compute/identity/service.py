from __future__ import annotations

import asyncio
from importlib.resources import files
from collections import Counter
from pathlib import Path
from typing import Iterable

from project_blends_compute.identity.adjudications import IdentityAdjudication, IdentityAdjudicationRegistry
from project_blends_compute.identity.cache import IdentityCache
from project_blends_compute.identity.chemistry import connectivity_block, validate_structure
from project_blends_compute.identity.frozen import FrozenIdentityRegistry
from project_blends_compute.identity.names import load_alias_map
from project_blends_compute.identity.sources import ChEBISource, IdentitySource, JsonCacheSource, PubChemSource
from project_blends_compute.schemas.identity import (
    CanonicalCompound,
    IdentityAdjudicationStatus,
    IdentityCandidate,
    IdentityResolveItem,
    IdentityResolveRequest,
    IdentityResolveResponse,
    ManualReviewStatus,
    StereochemistryStatus,
)
from project_blends_compute.settings import Settings
from project_blends_compute.utils import normalize_name, stable_hash, utc_now_iso


SOURCE_RANK = {"manual_pubchem_adjudication": -1, "pubchem": 0, "chebi": 1, "nist": 2, "lotus_coconut": 3, "fooddb": 4, "reported": 99}
IDENTITY_POLICY_VERSION = "name_first_external_structure_v5_manual_adjudication_freeze"


class IdentityService:
    def __init__(self, settings: Settings, *, reference_root: Path | None = None) -> None:
        self.settings = settings
        configured_reference = settings.project_root / "data" / "reference"
        bundled_reference = Path(str(files("project_blends_compute").joinpath("resources", "reference")))
        self.reference_root = reference_root or (configured_reference if configured_reference.exists() else bundled_reference)
        self.cache = IdentityCache(settings.state_root / "identity_cache.sqlite3")
        self.alias_map = load_alias_map(self.reference_root / "name_lookup_aliases.json")
        self.adjudications = IdentityAdjudicationRegistry(self.reference_root / "identity_adjudications.json")
        self.frozen_registry = FrozenIdentityRegistry(self.reference_root / "project_blends_compound_registry_v1.json")

    def _sources(self, requested: Iterable[str], online: bool) -> list[IdentitySource]:
        selected = set(requested)
        if "reported" in selected:
            raise ValueError(
                "The undergraduate manuscript SMILES are provenance-only and cannot be used as an identity evidence source. "
                "Use PubChem/ChEBI/NIST-backed resolution instead."
            )
        sources: list[IdentitySource] = []
        if "pubchem" in selected:
            if online:
                sources.append(PubChemSource(self.settings.pubchem_base_url, self.settings.identity_timeout_s, self.settings.identity_max_candidates, alias_map=self.alias_map))
            else:
                sources.append(JsonCacheSource("pubchem", self.reference_root / "pubchem_cache.json", base_score=0.95, alias_map=self.alias_map))
        if "chebi" in selected:
            if online:
                sources.append(ChEBISource(self.settings.chebi_base_url, self.settings.identity_timeout_s, alias_map=self.alias_map))
            else:
                sources.append(JsonCacheSource("chebi", self.reference_root / "chebi_cache.json", base_score=0.85, alias_map=self.alias_map))
        if "nist" in selected:
            sources.append(JsonCacheSource("nist", self.reference_root / "nist_cache.json", base_score=0.80, alias_map=self.alias_map))
        if "lotus" in selected or "coconut" in selected or "lotus_coconut" in selected:
            sources.append(JsonCacheSource("lotus_coconut", self.reference_root / "lotus_coconut_cache.json", base_score=0.72, alias_map=self.alias_map))
        if "fooddb" in selected:
            sources.append(JsonCacheSource("fooddb", self.reference_root / "fooddb_identity_cache.json", base_score=0.55, alias_map=self.alias_map))
        return sources

    async def resolve(self, request: IdentityResolveRequest) -> IdentityResolveResponse:
        online = self.settings.identity_online_enabled if request.online is None else request.online
        sources = self._sources(request.sources, bool(online))
        compounds: list[CanonicalCompound] = []
        frozen_registry_used = 0
        for item in request.items:
            adjudication = self.adjudications.get(item.reported_name)
            frozen = None if request.force_refresh else self.frozen_registry.get(item.reported_name)
            if frozen is not None:
                current_legacy = list(
                    dict.fromkeys([value for value in [item.reported_smiles, *item.legacy_reported_smiles] if value])
                )
                if current_legacy:
                    frozen.reported_smiles = current_legacy[0]
                    frozen.legacy_reported_smiles = current_legacy
                compounds.append(frozen)
                frozen_registry_used += 1
                continue
            cache_key = stable_hash(
                "|".join(
                    [
                        IDENTITY_POLICY_VERSION,
                        self.adjudications.content_sha256,
                        normalize_name(item.reported_name),
                        adjudication.adjudication_id if adjudication else "no_adjudication",
                        ",".join(sorted(request.sources)),
                        str(bool(online)),
                    ]
                )
            )
            cached = None if request.force_refresh else self.cache.get(cache_key)
            if cached:
                compounds.append(CanonicalCompound.model_validate(cached))
                continue

            # A manually corrected identity preserves the source-reported name but
            # searches external databases under the adjudicated database name. The
            # curated adjudication itself remains deterministic and sufficient for
            # offline reproducibility after structure validation.
            lookup_item = item
            if adjudication and adjudication.status == IdentityAdjudicationStatus.MANUAL_CORRECTED.value and adjudication.adjudicated_name:
                lookup_item = item.model_copy(update={"reported_name": adjudication.adjudicated_name})

            candidate_groups = await asyncio.gather(*(source.resolve(lookup_item) for source in sources), return_exceptions=True)
            candidates: list[IdentityCandidate] = []
            for group in candidate_groups:
                if isinstance(group, Exception):
                    continue
                candidates.extend(group)
            if adjudication:
                manual_candidate = self.adjudications.candidate(adjudication)
                if manual_candidate is not None:
                    candidates.append(manual_candidate)

            resolved = self._adjudicate(item, candidates, strict=request.strict, adjudication=adjudication)
            self.cache.set(cache_key, resolved.model_dump(mode="json"))
            compounds.append(resolved)

        unresolved_pending = sum(c.adjudication_status == IdentityAdjudicationStatus.UNRESOLVED_PENDING for c in compounds)
        excluded_unresolved = sum(c.adjudication_status == IdentityAdjudicationStatus.EXCLUDED_UNRESOLVED for c in compounds)
        manual_corrected = sum(c.adjudication_status == IdentityAdjudicationStatus.MANUAL_CORRECTED for c in compounds)
        unresolved_total = sum(not c.resolved_identity for c in compounds)
        return IdentityResolveResponse(
            ok=(not request.strict or unresolved_pending == 0),
            compounds=compounds,
            unresolved_count=unresolved_total,
            unresolved_pending_count=unresolved_pending,
            excluded_unresolved_count=excluded_unresolved,
            manual_corrected_count=manual_corrected,
            conflict_count=sum("cross_source_structure_conflict" in c.conflict_flags for c in compounds),
            manual_review_count=sum(c.manual_review_status == ManualReviewStatus.PENDING for c in compounds),
            adjudication_registry_sha256=self.adjudications.content_sha256,
            frozen_registry_sha256=self.frozen_registry.content_sha256 if len(self.frozen_registry) else None,
            frozen_registry_used_count=frozen_registry_used,
        )

    def _adjudicate(
        self,
        item: IdentityResolveItem,
        candidates: list[IdentityCandidate],
        *,
        strict: bool,
        adjudication: IdentityAdjudication | None = None,
    ) -> CanonicalCompound:
        deduped = self._dedupe_candidates(candidates)
        for candidate in deduped:
            candidate.score = self._score_candidate(item, candidate, deduped)

        manual_candidate = next((c for c in deduped if c.source == "manual_pubchem_adjudication"), None)
        if adjudication and adjudication.status == IdentityAdjudicationStatus.MANUAL_CORRECTED.value:
            if manual_candidate is None:
                raise ValueError(f"manual identity adjudication has no validated structure: {adjudication.adjudication_id}")
            manual_candidate.score = max(manual_candidate.score, 0.99)

        ranked = sorted(deduped, key=lambda c: (-c.score, SOURCE_RANK.get(c.source, 99), c.source_id or ""))
        top = manual_candidate if manual_candidate is not None else (ranked[0] if ranked else None)
        conflict_flags = self._conflicts(item, ranked)
        threshold = 0.78 if strict else 0.65
        blocking_conflict = "cross_source_structure_conflict" in conflict_flags
        exact_name_match = bool(top and normalize_name(top.preferred_name or "") == normalize_name(item.reported_name))
        source_consensus = self._supporting_source_count(top, ranked) >= 2
        runner_up_score = next((c.score for c in ranked if c is not top), 0.0)
        decisive_margin = bool(top and (top.score - runner_up_score) >= 0.04)

        status = IdentityAdjudicationStatus.UNRESOLVED_PENDING
        resolved = False
        manual_status = ManualReviewStatus.PENDING
        method = "name_first_unresolved_candidate_set"
        adjudication_id = None
        adjudicated_name = None
        adjudication_notes: list[str] = []
        adjudication_source: dict[str, object] = {}

        if adjudication and adjudication.status == IdentityAdjudicationStatus.EXCLUDED_UNRESOLVED.value:
            status = IdentityAdjudicationStatus.EXCLUDED_UNRESOLVED
            resolved = False
            manual_status = ManualReviewStatus.APPROVED
            method = "manual_excluded_unresolved"
            adjudication_id = adjudication.adjudication_id
            adjudicated_name = adjudication.adjudicated_name
            adjudication_notes = [adjudication.reason] if adjudication.reason else []
            adjudication_source = dict(adjudication.source)
        elif adjudication and adjudication.status == IdentityAdjudicationStatus.MANUAL_CORRECTED.value:
            status = IdentityAdjudicationStatus.MANUAL_CORRECTED
            resolved = bool(top and top.parse_valid)
            manual_status = ManualReviewStatus.APPROVED if resolved else ManualReviewStatus.PENDING
            method = "manual_corrected_official_database_structure"
            adjudication_id = adjudication.adjudication_id
            adjudicated_name = adjudication.adjudicated_name
            adjudication_notes = [adjudication.reason] if adjudication.reason else []
            adjudication_source = dict(adjudication.source)
            # The manual correction is itself the adjudication decision. Candidate
            # multiplicity from external lookup remains informational and must not
            # override the approved canonical record unless its structure fails local
            # validation (which is checked before this point).
            conflict_flags = [flag for flag in conflict_flags if flag != "cross_source_structure_conflict"]
            blocking_conflict = False
        else:
            resolved = bool(
                top
                and top.parse_valid
                and top.score >= threshold
                and not blocking_conflict
                and (exact_name_match or source_consensus or decisive_margin or len(ranked) == 1)
            )
            if resolved:
                status = IdentityAdjudicationStatus.RESOLVED
                manual_status = ManualReviewStatus.NOT_REQUIRED
                method = (
                    "name_first_cross_source_consensus"
                    if self._supporting_source_count(top, ranked) >= 2
                    else "name_first_single_source_resolution"
                )

        legacy_smiles = list(
            dict.fromkeys([value for value in [item.reported_smiles, *item.legacy_reported_smiles] if value])
        )
        compound_key = top.inchikey if resolved and top and top.inchikey else normalize_name(item.reported_name)
        compound_id = "cmp-" + stable_hash(compound_key)[:16]
        isomer_group_id = None
        tautomer_parent_id = None
        stereo = StereochemistryStatus.UNKNOWN
        if resolved and top and (top.isomeric_smiles or top.canonical_smiles or top.inchi):
            validation = validate_structure(smiles=top.isomeric_smiles or top.canonical_smiles, inchi=top.inchi)
            stereo = validation.stereochemistry_status
            block = connectivity_block(validation.inchikey)
            isomer_group_id = f"iso-{stable_hash(block)[:12]}" if block else None
            tautomer_parent_id = (
                f"tau-{stable_hash(validation.tautomer_parent_smiles)[:12]}"
                if validation.tautomer_parent_smiles
                else None
            )

        return CanonicalCompound(
            compound_id=compound_id,
            preferred_name=top.preferred_name if top else None,
            reported_name=item.reported_name,
            normalized_name=normalize_name(item.reported_name),
            pubchem_cid=top.pubchem_cid if top else None,
            chebi_id=top.chebi_id if top else None,
            cas_number=top.cas_number if top else None,
            inchi=top.inchi if resolved and top else None,
            inchikey=top.inchikey if resolved and top else None,
            canonical_smiles=top.canonical_smiles if resolved and top else None,
            isomeric_smiles=top.isomeric_smiles if resolved and top else None,
            molecular_formula=top.molecular_formula if resolved and top else None,
            exact_mass=top.exact_mass if resolved and top else None,
            formal_charge=top.formal_charge if resolved and top else None,
            stereochemistry_status=stereo,
            isomer_group_id=isomer_group_id,
            tautomer_parent_id=tautomer_parent_id,
            structure_source=top.source if resolved and top else None,
            source_retrieved_at=utc_now_iso() if resolved and top else None,
            resolution_method=method,
            resolution_confidence=top.score if resolved and top else 0.0,
            manual_review_status=manual_status,
            reported_smiles=legacy_smiles[0] if legacy_smiles else None,
            legacy_reported_smiles=legacy_smiles,
            reported_smiles_valid=None,
            identity_basis="reported_gc_ms_name",
            adjudication_status=status,
            adjudication_id=adjudication_id,
            adjudicated_name=adjudicated_name,
            adjudication_notes=adjudication_notes,
            adjudication_source=adjudication_source,
            downstream_structure_eligible=bool(resolved and top and (top.canonical_smiles or top.isomeric_smiles)),
            resolved_identity=resolved,
            candidate_identity_set=ranked,
            conflict_flags=sorted(set(conflict_flags)),
            provenance=[p for candidate in ranked[:5] for p in candidate.provenance],
        )

    @staticmethod
    def _dedupe_candidates(candidates: list[IdentityCandidate]) -> list[IdentityCandidate]:
        grouped: dict[str, IdentityCandidate] = {}
        for candidate in candidates:
            key = candidate.inchikey or candidate.canonical_smiles or f"{candidate.source}:{candidate.source_id}:{candidate.preferred_name}"
            existing = grouped.get(key)
            if existing is None or candidate.score > existing.score:
                grouped[key] = candidate
            elif existing:
                existing.provenance.extend(candidate.provenance)
        return list(grouped.values())

    def _score_candidate(self, item: IdentityResolveItem, candidate: IdentityCandidate, peers: list[IdentityCandidate]) -> float:
        score = float(candidate.score)
        normalized_query = normalize_name(item.reported_name)
        normalized_preferred = normalize_name(candidate.preferred_name or "")
        if normalized_query == normalized_preferred:
            score += 0.08
        elif normalized_query and normalized_preferred and (normalized_query in normalized_preferred or normalized_preferred in normalized_query):
            score += 0.04
        if item.reported_formula and candidate.molecular_formula == item.reported_formula:
            score += 0.04
        supporting_sources = self._supporting_source_count(candidate, peers)
        score += min(0.08, 0.03 * max(0, supporting_sources - 1))
        if candidate.parse_valid is False:
            score = min(score, 0.35)
        return max(0.0, min(1.0, score))

    @staticmethod
    def _supporting_source_count(candidate: IdentityCandidate | None, peers: list[IdentityCandidate]) -> int:
        if candidate is None:
            return 0
        key = candidate.inchikey or candidate.canonical_smiles
        return len({peer.source for peer in peers if key and (peer.inchikey or peer.canonical_smiles) == key})

    @staticmethod
    def _conflicts(item: IdentityResolveItem, ranked: list[IdentityCandidate]) -> list[str]:
        flags: list[str] = []
        valid = [candidate for candidate in ranked if candidate.parse_valid and candidate.inchikey and candidate.source != "reported"]
        valid_keys = [candidate.inchikey for candidate in valid]

        # Multiple candidates from PubChem (e.g. unspecified/specified stereoisomers)
        # are not a *cross-source* disagreement. Compare the best-supported structure
        # from each independent authority instead.
        best_by_source: dict[str, IdentityCandidate] = {}
        for candidate in valid:
            if candidate.source not in {"pubchem", "chebi", "nist"}:
                continue
            existing = best_by_source.get(candidate.source)
            if existing is None or candidate.score > existing.score:
                best_by_source[candidate.source] = candidate
        authoritative_keys = {candidate.inchikey for candidate in best_by_source.values() if candidate.score >= 0.70 and candidate.inchikey}
        if len(best_by_source) >= 2 and len(authoritative_keys) > 1:
            flags.append("cross_source_structure_conflict")
        if len(set(valid_keys)) > 1:
            flags.append("multiple_structural_candidates_same_or_multi_source")
        formula_counts = Counter(candidate.molecular_formula for candidate in ranked if candidate.molecular_formula)
        if len(formula_counts) > 1:
            flags.append("candidate_formula_conflict")
        return flags
