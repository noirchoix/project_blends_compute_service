from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path
from typing import Iterable

from project_blends_compute.utils import dedupe_keep_order


# These aliases repair transcription/punctuation/spelling defects in the Project
# Blends report. They are lookup-only aliases: the original GC-MS reported name is
# never overwritten, and accepting a structure still requires an external database
# record plus RDKit structure validation.
BUILTIN_LOOKUP_ALIASES: dict[str, list[str]] = {
    "gamma--Terpinene": ["gamma-Terpinene"],
    "Carophyllene": ["Caryophyllene"],
    "gamma--Muurolene": ["gamma-Muurolene"],
    "Phenol, 2-methoxy-4-(2 propenyl)-,acetate": [
        "Phenol, 2-methoxy-4-(2-propenyl)-, acetate",
    ],
    "(-)-beta--Bourbonene": ["(-)-beta-Bourbonene", "beta-Bourbonene"],
    "Dehydroelasholtza ketone": ["Dehydroelsholtzia ketone"],
    # Authoritatively verified synonym rescues for long NIST-style names whose
    # Word-table line wrapping prevents direct PubChem name lookup. These aliases
    # are search aids only; the external database still supplies the structure.
    "Naphthalene, decahydro-4a-methyl-1 -methylene-7-(1-methylethenyl)-, [4aR-(4aalpha-,7alpha-,8abeta-)]": ["beta-Selinene"],
    "Naphthalene,1,2,3,5,6,7,8,8a-octahydro-1,8a-dimethyl-7-(1-methylethenyl)-, [1R-(1α,7β,8aα)]-": ["Valencene"],
    "Benzene,1-(1,5-dimethyl-4-hexenyl)-4-methyl-": ["alpha-Curcumene"],
    "Tricyclo[2.2.1.0(2,6)]heptane, 1,3 ,3-trimethyl-": ["Cyclofenchene"],
    "1H-Cyclopropa[a]naphthalene, 1a,2, 3,3a,4,5,6,7b-octahydro-1,1,3a,7-t etramethyl-, [1aR-(1aalpha-,3a.al pha.,7balpha-)]-": ["beta-Maaliene"],
    "Naphthalene, 1,2,3,5,6,8a-hexahydr o-4,7-dimethyl-1-(1-methylethyl)-, (1S-cis)-": ["delta-Cadinene"],
    "1H-Cycloprop[e]azulen-7-ol, decahy dro-1,1,7-trimethyl-4-methylene-, [1ar-(1aalpha-,4aalpha-,7beta-, 7abeta-,7balpha-)]-": ["Spathulenol"],
    "1H-Cyclopropa[a]naphthalene, 1a,2, 6,7,7a,7b-hexahydro-1,1,7,7a-tetra methyl-, [1aR-(1aalpha-,7alpha-, 7aalpha-,7balpha-)]-": ["1,2,9,10-Tetradehydroaristolane"],
    "Naphthalene, 1,2,3,5,6,7,8,8a-octa hydro-1,8a-dimethyl-7-(1-methyleth enyl)-, [1S-(1alpha-,7alpha-,8a. alpha.)]-": ["Eremophilene"],
    "Neoisolongifolene, 8,9-dehydro-4,4-Dimethyl-3-(3-methylbut-3-enylidene)-2-methylenebicyclo[4.1.0]heptane": ["Neoisolongifolene, 8,9-dehydro-"],
    "1,3-Cyclohexadiene, 5-(1,5-dimethy l-4-hexenyl)-2-methyl-, [S-(R*,S*) ]-": ["Zingiberene"],
    "(4aS,4bR,10aS)-7-Isopropyl-1,1,4a- trimethyl-1,2,3,4,4a,5,6,9,10,10a-deca hydrophenanthrene": ["Abieta-7,13-diene"],
}


def _basic_cleanup(value: str) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).strip()
    text = (
        text.replace("α", "alpha")
        .replace("β", "beta")
        .replace("γ", "gamma")
        .replace("δ", "delta")
    )
    text = re.sub(r"\.(alpha|beta|gamma|delta)\.", r"\1", text, flags=re.IGNORECASE)
    text = text.replace("‐", "-").replace("‑", "-").replace("–", "-").replace("—", "-")
    text = re.sub(r"\s+", " ", text)
    # Word/line wrapping in the source introduced spacing around punctuation and
    # hyphens. Normalizing those boundaries is safe and reversible because the raw
    # reported name remains stored separately.
    text = re.sub(r"\s*-\s*", "-", text)
    text = re.sub(r"\s*,\s*", ", ", text)
    text = re.sub(r"\[\s*", "[", text)
    text = re.sub(r"\s*\]", "]", text)
    text = re.sub(r"\(\s*", "(", text)
    text = re.sub(r"\s*\)", ")", text)
    # NIST export strings such as .gamma.-Terpinene often become gamma--Terpinene
    # after Greek-token normalization.
    text = re.sub(r"\b(alpha|beta|gamma|delta)--+", r"\1-", text, flags=re.IGNORECASE)
    return text.strip()


def _compact_systematic_name(value: str) -> str:
    """Generate a whitespace-free systematic-name variant.

    This is specifically useful for GC-MS/NIST names broken across Word table lines,
    e.g. ``hexahydr o`` or ``deca hydrophenanthrene``. PubChem/NIST systematic
    synonyms commonly store the same identifiers without optional whitespace.
    """

    return re.sub(r"\s+", "", value)


def load_alias_map(path: Path | None) -> dict[str, list[str]]:
    aliases = {key: list(values) for key, values in BUILTIN_LOOKUP_ALIASES.items()}
    if path is None or not path.exists():
        return aliases
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return aliases
    if not isinstance(payload, dict):
        return aliases
    for key, values in payload.items():
        if isinstance(values, str):
            values = [values]
        if isinstance(values, list):
            aliases.setdefault(str(key), []).extend(str(v) for v in values if v)
    return aliases


def identity_lookup_variants(name: str, *, alias_map: dict[str, list[str]] | None = None) -> list[str]:
    """Return conservative external-database lookup strings for a reported name.

    The returned variants are search aids only. They do not rewrite the analytical
    identity and they never use the manuscript's legacy SMILES.
    """

    raw = str(name or "").strip()
    if not raw:
        return []
    aliases = alias_map or BUILTIN_LOOKUP_ALIASES
    cleaned = _basic_cleanup(raw)
    values: list[str] = [raw]
    if cleaned and cleaned != raw:
        values.append(cleaned)
    compact = _compact_systematic_name(cleaned)
    if compact and compact not in {raw, cleaned}:
        values.append(compact)
    for key in (raw, cleaned):
        for alias in aliases.get(key, []):
            alias_clean = _basic_cleanup(alias)
            values.append(alias_clean)
            alias_compact = _compact_systematic_name(alias_clean)
            if alias_compact != alias_clean:
                values.append(alias_compact)
    return dedupe_keep_order([v for v in values if v])


def variants_for_many(names: Iterable[str], *, alias_map: dict[str, list[str]] | None = None) -> list[str]:
    values: list[str] = []
    for name in names:
        values.extend(identity_lookup_variants(name, alias_map=alias_map))
    return dedupe_keep_order(values)
