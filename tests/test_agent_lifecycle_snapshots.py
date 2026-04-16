"""Tests for agent lifecycle with NixOS-style snapshots.

Covers the full flow: create agent → save code → change model → snapshot → rollback.
Simulates what happens when the CreatorAgent generates code and the system evolves.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

from mycelos.app import App
from mycelos.config.state_manager import StateManager
from mycelos.storage.object_store import ObjectStore


@pytest.fixture
def app() -> App:
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["MYCELOS_MASTER_KEY"] = "test-key-agent-lifecycle"
        a = App(Path(tmp))
        a.initialize()
        yield a


@pytest.fixture
def sm(app: App) -> StateManager:
    return app.state_manager


@pytest.fixture
def obj_store(tmp_path: Path) -> ObjectStore:
    return ObjectStore(tmp_path)


def _add_model(app: App, model_id: str, provider: str = "anthropic", tier: str = "sonnet") -> None:
    app.storage.execute(
        "INSERT OR IGNORE INTO llm_models (id, provider, tier) VALUES (?, ?, ?)",
        (model_id, provider, tier),
    )


# ---------------------------------------------------------------------------
# Simulated Creator-Agent flow
# ---------------------------------------------------------------------------


def test_simulated_agent_creation_generates_snapshot(app, sm, obj_store):
    """Simulate what CreatorAgent does: generate code → audit → register → snapshot."""
    # 1. "CreatorAgent" generates code (mocked, no LLM call)
    agent_code = """\
from mycelos.sdk import run, progress
from mycelos.agents.models import AgentInput, AgentOutput

class NewsAgent:
    agent_id = "news-agent"
    agent_type = "deterministic"
    capabilities_required = ["search.web"]

    def execute(self, input: AgentInput) -> AgentOutput:
        progress("Searching...")
        results = run(tool="search.web", args={"query": input.task})
        return AgentOutput(success=True, result=results, artifacts=[], metadata={})
"""
    agent_tests = """\
def test_news_agent_returns_results():
    from mycelos.agents.models import AgentInput, AgentOutput
    inp = AgentInput(task="test news", context={})
    assert isinstance(inp.task, str)
    assert len(inp.task) > 0
"""
    agent_prompt = "You are a news search agent. Find relevant articles."

    # 2. Register agent
    app.agent_registry.register(
        "news-agent", "News Agent", "deterministic",
        ["search.web", "search.news"], "creator-agent",
    )

    # 3. Save code to Object Store
    hashes = app.agent_registry.save_code(
        "news-agent", agent_code, agent_tests, agent_prompt, obj_store
    )
    assert hashes["code_hash"] is not None
    assert hashes["tests_hash"] is not None
    assert hashes["prompt_hash"] is not None

    # 4. Set LLM model for agent
    _add_model(app, "anthropic/claude-haiku-4-5", "anthropic", "haiku")
    app.agent_registry.set_models("news-agent", ["anthropic/claude-haiku-4-5"], "execution")

    # 5. Create generation
    gen_id = app.config.apply_from_state(sm, "Agent 'News Agent' registriert", "agent_creation")

    # 6. Verify snapshot contains everything
    import json
    row = app.storage.fetchone("SELECT config_snapshot FROM config_generations WHERE id = ?", (gen_id,))
    snapshot = json.loads(row["config_snapshot"])

    assert "news-agent" in snapshot["agents"]
    agent_snap = snapshot["agents"]["news-agent"]
    assert agent_snap["name"] == "News Agent"
    assert agent_snap["code_hash"] == hashes["code_hash"]
    assert set(agent_snap["capabilities"]) == {"search.web", "search.news"}

    # LLM assignment in snapshot
    assert "news-agent:execution" in snapshot["llm"]["assignments"]
    assert snapshot["llm"]["assignments"]["news-agent:execution"] == ["anthropic/claude-haiku-4-5"]

    # Code is in Object Store
    assert obj_store.load(hashes["code_hash"]) == agent_code


# ---------------------------------------------------------------------------
# Agent code update → new generation
# ---------------------------------------------------------------------------


def test_agent_code_update_creates_new_generation(app, sm, obj_store):
    """Updating agent code should produce a new generation with new hash."""
    app.agent_registry.register("a1", "Agent", "deterministic", [], "system")
    app.agent_registry.save_code("a1", "v1_code", "v1_tests", "v1_prompt", obj_store)
    gen1 = app.config.apply_from_state(sm, "v1", "test")

    v1_hash = app.agent_registry.get("a1")["code_hash"]

    # Update code
    app.agent_registry.save_code("a1", "v2_code_changed", "v2_tests", "v2_prompt", obj_store)
    gen2 = app.config.apply_from_state(sm, "v2", "test")

    v2_hash = app.agent_registry.get("a1")["code_hash"]

    # Different generations, different hashes
    assert gen1 != gen2
    assert v1_hash != v2_hash

    # Both versions exist in Object Store
    assert obj_store.load(v1_hash) == "v1_code"
    assert obj_store.load(v2_hash) == "v2_code_changed"


def test_agent_code_rollback_restores_old_code(app, sm, obj_store):
    """Rolling back should restore the old code hash — old code still in Object Store."""
    app.agent_registry.register("a1", "Agent", "deterministic", [], "system")
    app.agent_registry.save_code("a1", "original_code", None, None, obj_store)
    gen1 = app.config.apply_from_state(sm, "original", "test")
    original_hash = app.agent_registry.get("a1")["code_hash"]

    app.agent_registry.save_code("a1", "broken_code", None, None, obj_store)
    gen2 = app.config.apply_from_state(sm, "broken", "test")
    broken_hash = app.agent_registry.get("a1")["code_hash"]

    # Rollback
    app.config.rollback(to_generation=gen1, state_manager=sm)

    # Hash points back to original
    assert app.agent_registry.get("a1")["code_hash"] == original_hash
    # Code is loadable
    assert obj_store.load(original_hash) == "original_code"
    # Broken code still exists (immutable store)
    assert obj_store.load(broken_hash) == "broken_code"


# ---------------------------------------------------------------------------
# LLM model change → new generation
# ---------------------------------------------------------------------------


def test_agent_model_change_creates_new_generation(app, sm):
    """Changing an agent's LLM model should create a new generation."""
    app.agent_registry.register("a1", "Agent", "deterministic", [], "system")
    _add_model(app, "m-haiku", "anthropic", "haiku")
    _add_model(app, "m-sonnet", "anthropic", "sonnet")

    app.agent_registry.set_models("a1", ["m-haiku"], "execution")
    gen1 = app.config.apply_from_state(sm, "haiku", "test")

    app.agent_registry.set_models("a1", ["m-sonnet"], "execution")
    gen2 = app.config.apply_from_state(sm, "sonnet", "test")

    assert gen1 != gen2
    assert app.agent_registry.get_models("a1", "execution") == ["m-sonnet"]


