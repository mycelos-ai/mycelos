"""E2E test for Background Execution System.

Tests the full flow: dispatch → start → steps → complete → notification.
Uses the BackgroundTaskRunner directly (no Huey needed for this test).
"""

import os
import json
import tempfile
from pathlib import Path

import pytest

from mycelos.app import App
from mycelos.tasks.background_runner import BackgroundTaskRunner


@pytest.fixture
def app():
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["MYCELOS_MASTER_KEY"] = "test-key-e2e-bg"
        a = App(Path(tmp))
        a.initialize()
        yield a


@pytest.fixture
def runner(app):
    return app.task_runner


class TestBackgroundPipelineE2E:
    """Full lifecycle test for background task execution."""

    def test_full_lifecycle_dispatch_to_notify(self, runner, app):
        """Test complete flow: dispatch → start → steps → complete → notify."""
        # 1. Dispatch
        task_id = runner.dispatch(
            "creator_pipeline",
            {"name": "test-agent", "description": "E2E test agent"},
            user_id="stefan",
            session_id="e2e-session",
            agent_id="creator",
            cost_limit=5.0,
        )
        assert task_id
        status = runner.get_status(task_id)
        assert status["status"] == "pending"
        assert status["task_type"] == "creator_pipeline"
        assert status["user_id"] == "stefan"

        # 2. Start
        runner.start_task(task_id, total_steps=4)
        status = runner.get_status(task_id)
        assert status["status"] == "running"
        assert status["total_steps"] == 4

        # 3. Steps
        runner.update_step(task_id, "feasibility", "running")
        runner.update_step(task_id, "feasibility", "completed", cost=0.01)

        runner.update_step(task_id, "gherkin", "running")
        runner.update_step(task_id, "gherkin", "completed", cost=0.05)

        runner.update_step(task_id, "code_generation", "running")
        runner.update_step(task_id, "code_generation", "completed", cost=0.10)

        runner.update_step(task_id, "registration", "running")
        runner.update_step(task_id, "registration", "completed", cost=0.0)

        status = runner.get_status(task_id)
        assert status["current_step"] == "registration"

        # 4. Complete
        runner.complete_task(task_id, result={
            "success": True,
            "agent_name": "test-agent",
            "summary": "Agent test-agent created.",
        })
        status = runner.get_status(task_id)
        assert status["status"] == "completed"
        assert status["completed_at"] is not None

        # 5. Notification
        unnotified = runner.get_completed_unnotified("stefan")
        assert len(unnotified) == 1
        assert unnotified[0]["id"] == task_id

        result_data = json.loads(unnotified[0]["result"])
        assert result_data["success"] is True
        assert result_data["agent_name"] == "test-agent"

        # 6. Mark notified
        runner.mark_notified(task_id)
        assert len(runner.get_completed_unnotified("stefan")) == 0

    def test_failed_task_lifecycle(self, runner):
        """Test failure path: dispatch → start → fail → notify."""
        task_id = runner.dispatch("creator_pipeline", {}, user_id="u1")
        runner.start_task(task_id, total_steps=2)
        runner.update_step(task_id, "feasibility", "running")
        runner.fail_task(task_id, error="LLM quota exceeded")

        status = runner.get_status(task_id)
        assert status["status"] == "failed"
        assert status["error"] == "LLM quota exceeded"

        unnotified = runner.get_completed_unnotified("u1")
        assert len(unnotified) == 1

    def test_cancel_running_task(self, runner):
        """Test cancellation of a running task."""
        task_id = runner.dispatch("creator_pipeline", {})
        runner.start_task(task_id, total_steps=3)
        runner.update_step(task_id, "gherkin", "running")

        assert runner.cancel(task_id)
        status = runner.get_status(task_id)
        assert status["status"] == "cancelled"

    def test_multiple_tasks_isolation(self, runner):
        """Test that multiple tasks for different users are isolated."""
        t1 = runner.dispatch("creator_pipeline", {"name": "a1"}, user_id="user1")
        t2 = runner.dispatch("creator_pipeline", {"name": "a2"}, user_id="user2")
        t3 = runner.dispatch("creator_pipeline", {"name": "a3"}, user_id="user1")

        user1_tasks = runner.get_tasks_for_user("user1")
        user2_tasks = runner.get_tasks_for_user("user2")

        assert len(user1_tasks) == 2
        assert len(user2_tasks) == 1

    def test_app_task_runner_lazy_init(self, app):
        """Test that app.task_runner returns the same instance."""
        r1 = app.task_runner
        r2 = app.task_runner
        assert r1 is r2
        assert isinstance(r1, BackgroundTaskRunner)

    def test_step_progression_tracking(self, runner):
        """Test that step progression is correctly tracked."""
        task_id = runner.dispatch("creator_pipeline", {}, user_id="u1")
        runner.start_task(task_id, total_steps=3)

        # Simulate step-by-step progression
        runner.update_step(task_id, "step1", "running", cost=0.0)
        status = runner.get_status(task_id)
        assert status["current_step"] == "step1"

        runner.update_step(task_id, "step1", "completed", cost=0.02)

        runner.update_step(task_id, "step2", "running", cost=0.0)
        status = runner.get_status(task_id)
        assert status["current_step"] == "step2"

        runner.update_step(task_id, "step2", "completed", cost=0.03)

        runner.update_step(task_id, "step3", "running", cost=0.0)
        runner.update_step(task_id, "step3", "completed", cost=0.04)

        runner.complete_task(task_id)
        status = runner.get_status(task_id)
        assert status["status"] == "completed"
        assert status["current_step"] == "step3"

    def test_multiple_users_notification_isolation(self, runner):
        """Test that notifications are correctly isolated by user."""
        task_u1 = runner.dispatch("creator_pipeline", {}, user_id="user_a")
        task_u2 = runner.dispatch("creator_pipeline", {}, user_id="user_b")
        task_u3 = runner.dispatch("creator_pipeline", {}, user_id="user_a")

        # Complete some tasks
        runner.start_task(task_u1, total_steps=1)
        runner.complete_task(task_u1, result={"success": True})

        runner.start_task(task_u2, total_steps=1)
        runner.complete_task(task_u2, result={"success": True})

        # Check notifications per user
        unnotified_a = runner.get_completed_unnotified("user_a")
        unnotified_b = runner.get_completed_unnotified("user_b")

        assert len(unnotified_a) == 1
        assert unnotified_a[0]["id"] == task_u1

        assert len(unnotified_b) == 1
        assert unnotified_b[0]["id"] == task_u2

        # Mark user_a's notification, verify user_b unaffected
        runner.mark_notified(task_u1)
        assert len(runner.get_completed_unnotified("user_a")) == 0
        assert len(runner.get_completed_unnotified("user_b")) == 1

        # Complete task_u3 for user_a
        runner.start_task(task_u3, total_steps=1)
        runner.complete_task(task_u3, result={"success": True})
        assert len(runner.get_completed_unnotified("user_a")) == 1

    def test_error_payload_in_failed_task(self, runner):
        """Test that error messages are preserved in failed tasks."""
        task_id = runner.dispatch("creator_pipeline", {}, user_id="u1")
        runner.start_task(task_id, total_steps=2)
        runner.update_step(task_id, "step1", "running")
        error_msg = "Network timeout after 30 seconds"
        runner.fail_task(task_id, error=error_msg)

        status = runner.get_status(task_id)
        assert status["error"] == error_msg
        assert status["status"] == "failed"

        # Verify it shows up in unnotified list
        unnotified = runner.get_completed_unnotified("u1")
        assert len(unnotified) == 1
        assert unnotified[0]["error"] == error_msg

    def test_task_with_session_and_agent_context(self, runner):
        """Test that session_id and agent_id are preserved."""
        task_id = runner.dispatch(
            "creator_pipeline",
            {"name": "context-test"},
            user_id="u1",
            session_id="session-xyz",
            agent_id="planner",
        )
        status = runner.get_status(task_id)
        assert status["session_id"] == "session-xyz"
        assert status["agent_id"] == "planner"

    def test_result_json_serialization(self, runner):
        """Test that result payloads are correctly JSON serialized."""
        task_id = runner.dispatch("creator_pipeline", {}, user_id="u1")
        runner.start_task(task_id, total_steps=1)

        result_payload = {
            "success": True,
            "count": 42,
            "nested": {
                "key": "value",
                "list": [1, 2, 3],
            },
        }
        runner.complete_task(task_id, result=result_payload)

        status = runner.get_status(task_id)
        parsed_result = json.loads(status["result"])
        assert parsed_result == result_payload
        assert parsed_result["nested"]["key"] == "value"
        assert parsed_result["nested"]["list"] == [1, 2, 3]
