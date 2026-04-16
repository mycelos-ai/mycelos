"""Tests for Schema V2 — new tables and modified agents table."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from mycelos.storage.database import SQLiteStorage


@pytest.fixture
def storage() -> SQLiteStorage:
    with tempfile.TemporaryDirectory() as tmp:
        s = SQLiteStorage(Path(tmp) / "test.db")
        s.initialize()
        yield s


def test_connectors_table_exists(storage: SQLiteStorage):
    storage.execute(
        "INSERT INTO connectors (id, name, connector_type) VALUES (?, ?, ?)",
        ("ddg", "DuckDuckGo", "search"),
    )
    row = storage.fetchone("SELECT * FROM connectors WHERE id = ?", ("ddg",))
    assert row is not None
    assert row["name"] == "DuckDuckGo"
    assert row["status"] == "active"


def test_connector_capabilities_table(storage: SQLiteStorage):
    storage.execute(
        "INSERT INTO connectors (id, name, connector_type) VALUES (?, ?, ?)",
        ("ddg", "DuckDuckGo", "search"),
    )
    storage.execute(
        "INSERT INTO connector_capabilities (connector_id, capability) VALUES (?, ?)",
        ("ddg", "search.web"),
    )
    storage.execute(
        "INSERT INTO connector_capabilities (connector_id, capability) VALUES (?, ?)",
        ("ddg", "search.news"),
    )
    rows = storage.fetchall(
        "SELECT capability FROM connector_capabilities WHERE connector_id = ?",
        ("ddg",),
    )
    caps = {r["capability"] for r in rows}
    assert caps == {"search.web", "search.news"}


def test_connector_cascade_delete(storage: SQLiteStorage):
    storage.execute(
        "INSERT INTO connectors (id, name, connector_type) VALUES (?, ?, ?)",
        ("ddg", "DuckDuckGo", "search"),
    )
    storage.execute(
        "INSERT INTO connector_capabilities (connector_id, capability) VALUES (?, ?)",
        ("ddg", "search.web"),
    )
    storage.execute("DELETE FROM connectors WHERE id = ?", ("ddg",))
    rows = storage.fetchall(
        "SELECT * FROM connector_capabilities WHERE connector_id = ?", ("ddg",)
    )
    assert rows == []


def test_agent_capabilities_table(storage: SQLiteStorage):
    storage.execute(
        "INSERT INTO agents (id, name, agent_type, created_by) VALUES (?, ?, ?, ?)",
        ("a1", "test-agent", "deterministic", "system"),
    )
    storage.execute(
        "INSERT INTO agent_capabilities (agent_id, capability) VALUES (?, ?)",
        ("a1", "search.web"),
    )
    storage.execute(
        "INSERT INTO agent_capabilities (agent_id, capability) VALUES (?, ?)",
        ("a1", "http.get"),
    )
    rows = storage.fetchall(
        "SELECT capability FROM agent_capabilities WHERE agent_id = ?", ("a1",)
    )
    caps = {r["capability"] for r in rows}
    assert caps == {"search.web", "http.get"}


def test_agents_table_has_hash_columns(storage: SQLiteStorage):
    storage.execute(
        """INSERT INTO agents (id, name, agent_type, created_by, code_hash, tests_hash, prompt_hash)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        ("a1", "test", "deterministic", "system", "hash1", "hash2", "hash3"),
    )
    row = storage.fetchone("SELECT * FROM agents WHERE id = ?", ("a1",))
    assert row["code_hash"] == "hash1"
    assert row["tests_hash"] == "hash2"
    assert row["prompt_hash"] == "hash3"


def test_agents_table_no_capabilities_column(storage: SQLiteStorage):
    """The old capabilities JSON column should not exist."""
    storage.execute(
        "INSERT INTO agents (id, name, agent_type, created_by) VALUES (?, ?, ?, ?)",
        ("a1", "test", "deterministic", "system"),
    )
    row = storage.fetchone("SELECT * FROM agents WHERE id = ?", ("a1",))
    assert "capabilities" not in dict(row)


