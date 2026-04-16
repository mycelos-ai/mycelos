"""Test background workflow dispatch."""

import os
import tempfile
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from mycelos.app import App
from mycelos.scheduler.jobs import execute_background_workflow


@pytest.fixture
def app():
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["MYCELOS_MASTER_KEY"] = "test-bg-wf"
        a = App(Path(tmp))
        a.initialize()
        yield a


def test_background_workflow_runs_in_thread(app):
    """Background workflow executes in a separate thread and persists run."""
    app.workflow_registry.register(
        "bg-test", "BG Test",
        steps=[{"id": "s1"}],
        plan="Say hello.",
        allowed_tools=[],
    )

    mock_resp = MagicMock()
    mock_resp.content = "Background hello!"
    mock_resp.tool_calls = None
    mock_resp.total_tokens = 30
    mock_resp.cost = 0.001

    with patch.object(app.llm, "complete", return_value=mock_resp):
        run_id = execute_background_workflow(app, "bg-test", inputs={"request": "test"})
        # Give the thread time to complete (patch must stay active)
        time.sleep(2)

    runs = app.workflow_run_manager.list_runs(workflow_id="bg-test")
    assert len(runs) == 1
    assert runs[0]["status"] == "completed"
    assert runs[0]["id"] == run_id


def test_background_workflow_invalid_id_raises(app):
    """Dispatching with invalid workflow_id raises ValueError."""
    with pytest.raises(ValueError, match="not found"):
        execute_background_workflow(app, "nonexistent-wf")
