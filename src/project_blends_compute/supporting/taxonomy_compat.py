from __future__ import annotations

from typing import Any

from rdkit.Chem import rdFingerprintGenerator


def modern_morgan_bits(mol: Any, radius: int, nbits: int) -> list[int]:
    """Morgan bits using the non-deprecated RDKit fingerprint-generator API."""
    generator = rdFingerprintGenerator.GetMorganGenerator(radius=int(radius), fpSize=int(nbits))
    return list(generator.GetFingerprint(mol).GetOnBits())


def install_modern_morgan_generator() -> dict[str, str]:
    """Modernize the upstream taxonomy feature builder without duplicating it.

    The COCONUT taxonomy model owns its 42-descriptor + 2048-bit Morgan feature
    contract upstream. Project Blends only replaces the deprecated RDKit call with
    the equivalent generator API. The actual feature builder, order, model and
    metadata remain upstream-owned.
    """
    try:
        from pipelines.taxonomy_bridge import feature_builder
    except Exception as exc:  # pragma: no cover - exercised only with external runtime
        return {"status": "unavailable", "error": f"{type(exc).__name__}:{exc}"}
    feature_builder._compute_morgan_bits = modern_morgan_bits
    return {
        "status": "installed",
        "backend": "rdFingerprintGenerator.GetMorganGenerator",
        "feature_contract_owner": "pipelines.taxonomy_bridge.feature_builder",
    }
