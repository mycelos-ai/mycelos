"""Tests for workflow schema — workflows + workflow_runs tables."""

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


# --- workflows table ---


def test_workflows_table_exists(storage: SQLiteStorage):
    storage.execute(
        """INSERT INTO workflows (id, name, steps) VALUES (?, ?, ?)""",
        ("wf1", "Test Workflow", '[{"id": "s1", "agent": "test"}]'),
    )
    row = storage.fetchone("SELECT * FROM workflows WHERE id = ?", ("wf1",))
    assert row is not None
    assert row["name"] == "Test Workflow"
    assert row["status"] == "active"


def test_workflows_default_values(storage: SQLiteStorage):
    storage.execute(
        "INSERT INTO workflows (id, name, steps) VALUES (?, ?, ?)",
        ("wf1", "Test", "[]"),
    )
    row = storage.fetchone("SELECT * FROM workflows WHERE id = ?", ("wf1",))
    assert row["version"] == 1
    assert row["status"] == "active"
    assert row["created_by"] == "system"
    assert row["created_at"] is not None


def test_workflows_with_all_fields(storage: SQLiteStorage):
    storage.execute(
        """INSERT INTO workflows (id, name, description, goal, version, steps, scope, tags, status, created_by)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        ("wf1", "News", "Search news", "Find news", 2, "[]",
         '["search.web"]', '["news"]', "draft", "user"),
    )
    row = storage.fetchone("SELECT * FROM workflows WHERE id = ?", ("wf1",))
    assert row["description"] == "Search news"
    assert row["version"] == 2
    assert row["scope"] == '["search.web"]'
    assert row["status"] == "draft"


# --- workflow_runs table ---


def test_workflow_runs_table_exists(storage: SQLiteStorage):
    # Need a workflow first (FK)
    storage.execute(
        "INSERT INTO workflows (id, name, steps) VALUES (?, ?, ?)",
        ("wf1", "Test", "[]"),
    )
    storage.execute(
        "INSERT INTO workflow_runs (id, workflow_id) VALUES (?, ?)",
        ("run1", "wf1"),
    )
    row = storage.fetchone("SELECT * FROM workflow_runs WHERE id = ?", ("run1",))
    assert row is not None
    assert row["status"] == "running"
    assert row["cost"] == 0.0


def test_workflow_runs_default_values(storage: SQLiteStorage):
    storage.execute(
        "INSERT INTO workflows (id, name, steps) VALUES (?, ?, ?)",
        ("wf1", "Test", "[]"),
    )
    storage.execute(
        "INSERT INTO workflow_runs (id, workflow_id) VALUES (?, ?)",
        ("run1", "wf1"),
    )
    row = storage.fetchone("SELECT * FROM workflow_runs WHERE id = ?", ("run1",))
    assert row["user_id"] == "default"
    assert row["retry_count"] == 0
    assert row["budget_limit"] is None


def test_workflow_runs_with_state(storage: SQLiteStorage):
    storage.execute(
        "INSERT INTO workflows (id, name, steps) VALUES (?, ?, ?)",
        ("wf1", "Test", "[]"),
    )
    storage.execute(
        """INSERT INTO workflow_runs (id, workflow_id, status, current_step,
           completed_steps, artifacts, cost, budget_limit)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        ("run1", "wf1", "paused", "step3",
         '["step1", "step2"]', '{"code_hash": "abc"}', 0.05, 1.0),
    )
    row = storage.fetchone("SELECT * FROM workflow_runs WHERE id = ?", ("run1",))
    assert row["status"] == "paused"
    assert row["current_step"] == "step3"
    assert row["completed_steps"] == '["step1", "step2"]'
    assert row["artifacts"] == '{"code_hash": "abc"}'
    assert row["cost"] == 0.05
    assert row["budget_limit"] == 1.0


