"""EU-residency enforcement helpers.

Pure logic shared by the LLM broker, STT, and embedding gates. When EU mode
is on, every egress path consults these helpers and fails closed
(Constitution Rule 3): an unknown or non-EU provider is denied, never allowed.

EU-mode *state* is persisted server-side (see ``mycelos.llm.eu_mode``); this
module only answers "is this destination EU-resident?".
"""
from __future__ import annotations

from mycelos.llm.providers import PROVIDERS, _guess_provider_for_eu


class EUResidencyError(Exception):
    """Raised when EU mode is on and an action would send data outside the EU."""


def provider_of_model(model: str) -> str:
    """Return the provider id for a model string (prefixed or bare)."""
    if "/" in model:
        return model.split("/", 1)[0]
    return _guess_provider_for_eu(model)


def is_eu_model(model: str) -> bool:
    """True only if the model's provider keeps data in the EU.

    Fail-closed: an unknown provider (not in PROVIDERS, or without the
    eu_residency flag) returns False.
    """
    provider = provider_of_model(model)
    cfg = PROVIDERS.get(provider)
    return bool(cfg and cfg.eu_residency)


def filter_eu_models(models: list[str]) -> list[str]:
    """Return only the EU-resident models, preserving order."""
    return [m for m in models if is_eu_model(m)]


def vertex_region_is_eu(region: str | None) -> bool:
    """True if a Vertex AI region pins data to the EU (europe-* only)."""
    return bool(region) and region.startswith("europe-")
