"""Compute smart default model assignments for Mycelos system agents.

Assigns the right model tier to each system agent role based on
what models the user has available. Cross-provider fallbacks ensure
resilience.
"""

from __future__ import annotations

from mycelos.llm.providers import ModelInfo


# Agent roles and their ideal tier.
AGENT_ROLES: dict[str, dict[str, str]] = {
    "system:execution": {"tier": "sonnet", "description": "Default execution chain for agents without an explicit assignment"},
    "system:classification": {"tier": "haiku", "description": "Background tasks: knowledge classification, reminders, session summaries"},
    "mycelos:execution": {"tier": "sonnet", "description": "Primary chat agent — balanced reasoning and cost"},
    "builder:execution": {"tier": "opus", "description": "Workflow + agent creation needs the strongest model"},
    "workflow-agent:execution": {"tier": "haiku", "description": "Workflow default (overridden per workflow)"},
    "auditor-agent:execution": {"tier": "opus", "description": "Security review must be thorough"},
    "evaluator-agent:execution": {"tier": "haiku", "description": "Evaluation often deterministic"},
}

# Tier quality ordering: lower number = higher quality.
_TIER_RANK: dict[str, int] = {"opus": 0, "sonnet": 1, "haiku": 2}

# Providers considered "local" (free, no API key needed).
_LOCAL_PROVIDERS: frozenset[str] = frozenset({"ollama", "custom"})


def _is_cloud(model: ModelInfo) -> bool:
    """Return True if the model is from a cloud provider."""
    return model.provider not in _LOCAL_PROVIDERS


def _sort_key_for_tier(model: ModelInfo, preferred_tier: str) -> tuple[int, int, str]:
    """Sort key that prefers: exact tier match, then cloud over local, then by id.

    Returns a tuple for stable sorting:
        (tier_distance, locality, model_id)
    where tier_distance 0 = exact match, locality 0 = cloud.
    """
    model_rank = _TIER_RANK.get(model.tier, 1)
    preferred_rank = _TIER_RANK.get(preferred_tier, 1)
    tier_distance = abs(model_rank - preferred_rank)
    locality = 0 if _is_cloud(model) else 1
    # On tie: prefer higher quality (lower tier rank)
    return (tier_distance, locality, model_rank, model.id)


def compute_smart_defaults(
    available_models: list[ModelInfo],
) -> dict[str, list[str]]:
    """Compute agent-to-model assignments based on available models.

    Strategy:
    1. For each role, find the best model matching the ideal tier
    2. Add a fallback from a different provider or lower tier
    3. Prefer cloud models as primary (reliable), local as fallback (free)

    Args:
        available_models: List of enabled models the user has configured.

    Returns:
        Dict mapping role keys (like "creator-agent:execution") to
        ordered lists of model IDs [primary, fallback1, fallback2, ...].
        Empty dict if no models available.
    """
    # Filter to enabled models only.
    enabled = [m for m in available_models if m.enabled]
    if not enabled:
        return {}

    result: dict[str, list[str]] = {}

    for role, config in AGENT_ROLES.items():
        ideal_tier = config["tier"]

        # Sort all models by how well they match this role.
        ranked = sorted(enabled, key=lambda m: _sort_key_for_tier(m, ideal_tier))

        primary = ranked[0]
        models_for_role: list[str] = [primary.id]

        # Find best fallback: prefer different provider first.
        fallback = _find_fallback(ranked, primary)
        if fallback is not None:
            models_for_role.append(fallback.id)

        result[role] = models_for_role

    return result


def _find_fallback(
    ranked_models: list[ModelInfo],
    primary: ModelInfo,
) -> ModelInfo | None:
    """Find the best fallback model that differs from the primary.

    Preference order:
    1. Different provider (cross-provider diversity)
    2. Same provider, different model (different tier or variant)

    Args:
        ranked_models: Models sorted by suitability for the role.
        primary: The chosen primary model.

    Returns:
        A fallback ModelInfo, or None if only one model is available.
    """
    # First pass: different provider.
    for model in ranked_models:
        if model.id != primary.id and model.provider != primary.provider:
            return model

    # Second pass: same provider, different model.
    for model in ranked_models:
        if model.id != primary.id:
            return model

    return None