def test_llm_models_table(storage: SQLiteStorage):
    storage.execute(
        """INSERT INTO llm_models (id, provider, tier, input_cost_per_1k, output_cost_per_1k, max_context)
           VALUES (?, ?, ?, ?, ?, ?)""",
        ("anthropic/claude-sonnet-4-6", "anthropic", "sonnet", 0.003, 0.015, 200000),
    )
    row = storage.fetchone("SELECT * FROM llm_models WHERE id = ?", ("anthropic/claude-sonnet-4-6",))
    assert row is not None
    assert row["provider"] == "anthropic"
    assert row["tier"] == "sonnet"
    assert row["input_cost_per_1k"] == 0.003


def test_agent_llm_models_system_defaults(storage: SQLiteStorage):
    """System defaults have agent_id = NULL."""
    storage.execute(
        """INSERT INTO llm_models (id, provider, tier) VALUES (?, ?, ?)""",
        ("anthropic/claude-sonnet-4-6", "anthropic", "sonnet"),
    )
    storage.execute(
        """INSERT INTO agent_llm_models (agent_id, model_id, priority, purpose)
           VALUES (?, ?, ?, ?)""",
        (None, "anthropic/claude-sonnet-4-6", 1, "execution"),
    )
    rows = storage.fetchall(
        """SELECT model_id, priority FROM agent_llm_models
           WHERE agent_id IS NULL AND purpose = ? ORDER BY priority""",
        ("execution",),
    )
    assert len(rows) == 1
    assert rows[0]["model_id"] == "anthropic/claude-sonnet-4-6"


def test_agent_llm_models_per_agent(storage: SQLiteStorage):
    """Agent-specific model assignments."""
    storage.execute(
        "INSERT INTO agents (id, name, agent_type, created_by) VALUES (?, ?, ?, ?)",
        ("a1", "news-agent", "deterministic", "system"),
    )
    storage.execute(
        "INSERT INTO llm_models (id, provider, tier) VALUES (?, ?, ?)",
        ("anthropic/claude-haiku-4-5", "anthropic", "haiku"),
    )
    storage.execute(
        "INSERT INTO llm_models (id, provider, tier) VALUES (?, ?, ?)",
        ("ollama/llama3", "ollama", "sonnet"),
    )
    storage.execute(
        "INSERT INTO agent_llm_models (agent_id, model_id, priority, purpose) VALUES (?, ?, ?, ?)",
        ("a1", "anthropic/claude-haiku-4-5", 1, "execution"),
    )
    storage.execute(
        "INSERT INTO agent_llm_models (agent_id, model_id, priority, purpose) VALUES (?, ?, ?, ?)",
        ("a1", "ollama/llama3", 2, "execution"),
    )
    rows = storage.fetchall(
        """SELECT model_id FROM agent_llm_models
           WHERE agent_id = ? AND purpose = ? ORDER BY priority""",
        ("a1", "execution"),
    )
    models = [r["model_id"] for r in rows]
    assert models == ["anthropic/claude-haiku-4-5", "ollama/llama3"]


def test_agent_llm_cascade_delete(storage: SQLiteStorage):
    """Deleting an agent cascades to its LLM assignments."""
    storage.execute(
        "INSERT INTO agents (id, name, agent_type, created_by) VALUES (?, ?, ?, ?)",
        ("a1", "test", "deterministic", "system"),
    )
    storage.execute(
        "INSERT INTO llm_models (id, provider, tier) VALUES (?, ?, ?)",
        ("m1", "test", "haiku"),
    )
    storage.execute(
        "INSERT INTO agent_llm_models (agent_id, model_id, priority, purpose) VALUES (?, ?, ?, ?)",
        ("a1", "m1", 1, "execution"),
    )
    storage.execute("DELETE FROM agents WHERE id = ?", ("a1",))
    rows = storage.fetchall("SELECT * FROM agent_llm_models WHERE agent_id = ?", ("a1",))
    assert rows == []
