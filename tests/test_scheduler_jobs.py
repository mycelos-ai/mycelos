"""Tests for scheduled workflow execution jobs."""

from __future__ import annotations

import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from mycelos.app import App
from mycelos.scheduler.jobs import check_scheduled_workflows, notify_completed_workflows
from mycelos.workflows.agent import WorkflowAgentResult


@pytest.fixture
def app() -> App:
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["MYCELOS_MASTER_KEY"] = "test-key-scheduler-jobs"
        a = App(Path(tmp))
        a.initialize()
        yield a


def _create_test_workflow(app: App, wf_id: str = "test-wf") -> None:
    app.workflow_registry.register(
        wf_id,
        wf_id,
        [{"id": "s1", "agent": "search-agent", "action": "Search"}],
        scope=["search.web"],
        plan="You are a test agent. Call search_web with the given query.",
        model="haiku",
        allowed_tools=["search_web"],
    )


def _make_due(app: App, task_id: str) -> None:
    """Force a task's next_run into the past so it becomes due."""
    app.storage.execute(
        "UPDATE scheduled_tasks SET next_run = ? WHERE id = ?",
        (
            (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(),
            task_id,
        ),
    )


def _mock_agent_result(status: str = "completed", error: str = "") -> WorkflowAgentResult:
    return WorkflowAgentResult(status=status, result="Test result.", error=error)


def test_no_due_tasks(app: App) -> None:
    """No scheduled tasks means nothing executed."""
    result = check_scheduled_workflows(app)
    assert result == []


def test_due_task_executed(app: App) -> None:
    """Due task should be executed."""
    _create_test_workflow(app)
    task_id = app.schedule_manager.add("test-wf", "*/5 * * * *")
    _make_due(app, task_id)

    mock_result = _mock_agent_result()
    with patch("mycelos.workflows.agent.WorkflowAgent") as MockAgent:
        MockAgent.return_value.execute.return_value = mock_result
        executed = check_scheduled_workflows(app)

    assert task_id in executed


def test_executed_task_updates_next_run(app: App) -> None:
    """After execution, next_run should be recalculated."""
    _create_test_workflow(app)
    task_id = app.schedule_manager.add("test-wf", "*/5 * * * *")
    _make_due(app, task_id)

    mock_result = _mock_agent_result()
    with patch("mycelos.workflows.agent.WorkflowAgent") as MockAgent:
        MockAgent.return_value.execute.return_value = mock_result
        check_scheduled_workflows(app)

    task = app.schedule_manager.get(task_id)
    assert task is not None
    assert task["run_count"] == 1
    assert task["last_run"] is not None
    # next_run should be in the future
    next_run = datetime.fromisoformat(task["next_run"])
    assert next_run > datetime.now(timezone.utc) - timedelta(minutes=1)


def test_future_task_not_executed(app: App) -> None:
    """Task with future next_run should not be executed."""
    _create_test_workflow(app)
    task_id = app.schedule_manager.add("test-wf", "0 8 * * *")
    # next_run is calculated by add() -- should be in the future
    executed = check_scheduled_workflows(app)
    assert task_id not in executed


def test_paused_task_not_executed(app: App) -> None:
    """Paused tasks should not be executed."""
    _create_test_workflow(app)
    task_id = app.schedule_manager.add("test-wf", "*/5 * * * *")
    _make_due(app, task_id)
    app.schedule_manager.pause(task_id)

    executed = check_scheduled_workflows(app)
    assert task_id not in executed


def test_missing_workflow_skipped(app: App) -> None:
    """If workflow definition is missing, task is skipped (not executed)."""
    _create_test_workflow(app, "temp-wf")
    task_id = app.schedule_manager.add("temp-wf", "*/5 * * * *")
    _make_due(app, task_id)

    # Simulate missing workflow by making get() return None
    with patch.object(app.workflow_registry, "get", return_value=None):
        executed = check_scheduled_workflows(app)
    assert task_id not in executed


def test_failed_workflow_still_marked(app: App) -> None:
    """Even if workflow execution fails, mark as executed to avoid retry loop."""
    _create_test_workflow(app)
    task_id = app.schedule_manager.add("test-wf", "*/5 * * * *")
    _make_due(app, task_id)

    mock_result = _mock_agent_result(status="failed", error="timeout")
    with patch("mycelos.workflows.agent.WorkflowAgent") as MockAgent:
        MockAgent.return_value.execute.return_value = mock_result
        executed = check_scheduled_workflows(app)

    assert task_id in executed
    task = app.schedule_manager.get(task_id)
    assert task is not None
    assert task["run_count"] == 1


def test_exception_still_marks_executed(app: App) -> None:
    """If agent raises an exception, task is still marked to prevent loops."""
    _create_test_workflow(app)
    task_id = app.schedule_manager.add("test-wf", "*/5 * * * *")
    _make_due(app, task_id)

    with patch("mycelos.workflows.agent.WorkflowAgent") as MockAgent:
        MockAgent.return_value.execute.side_effect = RuntimeError("boom")
        executed = check_scheduled_workflows(app)

    assert task_id in executed
    task = app.schedule_manager.get(task_id)
    assert task is not None
    assert task["run_count"] == 1


def test_audit_logged(app: App) -> None:
    """Execution should be audit-logged."""
    _create_test_workflow(app)
    task_id = app.schedule_manager.add("test-wf", "*/5 * * * *")
    _make_due(app, task_id)

    mock_result = _mock_agent_result()
    with patch("mycelos.workflows.agent.WorkflowAgent") as MockAgent:
        MockAgent.return_value.execute.return_value = mock_result
        check_scheduled_workflows(app)

    events = app.storage.fetchall(
        "SELECT * FROM audit_events WHERE event_type = 'scheduled.executed'"
    )
    assert len(events) >= 1


def test_multiple_due_tasks(app: App) -> None:
    """Multiple due tasks should all be executed."""
    _create_test_workflow(app, "wf-a")
    _create_test_workflow(app, "wf-b")
    tid_a = app.schedule_manager.add("wf-a", "*/5 * * * *")
    tid_b = app.schedule_manager.add("wf-b", "*/5 * * * *")
    _make_due(app, tid_a)
    _make_due(app, tid_b)

    mock_result = _mock_agent_result()
    with patch("mycelos.workflows.agent.WorkflowAgent") as MockAgent:
        MockAgent.return_value.execute.return_value = mock_result
        executed = check_scheduled_workflows(app)

    assert tid_a in executed
    assert tid_b in executed


# --- Workflow notification tests ---


def _insert_completed_run(app: App, run_id: str = "run-1", workflow_id: str = "test-wf",
                          result: str = "Test result") -> None:
    """Insert a completed but unnotified workflow run (with required workflow FK)."""
    import json
    _create_test_workflow(app, workflow_id)
    app.storage.execute(
        """INSERT INTO workflow_runs (id, workflow_id, user_id, status, artifacts, created_at)
           VALUES (?, ?, 'default', 'completed', ?, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))""",
        (run_id, workflow_id, json.dumps({"result": result})),
    )


def test_notify_marks_notified_on_success(app: App) -> None:
    """Successful Telegram send should mark the run as notified."""
    _insert_completed_run(app, "run-ok", "wf-notify-ok")

    with patch("mycelos.channels.telegram.send_notification", return_value=True):
        count = notify_completed_workflows(app)

    assert count == 1
    row = app.storage.fetchone("SELECT notified_at FROM workflow_runs WHERE id = 'run-ok'")
    assert row["notified_at"] is not None


def test_notify_skips_mark_on_failure(app: App) -> None:
    """Failed Telegram send should NOT mark the run as notified."""
    _insert_completed_run(app, "run-fail", "wf-notify-fail")

    with patch("mycelos.channels.telegram.send_notification", return_value=False):
        count = notify_completed_workflows(app)

    assert count == 0
    row = app.storage.fetchone("SELECT notified_at FROM workflow_runs WHERE id = 'run-fail'")
    assert row["notified_at"] is None


def test_notify_skips_mark_on_exception(app: App) -> None:
    """Exception in Telegram send should NOT mark the run as notified."""
    _insert_completed_run(app, "run-exc", "wf-notify-exc")

    with patch("mycelos.channels.telegram.send_notification", side_effect=RuntimeError("network")):
        count = notify_completed_workflows(app)

    assert count == 0
    row = app.storage.fetchone("SELECT notified_at FROM workflow_runs WHERE id = 'run-exc'")
    assert row["notified_at"] is None


def test_notify_retries_on_next_cycle(app: App) -> None:
    """Unnotified runs should be picked up again on the next cycle."""
    _insert_completed_run(app, "run-retry", "wf-notify-retry")

    # First attempt fails
    with patch("mycelos.channels.telegram.send_notification", return_value=False):
        notify_completed_workflows(app)

    # Second attempt succeeds
    with patch("mycelos.channels.telegram.send_notification", return_value=True):
        count = notify_completed_workflows(app)

    assert count == 1
    row = app.storage.fetchone("SELECT notified_at FROM workflow_runs WHERE id = 'run-retry'")
    assert row["notified_at"] is not None
