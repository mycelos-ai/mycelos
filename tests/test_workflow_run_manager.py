"""Tests for WorkflowRunManager — execution state tracking for workflow runs."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from mycelos.storage.database import SQLiteStorage
from mycelos.workflows.run_manager import WorkflowRunManager


@pytest.fixture
def storage() -> SQLiteStorage:
    with tempfile.TemporaryDirectory() as tmp:
        s = SQLiteStorage(Path(tmp) / "test.db")
        s.initialize()
        yield s
        s.close()


def _create_workflow(storage: SQLiteStorage, wf_id: str = "test-wf") -> None:
    """Insert a workflow row to satisfy FK constraint."""
    storage.execute(
        "INSERT INTO workflows (id, name, steps) VALUES (?, ?, ?)",
        (wf_id, "Test Workflow", "[]"),
    )


def _create_user(storage: SQLiteStorage, user_id: str) -> None:
    """Insert a user row to satisfy FK constraint."""
    storage.execute(
        "INSERT OR IGNORE INTO users (id, name, status) VALUES (?, ?, ?)",
        (user_id, user_id, "active"),
    )


@pytest.fixture
def mgr(storage: SQLiteStorage) -> WorkflowRunManager:
    _create_workflow(storage)
    return WorkflowRunManager(storage)


# --- start / get ---


def test_start_creates_run_with_defaults(mgr: WorkflowRunManager, storage: SQLiteStorage):
    run_id = mgr.start("test-wf")
    run = mgr.get(run_id)
    assert run is not None
    assert run["workflow_id"] == "test-wf"
    assert run["status"] == "running"
    assert run["user_id"] == "default"
    assert run["cost"] == 0.0
    assert run["retry_count"] == 0
    assert run["completed_steps"] == []
    assert run["artifacts"] == {}
    assert run["task_id"] is None
    assert run["budget_limit"] is None
    assert run["error"] is None


def test_start_with_all_params(mgr: WorkflowRunManager, storage: SQLiteStorage):
    _create_user(storage, "alice")
    storage.execute(
        "INSERT INTO tasks (id, goal) VALUES (?, ?)", ("t1", "test goal")
    )
    run_id = mgr.start("test-wf", task_id="t1", user_id="alice", budget_limit=10.0)
    run = mgr.get(run_id)
    assert run is not None
    assert run["task_id"] == "t1"
    assert run["user_id"] == "alice"
    assert run["budget_limit"] == 10.0


def test_get_nonexistent_returns_none(mgr: WorkflowRunManager):
    assert mgr.get("does-not-exist") is None


def test_get_returns_parsed_json_fields(mgr: WorkflowRunManager):
    run_id = mgr.start("test-wf")
    run = mgr.get(run_id)
    assert run is not None
    assert isinstance(run["completed_steps"], list)
    assert isinstance(run["artifacts"], dict)


# --- update_step ---


def test_update_step_adds_to_completed_and_updates_cost(mgr: WorkflowRunManager):
    run_id = mgr.start("test-wf")
    mgr.update_step(run_id, "step-1", status="done", cost=0.5)
    run = mgr.get(run_id)
    assert run is not None
    assert "step-1" in run["completed_steps"]
    assert run["current_step"] == "step-1"
    assert run["cost"] == 0.5


def test_update_step_merges_artifacts(mgr: WorkflowRunManager):
    run_id = mgr.start("test-wf")
    mgr.update_step(run_id, "step-1", artifacts={"file": "a.txt"})
    mgr.update_step(run_id, "step-2", artifacts={"report": "b.pdf"})
    run = mgr.get(run_id)
    assert run is not None
    assert run["artifacts"] == {"file": "a.txt", "report": "b.pdf"}


def test_update_step_accumulates_cost(mgr: WorkflowRunManager):
    run_id = mgr.start("test-wf")
    mgr.update_step(run_id, "step-1", cost=0.3)
    mgr.update_step(run_id, "step-2", cost=0.7)
    run = mgr.get(run_id)
    assert run is not None
    assert abs(run["cost"] - 1.0) < 1e-9


def test_update_step_does_not_duplicate_completed(mgr: WorkflowRunManager):
    run_id = mgr.start("test-wf")
    mgr.update_step(run_id, "step-1", status="done")
    mgr.update_step(run_id, "step-1", status="done")
    run = mgr.get(run_id)
    assert run is not None
    assert run["completed_steps"].count("step-1") == 1


def test_update_step_nonexistent_raises(mgr: WorkflowRunManager):
    with pytest.raises(ValueError, match="not found"):
        mgr.update_step("nope", "step-1")


# --- pause / resume ---


def test_pause_running_workflow(mgr: WorkflowRunManager):
    run_id = mgr.start("test-wf")
    mgr.pause(run_id, reason="user request")
    run = mgr.get(run_id)
    assert run is not None
    assert run["status"] == "paused"
    assert "paused: user request" in run["error"]


def test_resume_paused_workflow(mgr: WorkflowRunManager):
    run_id = mgr.start("test-wf")
    mgr.pause(run_id)
    result = mgr.resume(run_id)
    assert result["status"] == "running"
    assert result["error"] is None


# --- wait_for_input ---


def test_wait_for_input(mgr: WorkflowRunManager):
    run_id = mgr.start("test-wf")
    mgr.wait_for_input(run_id, prompt="Enter API key")
    run = mgr.get(run_id)
    assert run is not None
    assert run["status"] == "waiting_input"
    assert "waiting: Enter API key" in run["error"]


def test_resume_from_waiting_input(mgr: WorkflowRunManager):
    run_id = mgr.start("test-wf")
    mgr.wait_for_input(run_id)
    result = mgr.resume(run_id)
    assert result["status"] == "running"
    assert result["error"] is None


# --- complete / fail ---


def test_complete_marks_as_completed(mgr: WorkflowRunManager):
    run_id = mgr.start("test-wf")
    mgr.complete(run_id)
    run = mgr.get(run_id)
    assert run is not None
    assert run["status"] == "completed"


def test_fail_marks_as_failed_with_error(mgr: WorkflowRunManager):
    run_id = mgr.start("test-wf")
    mgr.fail(run_id, error="timeout after 30s")
    run = mgr.get(run_id)
    assert run is not None
    assert run["status"] == "failed"
    assert run["error"] == "timeout after 30s"


# --- abort ---


def test_abort_from_paused(mgr: WorkflowRunManager):
    run_id = mgr.start("test-wf")
    mgr.pause(run_id)
    mgr.abort(run_id)
    run = mgr.get(run_id)
    assert run is not None
    assert run["status"] == "aborted"


def test_abort_from_waiting_input(mgr: WorkflowRunManager):
    run_id = mgr.start("test-wf")
    mgr.wait_for_input(run_id)
    mgr.abort(run_id)
    run = mgr.get(run_id)
    assert run is not None
    assert run["status"] == "aborted"


def test_abort_from_completed_raises(mgr: WorkflowRunManager):
    run_id = mgr.start("test-wf")
    mgr.complete(run_id)
    with pytest.raises(ValueError, match="Cannot abort"):
        mgr.abort(run_id)


def test_abort_from_failed_raises(mgr: WorkflowRunManager):
    run_id = mgr.start("test-wf")
    mgr.fail(run_id, error="boom")
    with pytest.raises(ValueError, match="Cannot abort"):
        mgr.abort(run_id)


def test_abort_from_running_raises(mgr: WorkflowRunManager):
    run_id = mgr.start("test-wf")
    with pytest.raises(ValueError, match="Cannot abort"):
        mgr.abort(run_id)


# --- invalid transitions ---


def test_invalid_transition_completed_to_running(mgr: WorkflowRunManager):
    run_id = mgr.start("test-wf")
    mgr.complete(run_id)
    with pytest.raises(ValueError, match="Cannot transition"):
        mgr.resume(run_id)


def test_invalid_transition_failed_to_paused(mgr: WorkflowRunManager):
    run_id = mgr.start("test-wf")
    mgr.fail(run_id, error="err")
    with pytest.raises(ValueError, match="Cannot transition"):
        mgr.pause(run_id)


def test_invalid_transition_paused_to_completed(mgr: WorkflowRunManager):
    run_id = mgr.start("test-wf")
    mgr.pause(run_id)
    with pytest.raises(ValueError, match="Cannot transition"):
        mgr.complete(run_id)


# --- increment_retry ---


def test_increment_retry_returns_new_count(mgr: WorkflowRunManager):
    run_id = mgr.start("test-wf")
    assert mgr.increment_retry(run_id) == 1
    assert mgr.increment_retry(run_id) == 2
    assert mgr.increment_retry(run_id) == 3


# --- check_budget ---


def test_check_budget_within_limit(mgr: WorkflowRunManager):
    run_id = mgr.start("test-wf", budget_limit=5.0)
    mgr.update_step(run_id, "s1", cost=2.0)
    assert mgr.check_budget(run_id) is True


def test_check_budget_exceeded(mgr: WorkflowRunManager):
    run_id = mgr.start("test-wf", budget_limit=1.0)
    mgr.update_step(run_id, "s1", cost=2.0)
    assert mgr.check_budget(run_id) is False


def test_check_budget_no_limit_returns_true(mgr: WorkflowRunManager):
    run_id = mgr.start("test-wf")
    mgr.update_step(run_id, "s1", cost=999.0)
    assert mgr.check_budget(run_id) is True


def test_check_budget_nonexistent_returns_false(mgr: WorkflowRunManager):
    assert mgr.check_budget("nope") is False


# --- list_runs ---


def test_list_runs_no_filter(mgr: WorkflowRunManager, storage: SQLiteStorage):
    _create_user(storage, "alice")
    _create_user(storage, "bob")
    mgr.start("test-wf", user_id="alice")
    mgr.start("test-wf", user_id="bob")
    runs = mgr.list_runs()
    assert len(runs) == 2


def test_list_runs_filter_by_status(mgr: WorkflowRunManager):
    r1 = mgr.start("test-wf")
    r2 = mgr.start("test-wf")
    mgr.complete(r1)
    runs = mgr.list_runs(status="completed")
    assert len(runs) == 1
    assert runs[0]["id"] == r1


def test_list_runs_filter_by_user(mgr: WorkflowRunManager, storage: SQLiteStorage):
    _create_user(storage, "alice")
    _create_user(storage, "bob")
    mgr.start("test-wf", user_id="alice")
    mgr.start("test-wf", user_id="bob")
    runs = mgr.list_runs(user_id="alice")
    assert len(runs) == 1
    assert runs[0]["user_id"] == "alice"


def test_list_runs_filter_by_workflow(mgr: WorkflowRunManager, storage: SQLiteStorage):
    _create_workflow(storage, "other-wf")
    mgr.start("test-wf")
    mgr.start("other-wf")
    runs = mgr.list_runs(workflow_id="test-wf")
    assert len(runs) == 1
    assert runs[0]["workflow_id"] == "test-wf"


# --- get_pending_runs ---


def test_get_pending_runs(mgr: WorkflowRunManager):
    r1 = mgr.start("test-wf", user_id="default")
    r2 = mgr.start("test-wf", user_id="default")
    r3 = mgr.start("test-wf", user_id="default")
    mgr.pause(r1)
    mgr.wait_for_input(r2)
    # r3 stays running — should NOT appear
    pending = mgr.get_pending_runs("default")
    pending_ids = {r["id"] for r in pending}
    assert r1 in pending_ids
    assert r2 in pending_ids
    assert r3 not in pending_ids


def test_get_pending_runs_includes_workflow_name(mgr: WorkflowRunManager):
    r1 = mgr.start("test-wf", user_id="default")
    mgr.pause(r1)
    pending = mgr.get_pending_runs("default")
    assert len(pending) == 1
    assert pending[0]["workflow_name"] == "Test Workflow"


# --- full lifecycle ---


def test_full_lifecycle_start_step_complete(mgr: WorkflowRunManager):
    """Run through a complete happy-path lifecycle."""
    run_id = mgr.start("test-wf", budget_limit=10.0)
    mgr.update_step(run_id, "step-1", status="done", cost=1.0, artifacts={"out": "v1"})
    mgr.update_step(run_id, "step-2", status="done", cost=2.0, artifacts={"out2": "v2"})
    mgr.complete(run_id)

    run = mgr.get(run_id)
    assert run is not None
    assert run["status"] == "completed"
    assert run["completed_steps"] == ["step-1", "step-2"]
    assert run["artifacts"] == {"out": "v1", "out2": "v2"}
    assert abs(run["cost"] - 3.0) < 1e-9
    assert mgr.check_budget(run_id) is True


def test_full_lifecycle_pause_resume_complete(mgr: WorkflowRunManager):
    """Pause mid-workflow, resume, then complete."""
    run_id = mgr.start("test-wf")
    mgr.update_step(run_id, "step-1", status="done")
    mgr.pause(run_id, reason="lunch break")

    run = mgr.get(run_id)
    assert run is not None
    assert run["status"] == "paused"

    resumed = mgr.resume(run_id)
    assert resumed["status"] == "running"

    mgr.update_step(run_id, "step-2", status="done")
    mgr.complete(run_id)

    run = mgr.get(run_id)
    assert run is not None
    assert run["status"] == "completed"
    assert run["completed_steps"] == ["step-1", "step-2"]
