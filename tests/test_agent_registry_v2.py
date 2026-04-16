"""Tests for AgentRegistry V2 — normalized capabilities, Object Store code, LLM models."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from mycelos.agents.registry import AgentRegistry
from mycelos.storage.database import SQLiteStorage
from mycelos.storage.object_store import ObjectStore


@pytest.fixture
def storage() -> SQLiteStorage:
    with tempfile.TemporaryDirectory() as tmp:
        s = SQLiteStorage(Path(tmp) / "test.db")
        s.initialize()
        yield s


@pytest.fixture
def object_store(tmp_path: Path) -> ObjectStore:
    return ObjectStore(tmp_path)


@pytest.fixture
def registry(storage: SQLiteStorage) -> AgentRegistry:
    return AgentRegistry(storage)


def test_register_with_capabilities(registry: AgentRegistry, storage: SQLiteStorage):
    registry.register("a1", "news-agent", "deterministic", ["search.web", "search.news"], "system")
    agent = registry.get("a1")
    assert agent is not None
    assert agent["name"] == "news-agent"
    assert set(agent["capabilities"]) == {"search.web", "search.news"}


def test_register_without_capabilities(registry: AgentRegistry):
    registry.register("a1", "simple", "deterministic", [], "system")
    agent = registry.get("a1")
    assert agent["capabilities"] == []


def test_set_capabilities_replaces(registry: AgentRegistry):
    registry.register("a1", "agent", "deterministic", ["old.cap"], "system")
    registry.set_capabilities("a1", ["new.cap1", "new.cap2"])
    agent = registry.get("a1")
    assert set(agent["capabilities"]) == {"new.cap1", "new.cap2"}


def test_get_nonexistent(registry: AgentRegistry):
    assert registry.get("nope") is None


def test_list_agents_with_capabilities(registry: AgentRegistry):
    registry.register("a1", "agent1", "deterministic", ["cap.a"], "system")
    registry.register("a2", "agent2", "light_model", ["cap.b", "cap.c"], "system")
    agents = registry.list_agents()
    assert len(agents) == 2
    a2 = next(a for a in agents if a["id"] == "a2")
    assert set(a2["capabilities"]) == {"cap.b", "cap.c"}


def test_list_agents_by_status(registry: AgentRegistry):
    registry.register("a1", "active1", "deterministic", [], "system")
    registry.register("a2", "proposed1", "deterministic", [], "system")
    registry.set_status("a1", "active")
    result = registry.list_agents(status="active")
    assert len(result) == 1
    assert result[0]["name"] == "active1"


def test_save_and_get_code(registry: AgentRegistry, object_store: ObjectStore):
    registry.register("a1", "agent", "deterministic", [], "system")
    registry.save_code("a1", "print('hello')", "def test(): pass", "You are an agent", object_store)
    agent = registry.get("a1")
    assert agent["code_hash"] is not None
    assert agent["tests_hash"] is not None
    assert agent["prompt_hash"] is not None
    # Load from Object Store
    code = object_store.load(agent["code_hash"])
    assert code == "print('hello')"


def test_get_code(registry: AgentRegistry, object_store: ObjectStore):
    registry.register("a1", "agent", "deterministic", [], "system")
    registry.save_code("a1", "code here", "tests here", "prompt here", object_store)
    result = registry.get_code("a1", object_store)
    assert result["code"] == "code here"
    assert result["tests"] == "tests here"
    assert result["prompt"] == "prompt here"


def test_get_code_no_code_stored(registry: AgentRegistry, object_store: ObjectStore):
    registry.register("a1", "agent", "deterministic", [], "system")
    result = registry.get_code("a1", object_store)
    assert result is None


def test_set_models(registry: AgentRegistry, storage: SQLiteStorage):
    registry.register("a1", "agent", "deterministic", [], "system")
    # Need to insert llm_models first (FK constraint)
    storage.execute("INSERT INTO llm_models (id, provider, tier) VALUES (?, ?, ?)",
                    ("anthropic/claude-haiku-4-5", "anthropic", "haiku"))
    storage.execute("INSERT INTO llm_models (id, provider, tier) VALUES (?, ?, ?)",
                    ("ollama/llama3", "ollama", "sonnet"))
    registry.set_models("a1", ["anthropic/claude-haiku-4-5", "ollama/llama3"], "execution")
    models = registry.get_models("a1", "execution")
    assert models == ["anthropic/claude-haiku-4-5", "ollama/llama3"]


def test_set_models_replaces(registry: AgentRegistry, storage: SQLiteStorage):
    registry.register("a1", "agent", "deterministic", [], "system")
    storage.execute("INSERT INTO llm_models (id, provider, tier) VALUES (?, ?, ?)",
                    ("m1", "test", "haiku"))
    storage.execute("INSERT INTO llm_models (id, provider, tier) VALUES (?, ?, ?)",
                    ("m2", "test", "sonnet"))
    registry.set_models("a1", ["m1"], "execution")
    registry.set_models("a1", ["m2"], "execution")
    models = registry.get_models("a1", "execution")
    assert models == ["m2"]


def test_get_models_empty(registry: AgentRegistry):
    registry.register("a1", "agent", "deterministic", [], "system")
    models = registry.get_models("a1", "execution")
    assert models == []


def test_cascade_delete_cleans_capabilities_and_models(registry: AgentRegistry, storage: SQLiteStorage):
    registry.register("a1", "agent", "deterministic", ["cap.a"], "system")
    storage.execute("INSERT INTO llm_models (id, provider, tier) VALUES (?, ?, ?)",
                    ("m1", "test", "haiku"))
    registry.set_models("a1", ["m1"], "execution")
    storage.execute("DELETE FROM agents WHERE id = ?", ("a1",))
    caps = storage.fetchall("SELECT * FROM agent_capabilities WHERE agent_id = ?", ("a1",))
    models = storage.fetchall("SELECT * FROM agent_llm_models WHERE agent_id = ?", ("a1",))
    assert caps == []
    assert models == []


def test_update_reputation(registry: AgentRegistry):
    registry.register("a1", "agent", "deterministic", [], "system")
    registry.update_reputation("a1", 0.9)
    agent = registry.get("a1")
    assert agent["reputation"] == 0.9
