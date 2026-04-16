"""Tests for ScheduleManager and cron parsing."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from mycelos.config.state_manager import StateManager
from mycelos.scheduler.schedule_manager import (
    ScheduleManager,
    _matches,
    parse_next_run,
)
from mycelos.storage.database import SQLiteStorage


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_storage(db_path: Path) -> SQLiteStorage:
    storage = SQLiteStorage(db_path)
    storage.initialize()
    return storage


def _make_manager(db_path: Path) -> tuple[ScheduleManager, SQLiteStorage]:
    storage = _make_storage(db_path)
    return ScheduleManager(storage), storage


def _insert_workflow(storage: SQLiteStorage, workflow_id: str = "wf-1") -> str:
    """Insert a minimal workflow row so FK constraint is satisfied."""
    storage.execute(
        """INSERT INTO workflows (id, name, steps, created_by)
           VALUES (?, ?, ?, ?)""",
        (workflow_id, "test-wf", "[]", "system"),
    )
    return workflow_id


# ---------------------------------------------------------------------------
# _matches tests
# ---------------------------------------------------------------------------


class TestMatches:
    def test_wildcard(self) -> None:
        assert _matches(0, "*") is True
        assert _matches(59, "*") is True

    def test_exact(self) -> None:
        assert _matches(5, "5") is True
        assert _matches(6, "5") is False

    def test_step(self) -> None:
        assert _matches(0, "*/5") is True
        assert _matches(5, "*/5") is True
        assert _matches(10, "*/5") is True
        assert _matches(3, "*/5") is False

    def test_range(self) -> None:
        assert _matches(1, "1-5") is True
        assert _matches(3, "1-5") is True
        assert _matches(5, "1-5") is True
        assert _matches(0, "1-5") is False
        assert _matches(6, "1-5") is False

    def test_comma_list(self) -> None:
        assert _matches(1, "1,3,5") is True
        assert _matches(3, "1,3,5") is True
        assert _matches(2, "1,3,5") is False


# ---------------------------------------------------------------------------
# parse_next_run tests
# ---------------------------------------------------------------------------


class TestParseNextRun:
    def test_daily_at_8(self) -> None:
        after = datetime(2026, 3, 21, 7, 0, tzinfo=timezone.utc)
        result = parse_next_run("0 8 * * *", after=after)
        assert result.hour == 8
        assert result.minute == 0
        assert result.day == 21

    def test_every_5_minutes(self) -> None:
        after = datetime(2026, 3, 21, 10, 2, tzinfo=timezone.utc)
        result = parse_next_run("*/5 * * * *", after=after)
        assert result.minute % 5 == 0
        assert result > after

    def test_every_2_hours(self) -> None:
        after = datetime(2026, 3, 21, 9, 30, tzinfo=timezone.utc)
        result = parse_next_run("0 */2 * * *", after=after)
        assert result.minute == 0
        assert result.hour % 2 == 0
        assert result > after

    def test_invalid_expression_too_few_fields(self) -> None:
        with pytest.raises(ValueError, match="need 5 fields"):
            parse_next_run("0 8 * *")

    def test_invalid_expression_too_many_fields(self) -> None:
        with pytest.raises(ValueError, match="need 5 fields"):
            parse_next_run("0 8 * * * *")

    def test_result_is_utc(self) -> None:
        result = parse_next_run("*/5 * * * *")
        assert result.tzinfo == timezone.utc

    def test_next_run_is_after_given_time(self) -> None:
        after = datetime(2026, 3, 21, 12, 0, tzinfo=timezone.utc)
        result = parse_next_run("0 8 * * *", after=after)
        assert result > after


# ---------------------------------------------------------------------------
# Schema tests
# ---------------------------------------------------------------------------


def test_scheduled_tasks_table_exists(db_path: Path) -> None:
    storage = _make_storage(db_path)
    rows = storage.fetchall(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='scheduled_tasks'"
    )
    assert len(rows) == 1


def test_scheduled_tasks_fk_constraint(db_path: Path) -> None:
    """Inserting a scheduled task without a valid workflow_id should fail."""
    storage = _make_storage(db_path)
    with pytest.raises(Exception):
        storage.execute(
            """INSERT INTO scheduled_tasks (id, workflow_id, schedule)
               VALUES ('t1', 'nonexistent', '*/5 * * * *')"""
        )


# ---------------------------------------------------------------------------
# ScheduleManager CRUD tests
# ---------------------------------------------------------------------------


class TestScheduleManagerCRUD:
    def test_add_and_get(self, db_path: Path) -> None:
        mgr, storage = _make_manager(db_path)
        wf_id = _insert_workflow(storage)
        task_id = mgr.add(wf_id, "0 8 * * *", inputs={"key": "val"})

        task = mgr.get(task_id)
        assert task is not None
        assert task["workflow_id"] == wf_id
        assert task["schedule"] == "0 8 * * *"
        assert task["inputs"] == {"key": "val"}
        assert task["status"] == "active"
        assert task["run_count"] == 0
        assert task["next_run"] is not None

    def test_add_with_budget(self, db_path: Path) -> None:
        mgr, storage = _make_manager(db_path)
        wf_id = _insert_workflow(storage)
        task_id = mgr.add(wf_id, "*/5 * * * *", budget_per_run=0.50)

        task = mgr.get(task_id)
        assert task is not None
        assert task["budget_per_run"] == 0.50

    def test_get_nonexistent(self, db_path: Path) -> None:
        mgr, _ = _make_manager(db_path)
        assert mgr.get("nonexistent") is None

    def test_list_tasks_all(self, db_path: Path) -> None:
        mgr, storage = _make_manager(db_path)
        wf_id = _insert_workflow(storage)
        mgr.add(wf_id, "0 8 * * *")
        mgr.add(wf_id, "0 9 * * *")

        tasks = mgr.list_tasks()
        assert len(tasks) == 2

    def test_list_tasks_by_status(self, db_path: Path) -> None:
        mgr, storage = _make_manager(db_path)
        wf_id = _insert_workflow(storage)
        tid1 = mgr.add(wf_id, "0 8 * * *")
        mgr.add(wf_id, "0 9 * * *")
        mgr.pause(tid1)

        active = mgr.list_tasks(status="active")
        paused = mgr.list_tasks(status="paused")
        assert len(active) == 1
        assert len(paused) == 1

    def test_list_tasks_by_user_id(self, db_path: Path) -> None:
        mgr, storage = _make_manager(db_path)
        wf_id = _insert_workflow(storage)
        mgr.add(wf_id, "0 8 * * *", user_id="alice")
        mgr.add(wf_id, "0 9 * * *", user_id="bob")

        alice_tasks = mgr.list_tasks(user_id="alice")
        assert len(alice_tasks) == 1
        assert alice_tasks[0]["user_id"] == "alice"

    def test_delete(self, db_path: Path) -> None:
        mgr, storage = _make_manager(db_path)
        wf_id = _insert_workflow(storage)
        task_id = mgr.add(wf_id, "0 8 * * *")
        mgr.delete(task_id)

        assert mgr.get(task_id) is None
        assert mgr.list_tasks() == []


# ---------------------------------------------------------------------------
# Pause / Resume tests
# ---------------------------------------------------------------------------


class TestPauseResume:
    def test_pause(self, db_path: Path) -> None:
        mgr, storage = _make_manager(db_path)
        wf_id = _insert_workflow(storage)
        task_id = mgr.add(wf_id, "0 8 * * *")
        mgr.pause(task_id)

        task = mgr.get(task_id)
        assert task is not None
        assert task["status"] == "paused"

    def test_resume(self, db_path: Path) -> None:
        mgr, storage = _make_manager(db_path)
        wf_id = _insert_workflow(storage)
        task_id = mgr.add(wf_id, "0 8 * * *")
        mgr.pause(task_id)
        mgr.resume(task_id)

        task = mgr.get(task_id)
        assert task is not None
        assert task["status"] == "active"
        assert task["next_run"] is not None


# ---------------------------------------------------------------------------
# Due tasks / mark_executed tests
# ---------------------------------------------------------------------------


class TestDueTasksAndExecution:
    def test_get_due_tasks(self, db_path: Path) -> None:
        mgr, storage = _make_manager(db_path)
        wf_id = _insert_workflow(storage)
        task_id = mgr.add(wf_id, "*/5 * * * *")

        # Force next_run to the past
        past = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
        storage.execute(
            "UPDATE scheduled_tasks SET next_run = ? WHERE id = ?",
            (past, task_id),
        )

        due = mgr.get_due_tasks()
        assert len(due) == 1
        assert due[0]["id"] == task_id

    def test_get_due_tasks_excludes_paused(self, db_path: Path) -> None:
        mgr, storage = _make_manager(db_path)
        wf_id = _insert_workflow(storage)
        task_id = mgr.add(wf_id, "*/5 * * * *")
        past = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
        storage.execute(
            "UPDATE scheduled_tasks SET next_run = ? WHERE id = ?",
            (past, task_id),
        )
        mgr.pause(task_id)

        due = mgr.get_due_tasks()
        assert len(due) == 0

    def test_get_due_tasks_excludes_future(self, db_path: Path) -> None:
        mgr, storage = _make_manager(db_path)
        wf_id = _insert_workflow(storage)
        mgr.add(wf_id, "*/5 * * * *")
        # next_run is already in the future by default
        due = mgr.get_due_tasks()
        assert len(due) == 0

    def test_mark_executed(self, db_path: Path) -> None:
        mgr, storage = _make_manager(db_path)
        wf_id = _insert_workflow(storage)
        task_id = mgr.add(wf_id, "*/5 * * * *")

        original = mgr.get(task_id)
        assert original is not None
        assert original["run_count"] == 0
        assert original["last_run"] is None

        mgr.mark_executed(task_id)

        updated = mgr.get(task_id)
        assert updated is not None
        assert updated["run_count"] == 1
        assert updated["last_run"] is not None
        # next_run is recalculated (may be same slot within same minute, so just check it exists)
        assert updated["next_run"] is not None

    def test_mark_executed_increments(self, db_path: Path) -> None:
        mgr, storage = _make_manager(db_path)
        wf_id = _insert_workflow(storage)
        task_id = mgr.add(wf_id, "*/5 * * * *")

        mgr.mark_executed(task_id)
        mgr.mark_executed(task_id)
        mgr.mark_executed(task_id)

        task = mgr.get(task_id)
        assert task is not None
        assert task["run_count"] == 3

    def test_mark_executed_nonexistent(self, db_path: Path) -> None:
        mgr, _ = _make_manager(db_path)
        # Should not raise
        mgr.mark_executed("nonexistent")


# ---------------------------------------------------------------------------
# StateManager integration tests
# ---------------------------------------------------------------------------


class TestStateManagerIntegration:
    def test_snapshot_includes_scheduled_tasks(self, db_path: Path) -> None:
        storage = _make_storage(db_path)
        wf_id = _insert_workflow(storage)
        mgr = ScheduleManager(storage)
        task_id = mgr.add(wf_id, "0 8 * * *", inputs={"foo": "bar"}, budget_per_run=1.0)

        state_mgr = StateManager(storage)
        snap = state_mgr.snapshot()

        assert "scheduled_tasks" in snap
        assert task_id in snap["scheduled_tasks"]
        entry = snap["scheduled_tasks"][task_id]
        assert entry["workflow_id"] == wf_id
        assert entry["schedule"] == "0 8 * * *"
        assert entry["inputs"] == {"foo": "bar"}
        assert entry["budget_per_run"] == 1.0

    def test_restore_recreates_scheduled_tasks(self, db_path: Path) -> None:
        storage = _make_storage(db_path)
        wf_id = _insert_workflow(storage)
        mgr = ScheduleManager(storage)
        task_id = mgr.add(wf_id, "0 8 * * *", inputs={"x": 1})

        state_mgr = StateManager(storage)
        snap = state_mgr.snapshot()

        # Delete the task
        mgr.delete(task_id)
        assert mgr.get(task_id) is None

        # Restore
        state_mgr.restore(snap)

        restored = mgr.get(task_id)
        assert restored is not None
        assert restored["workflow_id"] == wf_id
        assert restored["schedule"] == "0 8 * * *"
        assert restored["inputs"] == {"x": 1}
        assert restored["status"] == "active"

    def test_snapshot_empty_scheduled_tasks(self, db_path: Path) -> None:
        storage = _make_storage(db_path)
        state_mgr = StateManager(storage)
        snap = state_mgr.snapshot()
        assert snap["scheduled_tasks"] == {}

    def test_restore_without_scheduled_tasks_key(self, db_path: Path) -> None:
        """Restoring a snapshot without scheduled_tasks should not fail."""
        storage = _make_storage(db_path)
        state_mgr = StateManager(storage)
        snap = state_mgr.snapshot()
        del snap["scheduled_tasks"]
        # Should not raise
        state_mgr.restore(snap)


# ---------------------------------------------------------------------------
# App integration test
# ---------------------------------------------------------------------------


def test_app_schedule_manager_property(tmp_data_dir: Path) -> None:
    from mycelos.app import App

    app = App(tmp_data_dir)
    app.initialize()
    mgr = app.schedule_manager
    assert isinstance(mgr, ScheduleManager)
    # Second access returns same instance
    assert app.schedule_manager is mgr
