"""Tests for LLM Model Registry -- model CRUD, resolution, failover."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from mycelos.llm.model_registry import ModelRegistry
from mycelos.storage.database import SQLiteStorage


@pytest.fixture
def storage() -> SQLiteStorage:
    with tempfile.TemporaryDirectory() as tmp:
        s = SQLiteStorage(Path(tmp) / "test.db")
        s.initialize()
        yield s


@pytest.fixture
def registry(storage: SQLiteStorage) -> ModelRegistry:
    return ModelRegistry(storage)


def test_add_model(registry: ModelRegistry) -> None:
    registry.add_model(
        model_id="anthropic/claude-sonnet-4-6",
        provider="anthropic",
        tier="sonnet",
        input_cost_per_1k=0.003,
        output_cost_per_1k=0.015,
        max_context=200000,
    )
    m = registry.get_model("anthropic/claude-sonnet-4-6")
    assert m is not None
    assert m["provider"] == "anthropic"
    assert m["tier"] == "sonnet"
    assert m["input_cost_per_1k"] == 0.003


def test_get_model_nonexistent(registry: ModelRegistry) -> None:
    assert registry.get_model("nope") is None


def test_list_models(registry: ModelRegistry) -> None:
    registry.add_model("m1", "anthropic", "haiku")
    registry.add_model("m2", "openai", "sonnet")
    models = registry.list_models()
    assert len(models) == 2


def test_list_models_by_provider(registry: ModelRegistry) -> None:
    registry.add_model("m1", "anthropic", "haiku")
    registry.add_model("m2", "openai", "sonnet")
    result = registry.list_models(provider="anthropic")
    assert len(result) == 1
    assert result[0]["id"] == "m1"


def test_list_models_by_tier(registry: ModelRegistry) -> None:
    registry.add_model("m1", "anthropic", "haiku")
    registry.add_model("m2", "anthropic", "sonnet")
    result = registry.list_models(tier="haiku")
    assert len(result) == 1


def test_set_system_defaults(registry: ModelRegistry) -> None:
    registry.add_model("m1", "anthropic", "sonnet")
    registry.add_model("m2", "anthropic", "haiku")
    registry.set_system_defaults({"execution": ["m1", "m2"], "classification": ["m2"]})
    exec_models = registry.resolve_models(None, "execution")
    assert exec_models == ["m1", "m2"]
    class_models = registry.resolve_models(None, "classification")
    assert class_models == ["m2"]


def test_set_agent_models(registry: ModelRegistry, storage: SQLiteStorage) -> None:
    storage.execute(
        "INSERT INTO agents (id, name, agent_type, created_by) VALUES (?, ?, ?, ?)",
        ("a1", "test", "deterministic", "system"),
    )
    registry.add_model("m1", "anthropic", "haiku")
    registry.add_model("m2", "ollama", "sonnet")
    registry.set_agent_models("a1", ["m1", "m2"], "execution")
    models = registry.resolve_models("a1", "execution")
    assert models == ["m1", "m2"]


def test_resolve_agent_overrides_system(
    registry: ModelRegistry, storage: SQLiteStorage
) -> None:
    """Agent-specific models take priority over system defaults."""
    storage.execute(
        "INSERT INTO agents (id, name, agent_type, created_by) VALUES (?, ?, ?, ?)",
        ("a1", "test", "deterministic", "system"),
    )
    registry.add_model("sys-model", "anthropic", "sonnet")
    registry.add_model("agent-model", "anthropic", "haiku")
    registry.set_system_defaults({"execution": ["sys-model"]})
    registry.set_agent_models("a1", ["agent-model"], "execution")
    models = registry.resolve_models("a1", "execution")
    assert models == ["agent-model"]


def test_resolve_falls_back_to_system(
    registry: ModelRegistry, storage: SQLiteStorage
) -> None:
    """Agent with no models falls back to system defaults."""
    storage.execute(
        "INSERT INTO agents (id, name, agent_type, created_by) VALUES (?, ?, ?, ?)",
        ("a1", "test", "deterministic", "system"),
    )
    registry.add_model("sys-model", "anthropic", "sonnet")
    registry.set_system_defaults({"execution": ["sys-model"]})
    models = registry.resolve_models("a1", "execution")
    assert models == ["sys-model"]


def test_resolve_empty_when_nothing_configured(
    registry: ModelRegistry, storage: SQLiteStorage
) -> None:
    storage.execute(
        "INSERT INTO agents (id, name, agent_type, created_by) VALUES (?, ?, ?, ?)",
        ("a1", "test", "deterministic", "system"),
    )
    models = registry.resolve_models("a1", "execution")
    assert models == []


def test_sync_from_litellm_populates_models(registry: ModelRegistry) -> None:
    """sync_from_litellm should populate llm_models table."""
    result = registry.sync_from_litellm()
    assert result["total"] > 0
    assert len(result["added"]) > 0
    # Should have some Claude models
    models = registry.list_models(provider="anthropic")
    assert len(models) > 0


def test_sync_from_litellm_reports_added_vs_updated(registry: ModelRegistry) -> None:
    """Second call reports zero added and all models as updated."""
    first = registry.sync_from_litellm()
    assert len(first["added"]) > 0
    second = registry.sync_from_litellm()
    assert second["added"] == []  # nothing new
    assert len(second["updated"]) == len(first["added"])


def test_sync_from_litellm_skips_legacy_generations(registry: ModelRegistry) -> None:
    """Previous-generation models (Claude 3.x, GPT-3, Gemini 1/2.0) must NOT
    be added on a fresh sync — they would clutter the registry without
    being something a user would sensibly pick today."""
    result = registry.sync_from_litellm()
    # Nothing matching Claude 3 / 3.5 / 3.7 / 4.0 / 4.1 should be in the
    # registry after the sync.
    anthropic_ids = {m["id"] for m in registry.list_models(provider="anthropic")}
    for legacy in ("anthropic/claude-3-opus-20240229", "anthropic/claude-3-7-sonnet-20250219"):
        assert legacy not in anthropic_ids
    # But skipped_legacy should report what was filtered.
    assert len(result["skipped_legacy"]) > 0


def test_sync_from_litellm_include_legacy_escape_hatch(registry: ModelRegistry) -> None:
    """include_legacy=True still adds old models for explicit use."""
    result = registry.sync_from_litellm(include_legacy=True)
    # Legacy models now appear
    assert result["skipped_legacy"] == []


def test_sync_from_litellm_preserves_existing_legacy_entry(registry: ModelRegistry) -> None:
    """If a legacy model is ALREADY in the registry (user added it manually
    or an older sync seeded it), the default sync must leave it alone —
    the skip only applies to fresh additions."""
    # Seed with a legacy id
    registry.add_model(
        model_id="anthropic/claude-3-opus-20240229",
        provider="anthropic",
        tier="opus",
    )
    assert registry.get_model("anthropic/claude-3-opus-20240229") is not None
    registry.sync_from_litellm()
    # Still there — not deleted by the sync
    assert registry.get_model("anthropic/claude-3-opus-20240229") is not None


def test_sync_from_litellm_prefer_remote_falls_back_on_error(
    registry: ModelRegistry, monkeypatch
) -> None:
    """When the remote fetch fails, fall back to bundled map and still sync."""
    # Make httpx.get raise so the remote path fails
    import httpx
    def boom(*a, **kw):
        raise httpx.RequestError("no network")
    monkeypatch.setattr(httpx, "get", boom)
    result = registry.sync_from_litellm(prefer_remote=True)
    # Fallback still works; we get models from the bundled litellm.model_cost
    assert result["total"] > 0


def test_setup_smart_defaults_anthropic(
    registry: ModelRegistry, storage: SQLiteStorage
) -> None:
    """Smart defaults for Anthropic provider."""
    registry.sync_from_litellm()
    registry.setup_smart_defaults("anthropic")
    # System execution should have models
    sys_exec = registry.resolve_models(None, "execution")
    assert len(sys_exec) >= 1
    # Classification should use cheap model
    sys_class = registry.resolve_models(None, "classification")
    assert len(sys_class) >= 1
