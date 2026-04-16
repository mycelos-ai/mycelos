"""Integration tests for the full NixOS-style generation lifecycle.

Tests rollback of connectors, agents, capabilities, LLM models, policies,
and credentials — including the credential rotation exception.

Maps to: docs/scenarios/config-changes/CC22-rollback-lifecycle.feature
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

from mycelos.app import App
from mycelos.config.generations import GenerationNotFoundError
from mycelos.config.state_manager import StateManager
from mycelos.storage.object_store import ObjectStore


@pytest.fixture
def app() -> App:
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["MYCELOS_MASTER_KEY"] = "test-key-lifecycle"
        a = App(Path(tmp))
        a.initialize()
        yield a


@pytest.fixture
def sm(app: App) -> StateManager:
    return app.state_manager


@pytest.fixture
def obj_store(tmp_path: Path) -> ObjectStore:
    return ObjectStore(tmp_path)


def _add_model(app: App, model_id: str, provider: str = "test", tier: str = "haiku") -> None:
    app.storage.execute(
        "INSERT OR IGNORE INTO llm_models (id, provider, tier) VALUES (?, ?, ?)",
        (model_id, provider, tier),
    )


# --- Connector rollback ---


def test_rollback_restores_connectors(app: App, sm: StateManager) -> None:
    """CC22: Rollback restores connectors."""
    gen1 = app.config.apply_from_state(sm, "empty", "test")
    app.connector_registry.register("brave", "Brave", "search", ["search.web.brave"])
    gen2 = app.config.apply_from_state(sm, "with brave", "test")
    assert app.connector_registry.get("brave") is not None
    app.config.rollback(to_generation=gen1, state_manager=sm)
    assert app.connector_registry.get("brave") is None
    caps = app.storage.fetchall(
        "SELECT * FROM connector_capabilities WHERE connector_id = ?", ("brave",)
    )
    assert caps == []


# --- Agent capabilities rollback ---


def test_rollback_restores_agent_capabilities(app: App, sm: StateManager) -> None:
    """CC22: Rollback restores agent capabilities."""
    app.agent_registry.register("a1", "news", "deterministic", ["search.web", "search.news"], "system")
    gen1 = app.config.apply_from_state(sm, "agent with 2 caps", "test")
    app.agent_registry.set_capabilities("a1", ["search.web", "search.news", "http.get"])
    gen2 = app.config.apply_from_state(sm, "added http.get", "test")
    app.config.rollback(to_generation=gen1, state_manager=sm)
    agent = app.agent_registry.get("a1")
    assert agent is not None
    assert set(agent["capabilities"]) == {"search.web", "search.news"}


# --- Agent code rollback via Object Store ---


def test_rollback_restores_agent_code_hashes(app: App, sm: StateManager, obj_store: ObjectStore) -> None:
    """CC22: Rollback restores agent code via Object Store hashes."""
    app.agent_registry.register("a1", "agent", "deterministic", [], "system")
    app.agent_registry.save_code("a1", "v1 code", "v1 tests", "v1 prompt", obj_store)
    gen1 = app.config.apply_from_state(sm, "v1 code", "test")
    v1_hash = app.agent_registry.get("a1")["code_hash"]

    app.agent_registry.save_code("a1", "v2 code", "v2 tests", "v2 prompt", obj_store)
    gen2 = app.config.apply_from_state(sm, "v2 code", "test")
    v2_hash = app.agent_registry.get("a1")["code_hash"]

    assert v1_hash != v2_hash
    app.config.rollback(to_generation=gen1, state_manager=sm)
    assert app.agent_registry.get("a1")["code_hash"] == v1_hash
    # Both versions still in Object Store (immutable)
    assert obj_store.exists(v1_hash)
    assert obj_store.exists(v2_hash)


# --- LLM model assignment rollback ---


def test_rollback_restores_agent_llm_models(app: App, sm: StateManager) -> None:
    """CC22: Rollback restores agent LLM assignments."""
    app.agent_registry.register("a1", "agent", "deterministic", [], "system")
    _add_model(app, "m1")
    _add_model(app, "m2")
    _add_model(app, "m3")
    app.agent_registry.set_models("a1", ["m1", "m2"], "execution")
    gen1 = app.config.apply_from_state(sm, "m1+m2", "test")

    app.agent_registry.set_models("a1", ["m3"], "execution")
    gen2 = app.config.apply_from_state(sm, "m3 only", "test")

    app.config.rollback(to_generation=gen1, state_manager=sm)
    models = app.agent_registry.get_models("a1", "execution")
    assert models == ["m1", "m2"]


# --- System LLM defaults rollback ---


def test_rollback_restores_system_llm_defaults(app: App, sm: StateManager) -> None:
    """CC22: Rollback restores system LLM defaults."""
    _add_model(app, "m1")
    _add_model(app, "m2")
    _add_model(app, "m3")
    app.model_registry.set_system_defaults({"execution": ["m1", "m2"]})
    gen1 = app.config.apply_from_state(sm, "system m1+m2", "test")

    app.model_registry.set_system_defaults({"execution": ["m3"]})
    gen2 = app.config.apply_from_state(sm, "system m3", "test")

    app.config.rollback(to_generation=gen1, state_manager=sm)
    models = app.model_registry.resolve_models(None, "execution")
    assert models == ["m1", "m2"]


# --- Policy rollback ---


def test_rollback_restores_policies(app: App, sm: StateManager) -> None:
    """CC22: Rollback restores policies."""
    app.policy_engine.set_policy("default", None, "search.web", "always")
    gen1 = app.config.apply_from_state(sm, "always", "test")
    app.policy_engine.set_policy("default", None, "search.web", "confirm")
    gen2 = app.config.apply_from_state(sm, "confirm", "test")
    app.config.rollback(to_generation=gen1, state_manager=sm)
    decision = app.policy_engine.evaluate("default", None, "search.web")
    assert decision == "always"


# --- Credential rotation exception ---


def test_rollback_skips_rotated_credentials(app: App, sm: StateManager) -> None:
    """CC22: Rollback skips rotated credentials."""
    app.credentials.store_credential("svc", {"api_key": "old"})
    gen1 = app.config.apply_from_state(sm, "old key", "test")
    # Simulate rotation
    app.storage.execute(
        "UPDATE credentials SET security_rotated = 1 WHERE service = ?", ("svc",)
    )
    gen2 = app.config.apply_from_state(sm, "rotated key", "test")
    app.config.rollback(to_generation=gen1, state_manager=sm)
    # Rotated credential should NOT be reverted
    row = app.storage.fetchone(
        "SELECT security_rotated FROM credentials WHERE service = ?", ("svc",)
    )
    assert row["security_rotated"] == 1


# --- Dedup ---


def test_dedup_prevents_duplicate_generations(app: App, sm: StateManager) -> None:
    """CC22: Dedup prevents unnecessary generations."""
    gen1 = app.config.apply_from_state(sm, "first", "test")
    gen2 = app.config.apply_from_state(sm, "second", "test")
    assert gen1 == gen2  # Same hash -> reuse


# --- Error handling ---


def test_rollback_to_nonexistent_generation_fails(app: App, sm: StateManager) -> None:
    """CC22: Rollback to non-existent generation fails gracefully."""
    app.config.apply_from_state(sm, "initial", "test")
    with pytest.raises(GenerationNotFoundError):
        app.config.rollback(to_generation=999, state_manager=sm)


# --- Full round-trip ---


def test_full_round_trip(app: App, sm: StateManager, obj_store: ObjectStore) -> None:
    """CC22: Complete lifecycle: setup -> modify -> rollback -> verify."""
    # Setup: connector + agent + policy + model
    app.connector_registry.register("ddg", "DDG", "search", ["search.web"])
    app.agent_registry.register("a1", "search-agent", "deterministic", ["search.web"], "system")
    app.agent_registry.save_code("a1", "v1", None, None, obj_store)
    app.policy_engine.set_policy("default", None, "search.web", "always")
    _add_model(app, "m1")
    app.model_registry.set_system_defaults({"execution": ["m1"]})

    gen_base = app.config.apply_from_state(sm, "base state", "test")

    # Modify everything
    app.connector_registry.register("brave", "Brave", "search", ["search.brave"])
    app.agent_registry.set_capabilities("a1", ["search.web", "http.get"])
    app.agent_registry.save_code("a1", "v2", None, None, obj_store)
    app.policy_engine.set_policy("default", None, "search.web", "confirm")
    _add_model(app, "m2")
    app.model_registry.set_system_defaults({"execution": ["m2"]})

    gen_modified = app.config.apply_from_state(sm, "modified", "test")
    assert gen_base != gen_modified

    # Rollback
    app.config.rollback(to_generation=gen_base, state_manager=sm)

    # Verify everything restored
    assert app.connector_registry.get("ddg") is not None
    assert app.connector_registry.get("brave") is None
    agent = app.agent_registry.get("a1")
    assert agent is not None
    assert agent["capabilities"] == ["search.web"]
    assert app.model_registry.resolve_models(None, "execution") == ["m1"]
    # Object Store still has both versions
    assert obj_store.exists(agent["code_hash"])


# --- Model resolution (not rollback, but part of CC22 scenarios) ---


def test_agent_models_override_system_defaults(app: App, sm: StateManager) -> None:
    """CC22: Agent-specific models override system defaults."""
    app.agent_registry.register("a1", "agent", "deterministic", [], "system")
    _add_model(app, "sys-model")
    _add_model(app, "agent-model")
    app.model_registry.set_system_defaults({"execution": ["sys-model"]})
    app.agent_registry.set_models("a1", ["agent-model"], "execution")
    models = app.model_registry.resolve_models("a1", "execution")
    assert models == ["agent-model"]


def test_agent_without_models_uses_system_defaults(app: App, sm: StateManager) -> None:
    """CC22: Agent without own models falls back to system defaults."""
    app.agent_registry.register("a1", "agent", "deterministic", [], "system")
    _add_model(app, "sys-model")
    app.model_registry.set_system_defaults({"execution": ["sys-model"]})
    models = app.model_registry.resolve_models("a1", "execution")
    assert models == ["sys-model"]
