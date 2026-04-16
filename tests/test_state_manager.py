"""Tests for StateManager — snapshot and restore of declarative state."""

from __future__ import annotations

import base64
import tempfile
from pathlib import Path

import pytest

from mycelos.config.state_manager import StateManager
from mycelos.storage.database import SQLiteStorage


@pytest.fixture
def storage() -> SQLiteStorage:
    with tempfile.TemporaryDirectory() as tmp:
        s = SQLiteStorage(Path(tmp) / "test.db")
        s.initialize()
        yield s


@pytest.fixture
def mgr(storage: SQLiteStorage) -> StateManager:
    return StateManager(storage)


def _setup_connector(storage: SQLiteStorage, cid: str, name: str, caps: list[str]) -> None:
    storage.execute(
        "INSERT INTO connectors (id, name, connector_type) VALUES (?, ?, ?)",
        (cid, name, "search"),
    )
    for cap in caps:
        storage.execute(
            "INSERT INTO connector_capabilities (connector_id, capability) VALUES (?, ?)",
            (cid, cap),
        )


def _setup_agent(storage: SQLiteStorage, aid: str, name: str, caps: list[str]) -> None:
    storage.execute(
        "INSERT INTO agents (id, name, agent_type, created_by) VALUES (?, ?, ?, ?)",
        (aid, name, "deterministic", "system"),
    )
    for cap in caps:
        storage.execute(
            "INSERT INTO agent_capabilities (agent_id, capability) VALUES (?, ?)",
            (aid, cap),
        )


def test_snapshot_empty_db(mgr: StateManager):
    snap = mgr.snapshot()
    assert snap["schema_version"] == 2
    assert snap["connectors"] == {}
    assert snap["agents"] == {}
    assert snap["policies"] == {}


def test_snapshot_includes_connectors(mgr: StateManager, storage: SQLiteStorage):
    _setup_connector(storage, "ddg", "DuckDuckGo", ["search.web", "search.news"])
    snap = mgr.snapshot()
    assert "ddg" in snap["connectors"]
    assert set(snap["connectors"]["ddg"]["capabilities"]) == {"search.web", "search.news"}


def test_snapshot_includes_agents(mgr: StateManager, storage: SQLiteStorage):
    _setup_agent(storage, "a1", "news-agent", ["search.web"])
    snap = mgr.snapshot()
    assert "a1" in snap["agents"]
    assert snap["agents"]["a1"]["name"] == "news-agent"
    assert snap["agents"]["a1"]["capabilities"] == ["search.web"]


