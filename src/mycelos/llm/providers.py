"""LLM provider configuration and model discovery from LiteLLM's cost database."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


@dataclass
class ModelInfo:
    """Information about an LLM model."""

    id: str  # "claude-sonnet-4-6" or "gemini/gemini-2.5-flash"
    name: str  # "Claude Sonnet 4.6"
    tier: str  # "haiku", "sonnet", "opus"
    provider: str  # "anthropic", "openai", etc.
    input_cost_per_1k: float | None = None
    output_cost_per_1k: float | None = None
    max_context: int | None = None
    enabled: bool = True


@dataclass
class ProviderConfig:
    """Configuration for an LLM provider."""

    id: str  # "anthropic"
    name: str  # "Anthropic"
    env_var: str | None  # "ANTHROPIC_API_KEY" or None for Ollama
    requires_key: bool = True
    default_url: str | None = None  # "http://localhost:11434" for Ollama


PROVIDERS: dict[str, ProviderConfig] = {
    "anthropic": ProviderConfig(
        id="anthropic",
        name="Anthropic",
        env_var="ANTHROPIC_API_KEY",
    ),
    "openai": ProviderConfig(
        id="openai",
        name="OpenAI",
        env_var="OPENAI_API_KEY",
    ),
    "gemini": ProviderConfig(
        id="gemini",
        name="Google Gemini",
        env_var="GEMINI_API_KEY",
    ),
    "openrouter": ProviderConfig(
        id="openrouter",
        name="OpenRouter",
        env_var="OPENROUTER_API_KEY",
    ),
    "ollama": ProviderConfig(
        id="ollama",
        name="Ollama (local)",
        env_var=None,
        requires_key=False,
        default_url="http://localhost:11434",
    ),
    "custom": ProviderConfig(
        id="custom",
        name="Custom Server",
        env_var=None,
        requires_key=False,
    ),
}

# Prefixes that indicate a gateway/region variant (not a direct provider model).
_GATEWAY_PREFIXES = (
    "vertex_ai/",
    "bedrock/",
    "azure_ai/",
    "azure/",
    "sagemaker/",
    "github_copilot/",
    "heroku/",
    "vercel_ai_gateway/",
    "databricks/",
    "deepinfra/",
    "replicate/",
    "gmi/",
    "fireworks_ai/",
    "together_ai/",
    "anyscale/",
    "perplexity/",
    "groq/",
    "cerebras/",
    "friendliai/",
    "sambanova/",
    "ai21/",
    "cohere/",
    "mistral/",
    "codestral/",
    "voyage/",
    "text-completion-codestral/",
    "text-completion-openai/",
    "ft:",  # fine-tuned variants
)

_REGION_PREFIXES = re.compile(r"^(eu|us|au|jp|apac|global|ca|cn)\.")

# Model name patterns to skip (non-chat modes, special variants).
# 'container' is an OpenAI Code-Interpreter sandbox billing line, not a
# chat model — but litellm's cost map lists it without a mode, so the
# provider filter lets it through. 'codex' is a tool-use specialist
# not meant as a general chat default.
_SKIP_PATTERNS = re.compile(
    r"(realtime|audio-preview|transcribe|tts|speech|image|embed|"
    r"moderation|search-preview|computer-use|deep-research|"
    r"native-audio|rerank|completion|live-|^container$|/container$|"
    r"codex)"
)

# Tier sort order: opus first, sonnet second, haiku last.
_TIER_ORDER = {"opus": 0, "sonnet": 1, "haiku": 2}


def classify_tier(model_id: str) -> str:
    """Classify a model into a tier based on its name.

    Args:
        model_id: The model identifier string.

    Returns:
        One of "opus", "sonnet", or "haiku".
    """
    lower = model_id.lower()
    if "opus" in lower:
        return "opus"
    # Reasoning models (o1, o3, o4) are high-tier -- check before "mini"
    # since "o4-mini" should still be opus.
    if re.match(r"^o[134]", lower):
        return "opus"
    if "haiku" in lower or "mini" in lower or "flash" in lower:
        return "haiku"
    return "sonnet"  # default


def get_provider_models(provider_id: str) -> list[ModelInfo]:
    """Get available models for a provider from LiteLLM's cost database.

    For cloud providers (anthropic, openai, gemini, openrouter): filters
    litellm.model_cost to find models matching the provider.

    For ollama: returns empty list (use discover_ollama_models() instead).
    For custom: returns empty list (user configures manually).
    For unknown providers: returns empty list.

    Args:
        provider_id: One of the keys in PROVIDERS, e.g. "anthropic".

    Returns:
        List of ModelInfo sorted by tier (opus > sonnet > haiku) then name.
    """
    if provider_id not in PROVIDERS:
        return []
    if provider_id in ("ollama", "custom"):
        return []
    return _get_litellm_models(provider_id)


def _get_litellm_models(provider: str) -> list[ModelInfo]:
    """Extract models for a specific provider from litellm.model_cost.

    Filters out gateway/region variants and non-chat models.
    Groups by base model name and picks the latest version.

    Args:
        provider: Provider identifier, e.g. "anthropic".

    Returns:
        Sorted list of ModelInfo objects.
    """
    try:
        import litellm
    except ImportError:
        return []

    cost_map: dict[str, Any] = litellm.model_cost
    candidates: list[ModelInfo] = []

    for model_id, info in cost_map.items():
        # Skip gateway/region variants.
        if any(model_id.startswith(pfx) for pfx in _GATEWAY_PREFIXES):
            continue
        if _REGION_PREFIXES.match(model_id):
            continue

        # Match provider via litellm's own provider field.
        litellm_provider = info.get("litellm_provider", "")
        if litellm_provider != provider:
            continue

        # Only keep chat models (skip embeddings, image gen, TTS, etc.).
        mode = info.get("mode", "")
        if mode and mode != "chat":
            continue

        # Skip special variants (audio, realtime, search, image, etc.).
        if _SKIP_PATTERNS.search(model_id):
            continue

        # Skip the sample_spec entry.
        if model_id == "sample_spec":
            continue

        # Ensure provider prefix — litellm.model_cost uses bare IDs for some providers
        if "/" not in model_id:
            model_id = f"{provider}/{model_id}"

        tier = classify_tier(model_id)
        name = _model_id_to_display_name(model_id)
        input_cost_per_token = info.get("input_cost_per_token")
        output_cost_per_token = info.get("output_cost_per_token")

        candidates.append(
            ModelInfo(
                id=model_id,
                name=name,
                tier=tier,
                provider=provider,
                input_cost_per_1k=(
                    input_cost_per_token * 1000
                    if input_cost_per_token is not None
                    else None
                ),
                output_cost_per_1k=(
                    output_cost_per_token * 1000
                    if output_cost_per_token is not None
                    else None
                ),
                max_context=info.get("max_input_tokens"),
            )
        )

    # Filter to current-generation models only (skip legacy)
    current = _filter_current_generation(candidates, provider)

    # Deduplicate: group by base model name, keep the shortest ID
    # (which is typically the alias without date suffix).
    deduped = _deduplicate_models(current)

    # Sort by tier (opus first), then alphabetically.
    deduped.sort(key=lambda m: (_TIER_ORDER.get(m.tier, 1), m.id))
    return deduped


# Provider-specific legacy patterns. Models whose id matches are considered
# outdated enough that they would clutter the UI without giving the user
# anything they'd sensibly pick. Shared between provider listing and the
# periodic model-registry sync so both paths agree on "current".
#
# Keep the regexes conservative: if in doubt, let a model through — a stray
# older model is better than accidentally hiding a fresh one.
LEGACY_PATTERNS: dict[str, re.Pattern] = {
    "anthropic": re.compile(
        r"claude-(3|3\.5|3\.7|4-|4\.1|opus-4-1|opus-4-20|sonnet-4-20|4-opus|4-sonnet)"
    ),
    "openai": re.compile(
        r"(gpt-3|gpt-4-|gpt-4o-2024|chatgpt|gpt-4\.5)"
    ),
    "gemini": re.compile(
        r"gemini-(1|2\.0|pro$|ultra)"
    ),
}


def is_legacy_model(model_id: str, provider: str) -> bool:
    """Return True if this model is from a previous generation.

    Used to filter out outdated models from the registry. Unknown providers
    get a permissive default (nothing is legacy) so we don't accidentally
    drop models from providers we don't have a pattern for yet.
    """
    pattern = LEGACY_PATTERNS.get(provider)
    if pattern is None:
        return False
    return bool(pattern.search(model_id))


def _filter_current_generation(models: list[ModelInfo], provider: str) -> list[ModelInfo]:
    """Keep only current-generation models, filtering out legacy versions.

    For Anthropic: only Claude 4.5+ (skip Claude 3, 3.5, 3.7, 4.0, 4.1)
    For OpenAI: only GPT-4o+ and o-series (skip GPT-3.5, GPT-4-turbo, etc.)
    For Gemini: only 2.5+ (skip 1.0, 1.5, 2.0)
    """
    return [m for m in models if not is_legacy_model(m.id, provider)]


def _deduplicate_models(models: list[ModelInfo]) -> list[ModelInfo]:
    """Deduplicate model variants, keeping the canonical (shortest) ID per base name.

    For example, both "claude-sonnet-4-6" and "claude-sonnet-4-6-20260205"
    exist -- we keep only "claude-sonnet-4-6".

    For gemini, both "gemini-pro-latest" and "gemini/gemini-pro-latest" may
    exist -- we prefer the prefixed version (what litellm.completion expects).
    """
    groups: dict[str, ModelInfo] = {}
    for model in models:
        base = _base_model_name(_normalize_for_dedup(model.id))
        existing = groups.get(base)
        if existing is None:
            groups[base] = model
        elif _is_preferred_over(model, existing):
            groups[base] = model
    return list(groups.values())


def _normalize_for_dedup(model_id: str) -> str:
    """Normalize model ID for deduplication grouping.

    Strips provider prefix so "gemini/gemini-2.5-flash" and
    "gemini-2.5-flash" group together.
    """
    if "/" in model_id:
        return model_id.split("/", 1)[1]
    return model_id


def _is_preferred_over(candidate: ModelInfo, existing: ModelInfo) -> bool:
    """Determine if candidate should replace existing in dedup groups.

    Prefers: provider-prefixed IDs (litellm standard) over bare names,
    and shorter IDs (base vs dated variant).
    """
    c_has_prefix = "/" in candidate.id
    e_has_prefix = "/" in existing.id
    # Prefer prefixed (e.g., "gemini/gemini-2.5-flash" over "gemini-2.5-flash").
    if c_has_prefix and not e_has_prefix:
        return True
    if not c_has_prefix and e_has_prefix:
        return False
    # Among same prefix status, prefer shorter (base over dated).
    return len(candidate.id) < len(existing.id)


def _base_model_name(model_id: str) -> str:
    """Extract the base model name by stripping date suffixes.

    Examples:
        "claude-sonnet-4-6-20260205" -> "claude-sonnet-4-6"
        "gpt-4o-2024-11-20" -> "gpt-4o"
        "gemini/gemini-2.5-flash" -> "gemini/gemini-2.5-flash"
    """
    # Strip date suffix like -20240229, -2024-05-13.
    return re.sub(r"-\d{4}(?:-?\d{2}){1,2}$", "", model_id)


def display_name(model_id: str) -> str:
    """Convert a model ID to a human-readable display name.

    Public API — use this for all user-facing model name display.

    Examples:
        "anthropic/claude-sonnet-4-6" -> "Claude Sonnet 4.6"
        "openai/gpt-4o-mini" -> "GPT 4o Mini"
    """
    return _model_id_to_display_name(model_id)


def _model_id_to_display_name(model_id: str) -> str:
    """Convert a model ID to a human-readable display name.

    Examples:
        "claude-sonnet-4-6" -> "Claude Sonnet 4.6"
        "gpt-4o-mini" -> "GPT 4o Mini"
        "gemini/gemini-2.5-flash" -> "Gemini 2.5 Flash"
    """
    # Remove provider prefix if present (e.g., "gemini/gemini-2.5-flash").
    name = model_id
    if "/" in name:
        name = name.split("/", 1)[1]

    # Replace hyphens with spaces and title-case.
    name = name.replace("-", " ")

    # Convert version-like patterns: "4 6" -> "4.6", "2.5" stays.
    # Match sequences like "X Y" where both are single digits.
    name = re.sub(r"(\d+) (\d+)(?!\d)", r"\1.\2", name)

    parts = name.split()
    result: list[str] = []
    for part in parts:
        if part.lower() in ("gpt", "llm"):
            result.append(part.upper())
        else:
            result.append(part.capitalize())
    return " ".join(result)