def test_agent_model_rollback_restores_old_model(app, sm):
    """Rolling back should restore the old LLM model assignment."""
    app.agent_registry.register("a1", "Agent", "deterministic", [], "system")
    _add_model(app, "m-haiku", "anthropic", "haiku")
    _add_model(app, "m-sonnet", "anthropic", "sonnet")

    app.agent_registry.set_models("a1", ["m-haiku"], "execution")
    gen1 = app.config.apply_from_state(sm, "haiku", "test")

    app.agent_registry.set_models("a1", ["m-sonnet"], "execution")
    gen2 = app.config.apply_from_state(sm, "changed to sonnet", "test")

    # Rollback to haiku
    app.config.rollback(to_generation=gen1, state_manager=sm)
    assert app.agent_registry.get_models("a1", "execution") == ["m-haiku"]


def test_agent_model_failover_chain_in_snapshot(app, sm):
    """Full failover chain (primary + fallbacks) should be in snapshot."""
    app.agent_registry.register("a1", "Agent", "deterministic", [], "system")
    _add_model(app, "m1", "anthropic", "haiku")
    _add_model(app, "m2", "ollama", "sonnet")
    _add_model(app, "m3", "openai", "sonnet")

    app.agent_registry.set_models("a1", ["m1", "m2", "m3"], "execution")
    gen_id = app.config.apply_from_state(sm, "failover chain", "test")

    import json
    row = app.storage.fetchone("SELECT config_snapshot FROM config_generations WHERE id = ?", (gen_id,))
    snapshot = json.loads(row["config_snapshot"])

    assert snapshot["llm"]["assignments"]["a1:execution"] == ["m1", "m2", "m3"]


def test_agent_model_failover_rollback(app, sm):
    """Rollback should restore the entire failover chain, not just primary."""
    app.agent_registry.register("a1", "Agent", "deterministic", [], "system")
    _add_model(app, "m1", "anthropic", "haiku")
    _add_model(app, "m2", "ollama", "sonnet")
    _add_model(app, "m3", "openai", "sonnet")

    # Gen 1: chain [m1, m2, m3]
    app.agent_registry.set_models("a1", ["m1", "m2", "m3"], "execution")
    gen1 = app.config.apply_from_state(sm, "3-model chain", "test")

    # Gen 2: changed to just [m3]
    app.agent_registry.set_models("a1", ["m3"], "execution")
    gen2 = app.config.apply_from_state(sm, "single model", "test")

    # Rollback
    app.config.rollback(to_generation=gen1, state_manager=sm)
    assert app.agent_registry.get_models("a1", "execution") == ["m1", "m2", "m3"]


# ---------------------------------------------------------------------------
# Combined: code + model + capabilities change
# ---------------------------------------------------------------------------


