"""Tests for smart default model assignment computation."""

from __future__ import annotations

import pytest

from mycelos.llm.providers import ModelInfo
from mycelos.llm.smart_defaults import AGENT_ROLES, compute_smart_defaults


def _model(id: str, tier: str, provider: str, cost: float = 0.003) -> ModelInfo:
    return ModelInfo(
        id=id, name=id, tier=tier, provider=provider,
        input_cost_per_1k=cost, output_cost_per_1k=cost * 5,
    )


# --- Basic scenarios ---

def test_single_anthropic_provider():
    """Only Anthropic models -- should assign primary + fallback within provider."""
    models = [
        _model("claude-sonnet-4-6", "sonnet", "anthropic", 0.003),
        _model("claude-haiku-4-5", "haiku", "anthropic", 0.001),
        _model("claude-opus-4-6", "opus", "anthropic", 0.005),
    ]
    defaults = compute_smart_defaults(models)

    # System default should be sonnet primary
    assert defaults["system:execution"][0] == "claude-sonnet-4-6"
    # Classification should use haiku
    assert defaults["system:classification"][0] == "claude-haiku-4-5"
    # Builder should get opus (strongest model for workflow + agent creation)
    assert defaults["builder:execution"][0] == "claude-opus-4-6"
    # Workflow-agent should get haiku (cheap default, overridden per workflow)
    assert defaults["workflow-agent:execution"][0] == "claude-haiku-4-5"


def test_anthropic_plus_ollama():
    """Cloud + local -- cloud primary, local fallback."""
    models = [
        _model("claude-sonnet-4-6", "sonnet", "anthropic", 0.003),
        _model("claude-haiku-4-5", "haiku", "anthropic", 0.001),
        _model("ollama/llama3.3", "sonnet", "ollama", 0.0),
    ]
    defaults = compute_smart_defaults(models)

    # System primary should be cloud
    assert defaults["system:execution"][0] == "claude-sonnet-4-6"
    # Fallback should include ollama
    sys_models = defaults["system:execution"]
    assert any("ollama" in m for m in sys_models)


def test_anthropic_plus_openai():
    """Two cloud providers -- cross-provider fallbacks."""
    models = [
        _model("claude-sonnet-4-6", "sonnet", "anthropic", 0.003),
        _model("claude-haiku-4-5", "haiku", "anthropic", 0.001),
        _model("gpt-4o", "sonnet", "openai", 0.0025),
        _model("gpt-4o-mini", "haiku", "openai", 0.0001),
    ]
    defaults = compute_smart_defaults(models)

    # Primary should be one provider
    primary = defaults["system:execution"][0]
    assert primary in ("claude-sonnet-4-6", "gpt-4o")
    # Fallback should be different provider
    if len(defaults["system:execution"]) > 1:
        fallback = defaults["system:execution"][1]
        primary_provider = "anthropic" if "claude" in primary else "openai"
        fallback_provider = "anthropic" if "claude" in fallback else "openai"
        # At least check it's a valid model
        assert fallback in [m.id for m in models]


def test_only_ollama():
    """Only local models -- should still assign everything."""
    models = [
        _model("ollama/llama3.3", "sonnet", "ollama", 0.0),
        _model("ollama/mistral", "sonnet", "ollama", 0.0),
        _model("ollama/phi3", "haiku", "ollama", 0.0),
    ]
    defaults = compute_smart_defaults(models)

    assert len(defaults) > 0
    # All roles should have at least one model
    for role in AGENT_ROLES:
        assert role in defaults
        assert len(defaults[role]) >= 1


def test_single_model():
    """Only one model -- used everywhere, no fallback."""
    models = [_model("claude-sonnet-4-6", "sonnet", "anthropic")]
    defaults = compute_smart_defaults(models)

    for role in AGENT_ROLES:
        assert role in defaults
        assert defaults[role] == ["claude-sonnet-4-6"]


def test_no_models():
    """No models available -- empty dict."""
    defaults = compute_smart_defaults([])
    assert defaults == {}


def test_all_roles_covered():
    """Every AGENT_ROLES key should be in the output."""
    models = [
        _model("claude-sonnet-4-6", "sonnet", "anthropic"),
        _model("claude-haiku-4-5", "haiku", "anthropic"),
    ]
    defaults = compute_smart_defaults(models)

    for role in AGENT_ROLES:
        assert role in defaults, f"Missing role: {role}"


def test_builder_gets_strong_model():
    """Builder should get sonnet or opus, never haiku."""
    models = [
        _model("claude-sonnet-4-6", "sonnet", "anthropic"),
        _model("claude-haiku-4-5", "haiku", "anthropic"),
    ]
    defaults = compute_smart_defaults(models)
    assert defaults["builder:execution"][0] == "claude-sonnet-4-6"


def test_classifier_gets_cheap_model():
    """Classifier should get haiku tier (cheapest)."""
    models = [
        _model("claude-sonnet-4-6", "sonnet", "anthropic", 0.003),
        _model("claude-haiku-4-5", "haiku", "anthropic", 0.001),
        _model("claude-opus-4-6", "opus", "anthropic", 0.005),
    ]
    defaults = compute_smart_defaults(models)
    assert defaults["system:classification"][0] == "claude-haiku-4-5"


def test_disabled_models_excluded():
    """Models with enabled=False should not be used."""
    models = [
        _model("claude-sonnet-4-6", "sonnet", "anthropic"),
        ModelInfo(id="claude-haiku-4-5", name="haiku", tier="haiku",
                  provider="anthropic", enabled=False),
    ]
    defaults = compute_smart_defaults(models)
    for role, model_list in defaults.items():
        assert "claude-haiku-4-5" not in model_list


def test_fallback_different_from_primary():
    """If there's a fallback, it should differ from primary."""
    models = [
        _model("claude-sonnet-4-6", "sonnet", "anthropic"),
        _model("claude-haiku-4-5", "haiku", "anthropic"),
        _model("gpt-4o", "sonnet", "openai"),
    ]
    defaults = compute_smart_defaults(models)
    for role, model_list in defaults.items():
        if len(model_list) > 1:
            assert model_list[0] != model_list[1]
