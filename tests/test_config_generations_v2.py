"""Tests for ConfigGenerationManager V2 — state-aware apply and rollback."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from mycelos.config.generations import ConfigGenerationManager
from mycelos.config.state_manager import StateManager
from mycelos.storage.database import SQLiteStorage


@pytest.fixture
def storage() -> SQLiteStorage:
    with tempfile.TemporaryDirectory() as tmp:
        s = SQLiteStorage(Path(tmp) / "test.db")
        s.initialize()
        yield s


@pytest.fixture
def state_mgr(storage: SQLiteStorage) -> StateManager:
    return StateManager(storage)


@pytest.fixture
def config_mgr(storage: SQLiteStorage) -> ConfigGenerationManager:
    return ConfigGenerationManager(storage)


def test_apply_from_state_creates_generation(config_mgr, state_mgr, storage):
    """apply_from_state should create a new generation from current DB state."""
    # Set up some state
    storage.execute(
        "INSERT INTO connectors (id, name, connector_type) VALUES (?, ?, ?)",
        ("ddg", "DuckDuckGo", "search"),
    )
    storage.execute(
        "INSERT INTO connector_capabilities (connector_id, capability) VALUES (?, ?)",
        ("ddg", "search.web"),
    )

    gen_id = config_mgr.apply_from_state(
        state_manager=state_mgr,
        description="Added DuckDuckGo connector",
        trigger="connector_setup",
    )

    assert gen_id is not None
    # The snapshot should contain the connector
    import json
    row = storage.fetchone("SELECT config_snapshot FROM config_generations WHERE id = ?", (gen_id,))
    snapshot = json.loads(row["config_snapshot"])
    assert "ddg" in snapshot["connectors"]


def test_apply_from_state_deduplicates(config_mgr, state_mgr, storage):
    """Same state should not create duplicate generations."""
    gen1 = config_mgr.apply_from_state(state_mgr, "first", "test")
    gen2 = config_mgr.apply_from_state(state_mgr, "second", "test")
    assert gen1 == gen2  # Same content hash -> reuse


def test_apply_from_state_detects_changes(config_mgr, state_mgr, storage):
    """Different state should create new generations."""
    gen1 = config_mgr.apply_from_state(state_mgr, "empty", "test")
    storage.execute(
        "INSERT INTO connectors (id, name, connector_type) VALUES (?, ?, ?)",
        ("ddg", "DDG", "search"),
    )
    gen2 = config_mgr.apply_from_state(state_mgr, "with connector", "test")
    assert gen1 != gen2


def test_rollback_restores_state(config_mgr, state_mgr, storage):
    """Rollback should restore live tables to the target generation's state."""
    # Gen 1: empty
    gen1 = config_mgr.apply_from_state(state_mgr, "empty", "test")

    # Gen 2: with connector
    storage.execute(
        "INSERT INTO connectors (id, name, connector_type) VALUES (?, ?, ?)",
        ("ddg", "DDG", "search"),
    )
    storage.execute(
        "INSERT INTO connector_capabilities (connector_id, capability) VALUES (?, ?)",
        ("ddg", "search.web"),
    )
    gen2 = config_mgr.apply_from_state(state_mgr, "added connector", "test")

    # Verify connector exists
    assert storage.fetchone("SELECT * FROM connectors WHERE id = ?", ("ddg",)) is not None

    # Rollback to Gen 1
    config_mgr.rollback(to_generation=gen1, state_manager=state_mgr)

    # Connector should be gone
    assert storage.fetchone("SELECT * FROM connectors WHERE id = ?", ("ddg",)) is None
    assert storage.fetchall("SELECT * FROM connector_capabilities") == []


def test_rollback_restores_agents_and_capabilities(config_mgr, state_mgr, storage):
    """Rollback should restore agents with their capabilities."""
    # Gen 1: agent with cap.a
    storage.execute(
        "INSERT INTO agents (id, name, agent_type, created_by) VALUES (?, ?, ?, ?)",
        ("a1", "agent1", "deterministic", "system"),
    )
    storage.execute(
        "INSERT INTO agent_capabilities (agent_id, capability) VALUES (?, ?)",
        ("a1", "cap.a"),
    )
    gen1 = config_mgr.apply_from_state(state_mgr, "agent with cap.a", "test")

    # Gen 2: add cap.b
    storage.execute(
        "INSERT INTO agent_capabilities (agent_id, capability) VALUES (?, ?)",
        ("a1", "cap.b"),
    )
    gen2 = config_mgr.apply_from_state(state_mgr, "added cap.b", "test")

    # Rollback to Gen 1
    config_mgr.rollback(to_generation=gen1, state_manager=state_mgr)

    # Should only have cap.a
    caps = storage.fetchall(
        "SELECT capability FROM agent_capabilities WHERE agent_id = ?", ("a1",)
    )
    assert [c["capability"] for c in caps] == ["cap.a"]


def test_rollback_restores_policies(config_mgr, state_mgr, storage):
    # Gen 1: policy always
    storage.execute(
        "INSERT INTO policies (id, user_id, resource, decision) VALUES (?, ?, ?, ?)",
        ("p1", "default", "search.web", "always"),
    )
    gen1 = config_mgr.apply_from_state(state_mgr, "always policy", "test")

    # Gen 2: change to confirm
    storage.execute(
        "UPDATE policies SET decision = ? WHERE id = ?", ("confirm", "p1"),
    )
    gen2 = config_mgr.apply_from_state(state_mgr, "confirm policy", "test")

    # Rollback
    config_mgr.rollback(to_generation=gen1, state_manager=state_mgr)

    row = storage.fetchone("SELECT decision FROM policies WHERE resource = ?", ("search.web",))
    assert row["decision"] == "always"


def test_rollback_without_state_manager_only_swaps_pointer(config_mgr, storage):
    """Original rollback (no state_manager) should still work — just pointer swap."""
    gen1 = config_mgr.apply({"version": "1"}, "v1", "test")
    gen2 = config_mgr.apply({"version": "2"}, "v2", "test")
    assert config_mgr.get_active_generation_id() == gen2
    config_mgr.rollback(to_generation=gen1)
    assert config_mgr.get_active_generation_id() == gen1


def test_old_apply_still_works(config_mgr, storage):
    """Original apply(config) should still work for simple config changes."""
    gen_id = config_mgr.apply({"provider": "anthropic"}, "test", "manual")
    config = config_mgr.get_active_config()
    assert config["provider"] == "anthropic"