def test_combined_agent_evolution_and_rollback(app, sm, obj_store):
    """Full agent evolution: code + model + capabilities change, then rollback."""
    _add_model(app, "m-haiku", "anthropic", "haiku")
    _add_model(app, "m-sonnet", "anthropic", "sonnet")

    # Gen 1: initial agent
    app.agent_registry.register("a1", "Agent", "deterministic", ["search.web"], "system")
    app.agent_registry.save_code("a1", "v1_code", "v1_tests", None, obj_store)
    app.agent_registry.set_models("a1", ["m-haiku"], "execution")
    gen1 = app.config.apply_from_state(sm, "initial agent", "test")

    # Gen 2: evolved agent — new code, new capabilities, new model
    app.agent_registry.save_code("a1", "v2_code_improved", "v2_tests_more", None, obj_store)
    app.agent_registry.set_capabilities("a1", ["search.web", "search.news", "http.get"])
    app.agent_registry.set_models("a1", ["m-sonnet", "m-haiku"], "execution")
    gen2 = app.config.apply_from_state(sm, "evolved agent", "test")

    # Verify gen2 state
    agent = app.agent_registry.get("a1")
    assert set(agent["capabilities"]) == {"search.web", "search.news", "http.get"}
    assert obj_store.load(agent["code_hash"]) == "v2_code_improved"
    assert app.agent_registry.get_models("a1", "execution") == ["m-sonnet", "m-haiku"]

    # Rollback to gen1
    app.config.rollback(to_generation=gen1, state_manager=sm)

    # Everything restored
    agent = app.agent_registry.get("a1")
    assert agent["capabilities"] == ["search.web"]
    assert obj_store.load(agent["code_hash"]) == "v1_code"
    assert app.agent_registry.get_models("a1", "execution") == ["m-haiku"]


# ---------------------------------------------------------------------------
# System defaults change → snapshot
# ---------------------------------------------------------------------------


def test_system_default_model_change_and_rollback(app, sm):
    """Changing system-wide default models should create generation + be rollbackable."""
    _add_model(app, "m-sonnet", "anthropic", "sonnet")
    _add_model(app, "m-haiku", "anthropic", "haiku")
    _add_model(app, "m-gpt", "openai", "sonnet")

    # Gen 1: anthropic defaults
    app.model_registry.set_system_defaults({
        "execution": ["m-sonnet", "m-haiku"],
        "classification": ["m-haiku"],
    })
    gen1 = app.config.apply_from_state(sm, "anthropic defaults", "test")

    # Gen 2: switch to openai
    app.model_registry.set_system_defaults({
        "execution": ["m-gpt"],
        "classification": ["m-gpt"],
    })
    gen2 = app.config.apply_from_state(sm, "openai defaults", "test")

    assert app.model_registry.resolve_models(None, "execution") == ["m-gpt"]

    # Rollback
    app.config.rollback(to_generation=gen1, state_manager=sm)
    assert app.model_registry.resolve_models(None, "execution") == ["m-sonnet", "m-haiku"]
    assert app.model_registry.resolve_models(None, "classification") == ["m-haiku"]


# ---------------------------------------------------------------------------
# Multiple agents, independent evolution
# ---------------------------------------------------------------------------


def test_multiple_agents_independent_rollback(app, sm, obj_store):
    """Two agents evolve independently — rollback restores both."""
    _add_model(app, "m1", "anthropic", "haiku")
    _add_model(app, "m2", "anthropic", "sonnet")

    # Gen 1: two agents
    app.agent_registry.register("search", "Search", "deterministic", ["search.web"], "system")
    app.agent_registry.register("email", "Email", "light_model", ["google.gmail.read"], "system")
    app.agent_registry.save_code("search", "search_v1", None, None, obj_store)
    app.agent_registry.save_code("email", "email_v1", None, None, obj_store)
    app.agent_registry.set_models("search", ["m1"], "execution")
    app.agent_registry.set_models("email", ["m2"], "execution")
    gen1 = app.config.apply_from_state(sm, "two agents v1", "test")

    # Gen 2: only search evolves
    app.agent_registry.save_code("search", "search_v2_better", None, None, obj_store)
    app.agent_registry.set_capabilities("search", ["search.web", "search.news"])
    gen2 = app.config.apply_from_state(sm, "search evolved", "test")

    # Rollback
    app.config.rollback(to_generation=gen1, state_manager=sm)

    # Search restored
    search = app.agent_registry.get("search")
    assert search["capabilities"] == ["search.web"]
    assert obj_store.load(search["code_hash"]) == "search_v1"

    # Email unchanged (was same in both gens)
    email = app.agent_registry.get("email")
    assert email["capabilities"] == ["google.gmail.read"]
    assert obj_store.load(email["code_hash"]) == "email_v1"
