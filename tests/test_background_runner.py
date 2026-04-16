"""Tests for BackgroundTaskRunner — dispatch, status, lifecycle."""

import os
import tempfile
import json
from pathlib import Path
import pytest
from mycelos.app import App
from mycelos.tasks.background_runner import BackgroundTaskRunner


@pytest.fixture
def app():
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["MYCELOS_MASTER_KEY"] = "test-key-bg"
        a = App(Path(tmp))
        a.initialize()
        yield a


@pytest.fixture
def runner(app):
    return BackgroundTaskRunner(app)


class TestDispatch:
    def test_dispatch_returns_task_id(self, runner):
        task_id = runner.dispatch("creator_pipeline", {"name": "test"})
        assert task_id is not None
        assert len(task_id) > 8

    def test_dispatch_creates_db_record(self, runner, app):
        task_id = runner.dispatch("creator_pipeline", {"name": "test"})
        status = runner.get_status(task_id)
        assert status["status"] == "pending"
        assert status["task_type"] == "creator_pipeline"

    def test_dispatch_with_cost_limit(self, runner):
        task_id = runner.dispatch("creator_pipeline", {}, cost_limit=5.0)
        status = runner.get_status(task_id)
        assert status["cost_limit"] == 5.0


class TestLifecycle:
    def test_get_tasks_for_user(self, runner):
        runner.dispatch("creator_pipeline", {"n": "1"}, user_id="stefan")
        runner.dispatch("creator_pipeline", {"n": "2"}, user_id="stefan")
        tasks = runner.get_tasks_for_user("stefan")
        assert len(tasks) == 2

    def test_cancel_task(self, runner):
        task_id = runner.dispatch("creator_pipeline", {})
        assert runner.cancel(task_id)
        status = runner.get_status(task_id)
        assert status["status"] == "cancelled"

    def test_update_step(self, runner):
        task_id = runner.dispatch("creator_pipeline", {})
        runner.start_task(task_id, total_steps=3)
        runner.update_step(task_id, "gherkin", "running")
        status = runner.get_status(task_id)
        assert status["current_step"] == "gherkin"


class TestNotification:
    def test_completed_unnotified(self, runner):
        task_id = runner.dispatch("creator_pipeline", {}, user_id="u1")
        runner.start_task(task_id, total_steps=1)
        runner.complete_task(task_id, result={"success": True})
        unnotified = runner.get_completed_unnotified("u1")
        assert len(unnotified) == 1

    def test_mark_notified(self, runner):
        task_id = runner.dispatch("creator_pipeline", {}, user_id="u1")
        runner.start_task(task_id, total_steps=1)
        runner.complete_task(task_id, result={"success": True})
        runner.mark_notified(task_id)
        unnotified = runner.get_completed_unnotified("u1")
        assert len(unnotified) == 0
