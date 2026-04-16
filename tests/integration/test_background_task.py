"""Integration test: Background task lifecycle.

Tests the full dispatch → start → steps → complete → notification flow.

Cost estimate: ~$0.00 (no LLM calls)
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _ensure_users(integration_app):
    """Background tasks have FK to users(id) — create test users used here."""
    for uid in ("test-user", "notify-user", "cancel-user", "fail-user", "user-alice", "user-bob"):
        integration_app.storage.execute(
            "INSERT OR IGNORE INTO users (id, name) VALUES (?, ?)", (uid, uid)
        )


@pytest.mark.integration
def test_background_task_lifecycle(integration_app):
    """Dispatch → start → steps → complete → notification."""
    app = integration_app
    runner = app.task_runner

    task_id = runner.dispatch(
        "test_task",
        {"data": "hello"},
        user_id="test-user",
        session_id="test-session",
    )
    assert task_id, "dispatch() should return a task ID"

    # Initial status should be pending
    status = runner.get_status(task_id)
    assert status["status"] == "pending", \
        f"Task should start as 'pending', got '{status['status']}'"

    # Start task
    runner.start_task(task_id, total_steps=2)
    status = runner.get_status(task_id)
    assert status["status"] == "running", \
        f"Task should be 'running' after start, got '{status['status']}'"

    # Progress through steps
    runner.update_step(task_id, "step1", "running")
    runner.update_step(task_id, "step1", "completed")
    runner.update_step(task_id, "step2", "running")
    runner.update_step(task_id, "step2", "completed")

    # Complete task
    runner.complete_task(task_id, result={"success": True})
    status = runner.get_status(task_id)
    assert status["status"] == "completed", \
        f"Task should be 'completed', got '{status['status']}'"

    # Should appear in unnotified list
    unnotified = runner.get_completed_unnotified("test-user")
    assert len(unnotified) >= 1, "Should have at least one unnotified completed task"
    task_ids = [t["id"] for t in unnotified]
    assert task_id in task_ids, "The completed task should be in the unnotified list"


@pytest.mark.integration
def test_background_task_mark_notified(integration_app):
    """After mark_notified, task should disappear from unnotified list."""
    app = integration_app
    runner = app.task_runner

    task_id = runner.dispatch(
        "notify_test",
        {"data": "notify me"},
        user_id="notify-user",
    )

    runner.start_task(task_id, total_steps=1)
    runner.complete_task(task_id, result={"done": True})

    # Should appear in unnotified list
    unnotified_before = runner.get_completed_unnotified("notify-user")
    assert any(t["id"] == task_id for t in unnotified_before), \
        "Task should be in unnotified list before mark_notified"

    # Mark as notified
    runner.mark_notified(task_id)

    # Should no longer appear
    unnotified_after = runner.get_completed_unnotified("notify-user")
    assert not any(t["id"] == task_id for t in unnotified_after), \
        "Task should NOT be in unnotified list after mark_notified"


@pytest.mark.integration
def test_background_task_cancel(integration_app):
    """A pending task can be cancelled."""
    app = integration_app
    runner = app.task_runner

    task_id = runner.dispatch(
        "cancelable_task",
        {},
        user_id="cancel-user",
    )

    result = runner.cancel(task_id)
    assert result is True, "cancel() should return True for existing task"

    status = runner.get_status(task_id)
    assert status["status"] == "cancelled", \
        f"Task should be 'cancelled', got '{status['status']}'"


@pytest.mark.integration
def test_background_task_failed(integration_app):
    """A failed task should appear in unnotified list."""
    app = integration_app
    runner = app.task_runner

    task_id = runner.dispatch(
        "failing_task",
        {},
        user_id="fail-user",
    )

    runner.start_task(task_id, total_steps=1)
    runner.fail_task(task_id, "Something went wrong")

    status = runner.get_status(task_id)
    assert status["status"] == "failed", \
        f"Task with error should be 'failed', got '{status['status']}'"

    unnotified = runner.get_completed_unnotified("fail-user")
    assert any(t["id"] == task_id for t in unnotified), \
        "Failed task should also appear in unnotified list"


@pytest.mark.integration
def test_background_tasks_are_user_scoped(integration_app):
    """Tasks for one user should not appear in another user's list."""
    app = integration_app
    runner = app.task_runner

    task_id = runner.dispatch("task_a", {}, user_id="user-alice")
    runner.start_task(task_id, total_steps=1)
    runner.complete_task(task_id)

    # bob's unnotified list should not contain alice's task
    bob_tasks = runner.get_completed_unnotified("user-bob")
    assert not any(t["id"] == task_id for t in bob_tasks), \
        "Alice's task should not appear in Bob's unnotified list"
