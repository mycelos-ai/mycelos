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
    count = registry.sync_from_litellm()
    assert count > 0
    # Should have some Claude models
    models = registry.list_models(provider="anthropic")
    assert len(models) > 0


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
