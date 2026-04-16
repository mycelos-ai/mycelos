"""Tests for LLM provider configuration and model discovery."""

from __future__ import annotations

import pytest

from mycelos.llm.providers import (
    PROVIDERS,
    ModelInfo,
    ProviderConfig,
    classify_tier,
    get_provider_models,
    _base_model_name,
    _model_id_to_display_name,
)


# ---------------------------------------------------------------------------
# Provider registry tests
# ---------------------------------------------------------------------------


def test_providers_dict_has_all_providers() -> None:
    assert "anthropic" in PROVIDERS
    assert "openai" in PROVIDERS
    assert "gemini" in PROVIDERS
    assert "ollama" in PROVIDERS
    assert "openrouter" in PROVIDERS
    assert "custom" in PROVIDERS


def test_anthropic_provider_config() -> None:
    p = PROVIDERS["anthropic"]
    assert p.env_var == "ANTHROPIC_API_KEY"
    assert p.requires_key is True
    assert p.name == "Anthropic"


def test_openai_provider_config() -> None:
    p = PROVIDERS["openai"]
    assert p.env_var == "OPENAI_API_KEY"
    assert p.requires_key is True


def test_gemini_provider_config() -> None:
    p = PROVIDERS["gemini"]
    assert p.env_var == "GEMINI_API_KEY"
    assert p.requires_key is True
    assert p.name == "Google Gemini"


def test_ollama_provider_config() -> None:
    p = PROVIDERS["ollama"]
    assert p.env_var is None
    assert p.requires_key is False
    assert p.default_url == "http://localhost:11434"


def test_custom_provider_config() -> None:
    p = PROVIDERS["custom"]
    assert p.env_var is None
    assert p.requires_key is False
    assert p.default_url is None


def test_openrouter_provider_config() -> None:
    p = PROVIDERS["openrouter"]
    assert p.env_var == "OPENROUTER_API_KEY"
    assert p.requires_key is True


# ---------------------------------------------------------------------------
# Model discovery: Anthropic
# ---------------------------------------------------------------------------


def test_get_anthropic_models() -> None:
    models = get_provider_models("anthropic")
    assert len(models) > 0
    model_ids = [m.id for m in models]
    assert any("claude" in m for m in model_ids)
    assert all(m.provider == "anthropic" for m in models)


def test_anthropic_models_sorted_by_tier() -> None:
    models = get_provider_models("anthropic")
    tiers = [m.tier for m in models]
    # Opus should come before sonnet, sonnet before haiku.
    tier_order = {"opus": 0, "sonnet": 1, "haiku": 2}
    tier_indices = [tier_order[t] for t in tiers]
    assert tier_indices == sorted(tier_indices)


# ---------------------------------------------------------------------------
# Model discovery: OpenAI
# ---------------------------------------------------------------------------


def test_get_openai_models() -> None:
    models = get_provider_models("openai")
    assert len(models) > 0
    model_ids = [m.id for m in models]
    assert any("gpt" in m or "o4" in m or "o1" in m for m in model_ids)


# ---------------------------------------------------------------------------
# Model discovery: Gemini
# ---------------------------------------------------------------------------


def test_get_gemini_models() -> None:
    models = get_provider_models("gemini")
    assert len(models) > 0
    model_ids = [m.id for m in models]
    assert any("gemini" in m for m in model_ids)


# ---------------------------------------------------------------------------
# Model discovery: edge cases
# ---------------------------------------------------------------------------


def test_get_ollama_models_returns_empty() -> None:
    """Ollama needs discover_ollama_models(), not get_provider_models()."""
    models = get_provider_models("ollama")
    assert models == []


def test_get_custom_models_returns_empty() -> None:
    models = get_provider_models("custom")
    assert models == []


def test_get_unknown_provider_returns_empty() -> None:
    models = get_provider_models("nonexistent")
    assert models == []


# ---------------------------------------------------------------------------
# Cost and context metadata
# ---------------------------------------------------------------------------


def test_models_have_cost_info() -> None:
    """Cloud provider models should have cost information from LiteLLM."""
    models = get_provider_models("anthropic")
    for m in models:
        if m.input_cost_per_1k is not None:
            assert m.input_cost_per_1k >= 0
            assert m.output_cost_per_1k is not None
            assert m.output_cost_per_1k >= 0
            break
    else:
        pytest.fail("No models had cost info")


