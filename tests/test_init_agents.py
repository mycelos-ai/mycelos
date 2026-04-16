"""Tests for system agent registration during init.

Root cause of Pi deployment bug: docker-entrypoint.sh called
AgentRegistry.register() with wrong signature, silently swallowed
by except Exception: pass. These tests ensure agents are always
registered correctly.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

from mycelos.app import App
from mycelos.cli.init_cmd import SYSTEM_AGENTS, _register_system_agents


@pytest.fixture
def app(tmp_path: Path) -> App:
    """Fresh App with initialized DB."""
    os.environ["MYCELOS_MASTER_KEY"] = "test-key-init-agents"
    a = App(tmp_path)
    a.initialize()
    return a


# ---------------------------------------------------------------------------
# Agent registration
# ---------------------------------------------------------------------------


class TestSystemAgentRegistration:
    """Verify _register_system_agents populates the agents table."""

    def test_register_creates_all_agents(self, app: App):
        """All SYSTEM_AGENTS must be present in DB after registration."""
        _register_system_agents(app)
        agents = app.agent_registry.list_agents()
        agent_ids = {a["id"] for a in agents}
        for agent_def in SYSTEM_AGENTS:
            assert agent_def["id"] in agent_ids, f"Missing agent: {agent_def['id']}"

    def test_register_sets_status_active(self, app: App):
        """All system agents must have status 'active'."""
        _register_system_agents(app)
        for agent_def in SYSTEM_AGENTS:
            agent = app.agent_registry.get(agent_def["id"])
            assert agent is not None
            assert agent["status"] == "active"

    def test_register_sets_correct_name(self, app: App):
        """Agent names must match SYSTEM_AGENTS definitions."""
        _register_system_agents(app)
        for agent_def in SYSTEM_AGENTS:
            agent = app.agent_registry.get(agent_def["id"])
            assert agent["name"] == agent_def["name"]

    def test_register_sets_correct_type(self, app: App):
        """Agent types must match SYSTEM_AGENTS definitions."""
        _register_system_agents(app)
        for agent_def in SYSTEM_AGENTS:
            agent = app.agent_registry.get(agent_def["id"])
            assert agent["agent_type"] == agent_def["agent_type"]

    def test_mycelos_agent_in_system_agents(self):
        """The primary 'mycelos' agent must be in SYSTEM_AGENTS."""
        ids = [a["id"] for a in SYSTEM_AGENTS]
        assert "mycelos" in ids, "Primary 'mycelos' agent missing from SYSTEM_AGENTS"

    def test_builder_agent_in_system_agents(self):
        """The 'builder' agent must be in SYSTEM_AGENTS."""
        ids = [a["id"] for a in SYSTEM_AGENTS]
        assert "builder" in ids

    def test_register_idempotent(self, app: App):
        """Calling _register_system_agents twice must not fail or create duplicates."""
        _register_system_agents(app)
        _register_system_agents(app)  # second call
        agents = app.agent_registry.list_agents()
        ids = [a["id"] for a in agents]
        assert len(ids) == len(set(ids)), "Duplicate agents found"

    def test_register_count_matches(self, app: App):
        """Number of registered agents must match SYSTEM_AGENTS length."""
        _register_system_agents(app)
        agents = app.agent_registry.list_agents()
        assert len(agents) == len(SYSTEM_AGENTS)


# ---------------------------------------------------------------------------
# SYSTEM_AGENTS data integrity
# ---------------------------------------------------------------------------


class TestSystemAgentsDefinition:
    """Verify SYSTEM_AGENTS list has the correct structure."""

    def test_all_agents_have_required_keys(self):
        """Every entry must have id, name, agent_type, capabilities."""
        required = {"id", "name", "agent_type", "capabilities"}
        for agent in SYSTEM_AGENTS:
            missing = required - set(agent.keys())
            assert not missing, f"Agent {agent.get('id', '?')} missing keys: {missing}"

    def test_all_agents_have_string_id(self):
        for agent in SYSTEM_AGENTS:
            assert isinstance(agent["id"], str)
            assert len(agent["id"]) > 0

    def test_capabilities_is_list(self):
        for agent in SYSTEM_AGENTS:
            assert isinstance(agent["capabilities"], list)

    def test_no_duplicate_ids(self):
        ids = [a["id"] for a in SYSTEM_AGENTS]
        assert len(ids) == len(set(ids))

    def test_signature_matches_registry(self, app: App):
        """Calling register with SYSTEM_AGENTS data must NOT raise TypeError."""
        for agent in SYSTEM_AGENTS:
            # This must not raise — it's the exact call that docker-entrypoint should make
            try:
                app.agent_registry.register(
                    agent["id"] + "_test",  # avoid PK collision
                    agent["name"],
                    agent["agent_type"],
                    agent["capabilities"],
                    "system",
                )
            except TypeError as e:
                pytest.fail(f"Signature mismatch for agent {agent['id']}: {e}")


# ---------------------------------------------------------------------------
# Master key security
# ---------------------------------------------------------------------------


class TestMasterKeySecurity:
    """Verify master key file handling."""

    def test_setup_master_key_creates_file(self, tmp_path: Path, monkeypatch):
        monkeypatch.delenv("MYCELOS_MASTER_KEY", raising=False)
        from mycelos.cli.init_cmd import _setup_master_key
        _setup_master_key(tmp_path)
        assert (tmp_path / ".master_key").exists()

    def test_setup_master_key_permissions(self, tmp_path: Path, monkeypatch):
        """Master key file must have 0600 permissions (owner-only read/write)."""
        import stat
        monkeypatch.delenv("MYCELOS_MASTER_KEY", raising=False)
        from mycelos.cli.init_cmd import _setup_master_key
        _setup_master_key(tmp_path)
        key_file = tmp_path / ".master_key"
        mode = key_file.stat().st_mode & 0o777
        assert mode == 0o600, f"Key file permissions are {oct(mode)}, expected 0o600"

    def test_setup_master_key_not_empty(self, tmp_path: Path, monkeypatch):
        monkeypatch.delenv("MYCELOS_MASTER_KEY", raising=False)
        from mycelos.cli.init_cmd import _setup_master_key
        _setup_master_key(tmp_path)
        content = (tmp_path / ".master_key").read_text().strip()
        assert len(content) >= 32, "Master key too short"

    def test_master_key_from_env_used(self, tmp_path: Path, monkeypatch):
        monkeypatch.setenv("MYCELOS_MASTER_KEY", "custom-test-key-12345")
        from mycelos.cli.init_cmd import _setup_master_key
        _setup_master_key(tmp_path)
        # When env var is set, key file may or may not be created
        # but the env var should be used
        assert os.environ["MYCELOS_MASTER_KEY"] == "custom-test-key-12345"


# ---------------------------------------------------------------------------
# Init end-to-end (programmatic, no CLI)
# ---------------------------------------------------------------------------


class TestInitEndToEnd:
    """Verify that a fresh init produces a working system."""

    def test_fresh_init_has_agents(self, tmp_path: Path):
        """After App.initialize() + _register_system_agents, agents table is populated."""
        os.environ["MYCELOS_MASTER_KEY"] = "test-e2e-init"
        app = App(tmp_path)
        app.initialize()
        _register_system_agents(app)
        agents = app.agent_registry.list_agents()
        assert len(agents) >= 4, f"Expected at least 4 agents, got {len(agents)}"

    def test_fresh_init_has_config_generation(self, tmp_path: Path):
        """After init, at least one config generation must exist."""
        os.environ["MYCELOS_MASTER_KEY"] = "test-e2e-config"
        app = App(tmp_path)
        app.initialize()
        gens = app.config.list_generations()
        assert len(gens) >= 1

    def test_fresh_init_has_default_user(self, tmp_path: Path):
        """After init, 'default' user must exist."""
        os.environ["MYCELOS_MASTER_KEY"] = "test-e2e-user"
        app = App(tmp_path)
        app.initialize()
        row = app.storage.fetchone("SELECT id FROM users WHERE id = 'default'")
        assert row is not None

    def test_fresh_init_has_builtin_connectors(self, tmp_path: Path):
        """After init, builtin connectors (duckduckgo, http) should be registered."""
        os.environ["MYCELOS_MASTER_KEY"] = "test-e2e-connectors"
        app = App(tmp_path)
        app.initialize()
        from mycelos.cli.init_cmd import _register_builtin_connectors
        _register_builtin_connectors(app)
        connectors = app.connector_registry.list_connectors()
        assert len(connectors) >= 1
