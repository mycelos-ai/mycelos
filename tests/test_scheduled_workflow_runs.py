"""Test that scheduled workflow execution persists a run and queues notification."""

import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from mycelos.app import App
from mycelos.scheduler.jobs import check_scheduled_workflows


@pytest.fixture
def app():
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["MYCELOS_MASTER_KEY"] = "test-sched-runs"
        a = App(Path(tmp))
        a.initialize()
        yield a


def test_scheduled_workflow_creates_run_record(app):
    """Executing a scheduled workflow persists a run record."""
    app.workflow_registry.register(
        "daily-test", "Daily Test",
        steps=[{"id": "s1"}],
        plan="Say hello.",
        allowed_tools=[],
    )
    task_id = app.schedule_manager.add("daily-test", "* * * * *")  # due now

    # Force next_run to be in the past so get_due_tasks() returns it
    app.storage.execute(
        "UPDATE scheduled_tasks SET next_run = '2000-01-01T00:00:00+00:00' WHERE id = ?",
        (task_id,),
    )

    mock_resp = MagicMock()
    mock_resp.content = "Hello from scheduled run!"
    mock_resp.tool_calls = None
    mock_resp.total_tokens = 30
    mock_resp.cost = 0.001

    with patch.object(app.llm, "complete", return_value=mock_resp):
        executed = check_scheduled_workflows(app)

    assert len(executed) >= 1
    runs = app.workflow_run_manager.list_runs(workflow_id="daily-test")
    assert len(runs) == 1
    assert runs[0]["status"] == "completed"