def test_workflow_runs_fk_constraint(storage: SQLiteStorage):
    """workflow_runs must reference an existing workflow."""
    with pytest.raises(Exception):
        storage.execute(
            "INSERT INTO workflow_runs (id, workflow_id) VALUES (?, ?)",
            ("run1", "nonexistent"),
        )


def test_workflow_runs_status_update(storage: SQLiteStorage):
    storage.execute(
        "INSERT INTO workflows (id, name, steps) VALUES (?, ?, ?)",
        ("wf1", "Test", "[]"),
    )
    storage.execute(
        "INSERT INTO workflow_runs (id, workflow_id) VALUES (?, ?)",
        ("run1", "wf1"),
    )
    storage.execute(
        "UPDATE workflow_runs SET status = ?, current_step = ? WHERE id = ?",
        ("paused", "step2", "run1"),
    )
    row = storage.fetchone("SELECT * FROM workflow_runs WHERE id = ?", ("run1",))
    assert row["status"] == "paused"
    assert row["current_step"] == "step2"


# --- workflow_events with run_id ---


def test_workflow_events_has_run_id(storage: SQLiteStorage):
    storage.execute(
        "INSERT INTO workflows (id, name, steps) VALUES (?, ?, ?)",
        ("wf1", "Test", "[]"),
    )
    storage.execute(
        "INSERT INTO workflow_runs (id, workflow_id) VALUES (?, ?)",
        ("run1", "wf1"),
    )
    storage.execute(
        """INSERT INTO workflow_events (workflow_id, run_id, step_id, event_type)
           VALUES (?, ?, ?, ?)""",
        ("wf1", "run1", "step1", "started"),
    )
    row = storage.fetchone(
        "SELECT * FROM workflow_events WHERE run_id = ?", ("run1",)
    )
    assert row is not None
    assert row["step_id"] == "step1"


# --- Index check ---


def test_workflow_runs_indexes(storage: SQLiteStorage):
    """Indexes should exist for status and user queries."""
    indexes = storage.fetchall(
        "SELECT name FROM sqlite_master WHERE type='index' AND name LIKE 'idx_workflow_runs%'"
    )
    names = {i["name"] for i in indexes}
    assert "idx_workflow_runs_status" in names
    assert "idx_workflow_runs_user" in names


# --- session_id column for run ↔ session linking ---


def test_workflow_runs_has_session_id_column(storage: SQLiteStorage):
    """workflow_runs must have a nullable session_id column to link runs back
    to the chat session they were started from."""
    storage.execute(
        "INSERT INTO workflows (id, name, steps) VALUES (?, ?, ?)",
        ("wf1", "Test", "[]"),
    )
    storage.execute(
        "INSERT INTO workflow_runs (id, workflow_id, session_id) VALUES (?, ?, ?)",
        ("run1", "wf1", "sess-abc"),
    )
    row = storage.fetchone("SELECT session_id FROM workflow_runs WHERE id = ?", ("run1",))
    assert row is not None
    assert row["session_id"] == "sess-abc"


def test_workflow_runs_session_id_is_nullable(storage: SQLiteStorage):
    """Runs created without a session (e.g. cron-triggered) have session_id = NULL."""
    storage.execute(
        "INSERT INTO workflows (id, name, steps) VALUES (?, ?, ?)",
        ("wf1", "Test", "[]"),
    )
    storage.execute(
        "INSERT INTO workflow_runs (id, workflow_id) VALUES (?, ?)",
        ("run1", "wf1"),
    )
    row = storage.fetchone("SELECT session_id FROM workflow_runs WHERE id = ?", ("run1",))
    assert row["session_id"] is None


def test_workflow_runs_session_id_index(storage: SQLiteStorage):
    """Index on session_id so session inspector can fetch runs for a session cheaply."""
    indexes = storage.fetchall(
        "SELECT name FROM sqlite_master WHERE type='index' AND name LIKE 'idx_workflow_runs%'"
    )
    names = {i["name"] for i in indexes}
    assert "idx_workflow_runs_session_id" in names
