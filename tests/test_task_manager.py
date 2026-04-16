"""Tests for TaskManager."""

from pathlib import Path

import pytest

from mycelos.storage.database import SQLiteStorage
from mycelos.tasks.manager import VALID_STATUSES, TaskManager


def make_manager(db_path: Path) -> tuple[TaskManager, SQLiteStorage]:
    storage = SQLiteStorage(db_path)
    storage.initialize()
    return TaskManager(storage), storage


def _insert_agent(storage: SQLiteStorage, agent_id: str) -> None:
    """Insert a stub agent row to satisfy the foreign key on attempts."""
    storage.execute(
        "INSERT OR IGNORE INTO agents (id, name, agent_type) VALUES (?, ?, ?)",
        (agent_id, agent_id, "deterministic"),
    )


def test_create_task(db_path: Path) -> None:
    mgr, _ = make_manager(db_path)
    task_id = mgr.create("Summarize my emails", user_id="stefan")
    task = mgr.get(task_id)
    assert task is not None
    assert task["goal"] == "Summarize my emails"
    assert task["status"] == "pending"
    assert task["user_id"] == "stefan"


def test_status_lifecycle(db_path: Path) -> None:
    mgr, _ = make_manager(db_path)
    tid = mgr.create("Test")
    for status in ["planning", "awaiting", "running", "completed"]:
        mgr.update_status(tid, status)
        assert mgr.get(tid)["status"] == status


def test_invalid_status_raises(db_path: Path) -> None:
    mgr, _ = make_manager(db_path)
    tid = mgr.create("Test")
    with pytest.raises(ValueError, match="Invalid status"):
        mgr.update_status(tid, "invalid_status")


def test_all_terminal_statuses(db_path: Path) -> None:
    mgr, _ = make_manager(db_path)
    for status in ["completed", "failed", "aborted", "timeout", "partial"]:
        tid = mgr.create(f"Task for {status}")
        mgr.update_status(tid, status)
        assert mgr.get(tid)["status"] == status


def test_set_result_creates_attempt(db_path: Path) -> None:
    mgr, storage = make_manager(db_path)
    _insert_agent(storage, "email-agent")
    tid = mgr.create("Task")
    mgr.update_status(tid, "running")
    mgr.set_result(tid, result={"summary": "Done"}, cost=0.003, agent_id="email-agent")
    assert mgr.get(tid)["status"] == "completed"
    attempts = mgr.get_attempts(tid)
    assert len(attempts) == 1
    assert attempts[0]["cost"] == 0.003
    assert attempts[0]["success"] == 1


def test_set_result_failed(db_path: Path) -> None:
    mgr, storage = make_manager(db_path)
    _insert_agent(storage, "bad-agent")
    tid = mgr.create("Task")
    mgr.set_result(tid, result=None, status="failed", agent_id="bad-agent")
    assert mgr.get(tid)["status"] == "failed"
    attempts = mgr.get_attempts(tid)
    assert attempts[0]["success"] == 0


def test_list_by_status(db_path: Path) -> None:
    mgr, _ = make_manager(db_path)
    t1 = mgr.create("Task 1")
    t2 = mgr.create("Task 2")
    mgr.update_status(t1, "completed")
    pending = mgr.list_tasks(status="pending")
    assert len(pending) == 1
    assert pending[0]["id"] == t2


def test_list_recent(db_path: Path) -> None:
    mgr, _ = make_manager(db_path)
    for i in range(5):
        mgr.create(f"Task {i}")
    assert len(mgr.list_tasks(limit=3)) == 3


def test_get_nonexistent(db_path: Path) -> None:
    mgr, _ = make_manager(db_path)
    assert mgr.get("nonexistent") is None


def test_create_with_budget(db_path: Path) -> None:
    mgr, _ = make_manager(db_path)
    tid = mgr.create("Expensive task", budget=1.50)
    task = mgr.get(tid)
    assert task["budget"] == 1.50


def test_valid_statuses_constant() -> None:
    """All expected statuses are in the valid set."""
    expected = {
        "pending",
        "planning",
        "awaiting",
        "running",
        "paused",
        "completed",
        "failed",
        "aborted",
        "timeout",
        "partial",
    }
    assert VALID_STATUSES == expected