def test_snapshot_includes_agent_code_hashes(mgr: StateManager, storage: SQLiteStorage):
    storage.execute(
        """INSERT INTO agents (id, name, agent_type, created_by, code_hash, tests_hash, prompt_hash)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        ("a1", "agent", "deterministic", "system", "hash1", "hash2", "hash3"),
    )
    snap = mgr.snapshot()
    assert snap["agents"]["a1"]["code_hash"] == "hash1"
    assert snap["agents"]["a1"]["tests_hash"] == "hash2"
    assert snap["agents"]["a1"]["prompt_hash"] == "hash3"


def test_snapshot_includes_policies(mgr: StateManager, storage: SQLiteStorage):
    storage.execute(
        "INSERT INTO policies (id, user_id, agent_id, resource, decision) VALUES (?, ?, ?, ?, ?)",
        ("p1", "default", None, "search.web", "always"),
    )
    snap = mgr.snapshot()
    assert "default:*:search.web" in snap["policies"]
    assert snap["policies"]["default:*:search.web"] == "always"


def test_snapshot_includes_llm_models(mgr: StateManager, storage: SQLiteStorage):
    storage.execute(
        """INSERT INTO llm_models (id, provider, tier, input_cost_per_1k, output_cost_per_1k, max_context)
           VALUES (?, ?, ?, ?, ?, ?)""",
        ("m1", "anthropic", "sonnet", 0.003, 0.015, 200000),
    )
    snap = mgr.snapshot()
    assert "m1" in snap["llm"]["models"]
    assert snap["llm"]["models"]["m1"]["provider"] == "anthropic"


def test_snapshot_includes_llm_assignments(mgr: StateManager, storage: SQLiteStorage):
    storage.execute("INSERT INTO llm_models (id, provider, tier) VALUES (?, ?, ?)", ("m1", "test", "haiku"))
    storage.execute(
        "INSERT INTO agent_llm_models (agent_id, model_id, priority, purpose) VALUES (?, ?, ?, ?)",
        (None, "m1", 1, "execution"),
    )
    snap = mgr.snapshot()
    assert "system:execution" in snap["llm"]["assignments"]
    assert snap["llm"]["assignments"]["system:execution"] == ["m1"]


def test_restore_connectors(mgr: StateManager, storage: SQLiteStorage):
    _setup_connector(storage, "ddg", "DuckDuckGo", ["search.web"])
    snap = mgr.snapshot()
    # Wipe connectors
    storage.execute("DELETE FROM connector_capabilities")
    storage.execute("DELETE FROM connectors")
    assert storage.fetchall("SELECT * FROM connectors") == []
    # Restore
    mgr.restore(snap)
    rows = storage.fetchall("SELECT * FROM connectors")
    assert len(rows) == 1
    assert rows[0]["id"] == "ddg"
    caps = storage.fetchall("SELECT capability FROM connector_capabilities WHERE connector_id = ?", ("ddg",))
    assert len(caps) == 1


def test_restore_agents_and_capabilities(mgr: StateManager, storage: SQLiteStorage):
    _setup_agent(storage, "a1", "news-agent", ["search.web", "http.get"])
    snap = mgr.snapshot()
    # Wipe
    storage.execute("DELETE FROM agent_capabilities")
    storage.execute("DELETE FROM agents")
    # Restore
    mgr.restore(snap)
    agent = storage.fetchone("SELECT * FROM agents WHERE id = ?", ("a1",))
    assert agent is not None
    assert agent["name"] == "news-agent"
    caps = storage.fetchall("SELECT capability FROM agent_capabilities WHERE agent_id = ?", ("a1",))
    assert {c["capability"] for c in caps} == {"search.web", "http.get"}


def test_restore_policies(mgr: StateManager, storage: SQLiteStorage):
    storage.execute(
        "INSERT INTO policies (id, user_id, resource, decision) VALUES (?, ?, ?, ?)",
        ("p1", "default", "search.web", "always"),
    )
    snap = mgr.snapshot()
    storage.execute("DELETE FROM policies")
    mgr.restore(snap)
    row = storage.fetchone("SELECT * FROM policies WHERE resource = ?", ("search.web",))
    assert row is not None
    assert row["decision"] == "always"


def test_restore_llm_models_and_assignments(mgr: StateManager, storage: SQLiteStorage):
    storage.execute("INSERT INTO llm_models (id, provider, tier) VALUES (?, ?, ?)", ("m1", "test", "haiku"))
    storage.execute(
        "INSERT INTO agent_llm_models (agent_id, model_id, priority, purpose) VALUES (?, ?, ?, ?)",
        (None, "m1", 1, "execution"),
    )
    snap = mgr.snapshot()
    storage.execute("DELETE FROM agent_llm_models")
    storage.execute("DELETE FROM llm_models")
    mgr.restore(snap)
    models = storage.fetchall("SELECT * FROM llm_models")
    assert len(models) == 1
    assignments = storage.fetchall("SELECT * FROM agent_llm_models WHERE agent_id IS NULL")
    assert len(assignments) == 1


def test_restore_skips_rotated_credentials(mgr: StateManager, storage: SQLiteStorage):
    """Credentials with security_rotated=1 must NOT be reverted on restore."""
    # Snapshot has old credential
    storage.execute(
        "INSERT INTO credentials (service, encrypted, nonce) VALUES (?, ?, ?)",
        ("svc", b"old_encrypted", b"old_nonce"),
    )
    snap = mgr.snapshot()
    # Now rotate the credential (security incident)
    storage.execute(
        "UPDATE credentials SET encrypted = ?, nonce = ?, security_rotated = 1 WHERE service = ?",
        (b"new_encrypted", b"new_nonce", "svc"),
    )
    # Restore should keep the NEW (rotated) credential
    mgr.restore(snap)
    row = storage.fetchone("SELECT * FROM credentials WHERE service = ?", ("svc",))
    assert row["encrypted"] == b"new_encrypted"
    assert row["security_rotated"] == 1


def test_restore_restores_non_rotated_credentials(mgr: StateManager, storage: SQLiteStorage):
    """Normal credentials (not rotated) should be restored."""
    storage.execute(
        "INSERT INTO credentials (service, encrypted, nonce) VALUES (?, ?, ?)",
        ("svc", b"original", b"nonce1"),
    )
    snap = mgr.snapshot()
    # Change credential without marking as rotated
    storage.execute(
        "UPDATE credentials SET encrypted = ?, nonce = ? WHERE service = ?",
        (b"changed", b"nonce2", "svc"),
    )
    # Restore should bring back original
    mgr.restore(snap)
    row = storage.fetchone("SELECT * FROM credentials WHERE service = ?", ("svc",))
    assert row["encrypted"] == b"original"


def test_snapshot_includes_workflows(mgr: StateManager, storage: SQLiteStorage):
    """Workflows should be included in the snapshot."""
    storage.execute(
        """INSERT INTO workflows (id, name, steps, scope, tags)
           VALUES (?, ?, ?, ?, ?)""",
        ("news", "News Summary", '[{"id": "s1", "agent": "search"}]',
         '["search.web"]', '["news"]'),
    )
    snap = mgr.snapshot()
    assert "workflows" in snap
    assert "news" in snap["workflows"]
    assert snap["workflows"]["news"]["name"] == "News Summary"
    assert snap["workflows"]["news"]["steps"] == [{"id": "s1", "agent": "search"}]
    assert snap["workflows"]["news"]["scope"] == ["search.web"]


def test_restore_workflows(mgr: StateManager, storage: SQLiteStorage):
    """Restore should recreate workflows from snapshot."""
    storage.execute(
        "INSERT INTO workflows (id, name, steps) VALUES (?, ?, ?)",
        ("wf1", "Test WF", '[{"id": "s1"}]'),
    )
    snap = mgr.snapshot()
    storage.execute("DELETE FROM workflows")
    assert storage.fetchall("SELECT * FROM workflows") == []
    mgr.restore(snap)
    rows = storage.fetchall("SELECT * FROM workflows")
    assert len(rows) == 1
    assert rows[0]["id"] == "wf1"
    assert rows[0]["name"] == "Test WF"


def test_rollback_restores_workflows(mgr: StateManager, storage: SQLiteStorage):
    """Full round-trip: add workflow, snapshot, remove, restore."""
    storage.execute(
        "INSERT INTO workflows (id, name, steps) VALUES (?, ?, ?)",
        ("wf1", "Original", "[]"),
    )
    snap = mgr.snapshot()
    # Add another workflow
    storage.execute(
        "INSERT INTO workflows (id, name, steps) VALUES (?, ?, ?)",
        ("wf2", "Extra", "[]"),
    )
    # Restore should bring back only wf1
    mgr.restore(snap)
    rows = storage.fetchall("SELECT * FROM workflows")
    assert len(rows) == 1
    assert rows[0]["id"] == "wf1"


def test_full_round_trip(mgr: StateManager, storage: SQLiteStorage):
    """Snapshot -> modify -> restore -> verify original state."""
    _setup_connector(storage, "ddg", "DDG", ["search.web"])
    _setup_agent(storage, "a1", "agent1", ["cap.a"])
    storage.execute(
        "INSERT INTO policies (id, user_id, resource, decision) VALUES (?, ?, ?, ?)",
        ("p1", "default", "cap.a", "always"),
    )
    storage.execute("INSERT INTO llm_models (id, provider, tier) VALUES (?, ?, ?)", ("m1", "test", "haiku"))
    storage.execute(
        "INSERT INTO agent_llm_models (agent_id, model_id, priority, purpose) VALUES (?, ?, ?, ?)",
        (None, "m1", 1, "execution"),
    )
    storage.execute(
        "INSERT INTO workflows (id, name, steps) VALUES (?, ?, ?)",
        ("wf1", "Test WF", "[]"),
    )

    snap = mgr.snapshot()

    # Modify everything
    _setup_connector(storage, "brave", "Brave", ["search.brave"])
    storage.execute(
        "INSERT INTO agent_capabilities (agent_id, capability) VALUES (?, ?)",
        ("a1", "cap.b"),
    )
    storage.execute(
        "INSERT INTO policies (id, user_id, resource, decision) VALUES (?, ?, ?, ?)",
        ("p2", "default", "cap.b", "confirm"),
    )
    storage.execute(
        "INSERT INTO workflows (id, name, steps) VALUES (?, ?, ?)",
        ("wf2", "Extra WF", "[]"),
    )

    # Restore original
    mgr.restore(snap)

    # Verify
    connectors = storage.fetchall("SELECT * FROM connectors")
    assert len(connectors) == 1
    assert connectors[0]["id"] == "ddg"

    caps = storage.fetchall("SELECT capability FROM agent_capabilities WHERE agent_id = ?", ("a1",))
    assert [c["capability"] for c in caps] == ["cap.a"]

    policies = storage.fetchall("SELECT * FROM policies")
    assert len(policies) == 1
    assert policies[0]["resource"] == "cap.a"

    workflows = storage.fetchall("SELECT * FROM workflows")
    assert len(workflows) == 1
    assert workflows[0]["id"] == "wf1"


def test_restore_is_atomic_on_failure(mgr: StateManager, storage: SQLiteStorage):
    """If restore() fails partway, the previous state must remain intact.

    We seed the DB with one connector, snapshot it, then trigger a failing
    restore by feeding a snapshot whose data violates a NOT NULL constraint
    (missing connector_type). The restore must roll back cleanly — leaving
    the original connector still present.
    """
    storage.execute(
        "INSERT INTO connectors (id, name, connector_type, status) VALUES (?, ?, ?, ?)",
        ("original", "Original", "mcp", "active"),
    )
    good_snap = mgr.snapshot()

    # Build a poisoned snapshot: connector missing connector_type
    bad_snap = {**good_snap, "connectors": {"broken": {"name": "Broken"}}}

    with pytest.raises(Exception):
        mgr.restore(bad_snap)

    # Original must still be there — nothing half-applied
    rows = storage.fetchall("SELECT id FROM connectors")
    assert [r["id"] for r in rows] == ["original"]