def test_models_have_context_info() -> None:
    models = get_provider_models("anthropic")
    for m in models:
        if m.max_context is not None:
            assert m.max_context > 0
            break
    else:
        pytest.fail("No models had context info")


# ---------------------------------------------------------------------------
# Tier classification
# ---------------------------------------------------------------------------


def test_classify_tier() -> None:
    assert classify_tier("claude-opus-4-6") == "opus"
    assert classify_tier("claude-sonnet-4-6") == "sonnet"
    assert classify_tier("claude-haiku-4-5") == "haiku"
    assert classify_tier("gpt-4o-mini") == "haiku"
    assert classify_tier("gpt-4o") == "sonnet"
    assert classify_tier("gemini-2.5-flash") == "haiku"
    assert classify_tier("o4-mini") == "opus"  # reasoning > mini


def test_classify_tier_reasoning_models() -> None:
    assert classify_tier("o1") == "opus"
    assert classify_tier("o3") == "opus"
    assert classify_tier("o3-pro") == "opus"


def test_classify_tier_defaults_to_sonnet() -> None:
    assert classify_tier("some-unknown-model") == "sonnet"


# ---------------------------------------------------------------------------
# Gateway / region filtering
# ---------------------------------------------------------------------------


def test_no_gateway_models_in_results() -> None:
    """Should not include vertex_ai/, bedrock/, etc. variants."""
    models = get_provider_models("anthropic")
    for m in models:
        assert not m.id.startswith("vertex_ai/")
        assert not m.id.startswith("bedrock/")
        assert not m.id.startswith("eu.")
        assert not m.id.startswith("us.")
        assert not m.id.startswith("azure/")


def test_no_gateway_models_for_openai() -> None:
    models = get_provider_models("openai")
    for m in models:
        assert not m.id.startswith("ft:")
        assert not m.id.startswith("azure/")


def test_no_gateway_models_for_gemini() -> None:
    models = get_provider_models("gemini")
    for m in models:
        assert not m.id.startswith("vertex_ai/")


# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------


def test_no_dated_duplicates() -> None:
    """Dated variants like claude-sonnet-4-6-20260205 should be deduplicated."""
    models = get_provider_models("anthropic")
    model_ids = [m.id for m in models]
    # Should not have both the base and dated version.
    for mid in model_ids:
        # If a model ends with a date pattern, the base should not also be present.
        import re

        if re.search(r"-\d{8}$", mid):
            base = re.sub(r"-\d{8}$", "", mid)
            assert base not in model_ids, (
                f"Both {mid} and {base} present -- should be deduplicated"
            )


# ---------------------------------------------------------------------------
# Display name generation
# ---------------------------------------------------------------------------


def test_model_id_to_display_name() -> None:
    assert _model_id_to_display_name("claude-sonnet-4-6") == "Claude Sonnet 4.6"
    assert _model_id_to_display_name("gpt-4o") == "GPT 4o"
    assert _model_id_to_display_name("gemini/gemini-2.5-flash") == "Gemini 2.5 Flash"


def test_base_model_name_strips_dates() -> None:
    assert _base_model_name("claude-sonnet-4-6-20260205") == "claude-sonnet-4-6"
    assert _base_model_name("gpt-4o-2024-11-20") == "gpt-4o"
    assert _base_model_name("claude-haiku-4-5") == "claude-haiku-4-5"
    assert _base_model_name("gemini/gemini-2.5-flash") == "gemini/gemini-2.5-flash"


# ---------------------------------------------------------------------------
# Dataclass construction
# ---------------------------------------------------------------------------


def test_model_info_defaults() -> None:
    m = ModelInfo(id="test", name="Test", tier="sonnet", provider="test")
    assert m.enabled is True
    assert m.input_cost_per_1k is None
    assert m.output_cost_per_1k is None
    assert m.max_context is None


def test_provider_config_defaults() -> None:
    p = ProviderConfig(id="test", name="Test", env_var="TEST_KEY")
    assert p.requires_key is True
    assert p.default_url is None
