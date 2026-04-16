"""Tests that WorkflowAgent persists run state to workflow_runs table."""

import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from mycelos.app import App
from mycelos.workflows.agent import WorkflowAgent


@pytest.fixture
def app():
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["MYCELOS_MASTER_KEY"] = "test-key-wf-runs"
        a = App(Path(tmp))
        a.initialize()
        yield a


def _mock_llm_response(content="Done.", tool_calls=None):
    resp = MagicMock()
    resp.content = content
    resp.tool_calls = tool_calls
    resp.total_tokens = 50
    resp.cost = 0.0023
    return resp


def test_execute_creates_workflow_run(app):
    """Completed workflow execution creates a run record."""
    app.workflow_registry.register(
        "test-wf", "Test Workflow",
        steps=[{"id": "s1"}],
        plan="Say hello.",
        allowed_tools=[],
    )
    wf = app.workflow_registry.get("test-wf")
    agent = WorkflowAgent(app=app, workflow_def=wf, run_id="run-001")

    with patch.object(app.llm, "complete", return_value=_mock_llm_response("Hello!")):
        result = agent.execute()

    assert result.status == "completed"
    runs = app.workflow_run_manager.list_runs(workflow_id="test-wf")
    assert len(runs) == 1
    assert runs[0]["status"] == "completed"
    assert runs[0]["id"] == "run-001"
    assert runs[0]["cost"] > 0  # Should be 0.0023
    # Result text and conversation should be persisted
    run = app.workflow_run_manager.get("run-001")
    assert run["artifacts"]["result"] == "Hello!"
    assert run["conversation"] is not None
    assert len(run["conversation"]) >= 2


def test_failed_workflow_persists_error(app):
    """Failed workflow execution persists error in run record."""
    app.workflow_registry.register(
        "fail-wf", "Fail WF",
        steps=[{"id": "s1"}],
        plan="Do something.",
        allowed_tools=[],
    )
    wf = app.workflow_registry.get("fail-wf")
    agent = WorkflowAgent(app=app, workflow_def=wf, run_id="run-002", max_rounds=1)

    mock_resp = _mock_llm_response(tool_calls=[{
        "id": "tc1", "function": {"name": "fake_tool", "arguments": "{}"}
    }])
    with patch.object(app.llm, "complete", return_value=mock_resp):
        result = agent.execute()

    assert result.status == "failed"
    runs = app.workflow_run_manager.list_runs(workflow_id="fail-wf")
    assert len(runs) == 1
    assert runs[0]["status"] == "failed"
    assert "Max rounds" in runs[0]["error"]


def test_run_persists_session_id(app):
    """WorkflowAgent with session_id stores it on the run record."""
    app.workflow_registry.register(
        "sid-wf", "Session ID WF",
        steps=[{"id": "s1"}],
        plan="Say hi.",
        allowed_tools=[],
    )
    wf = app.workflow_registry.get("sid-wf")
    agent = WorkflowAgent(
        app=app, workflow_def=wf, run_id="run-with-session",
        session_id="sess-xyz-123",
    )

    with patch.object(app.llm, "complete", return_value=_mock_llm_response("Hi.")):
        agent.execute()

    run = app.workflow_run_manager.get("run-with-session")
    assert run is not None
    assert run["session_id"] == "sess-xyz-123"


def test_run_without_session_id_is_none(app):
    """Headless runs (scheduled/cron) store session_id as NULL."""
    app.workflow_registry.register(
        "nosid-wf", "No Session WF",
        steps=[{"id": "s1"}],
        plan="Say hi.",
        allowed_tools=[],
    )
    wf = app.workflow_registry.get("nosid-wf")
    agent = WorkflowAgent(app=app, workflow_def=wf, run_id="run-no-session")

    with patch.object(app.llm, "complete", return_value=_mock_llm_response("Hi.")):
        agent.execute()

    run = app.workflow_run_manager.get("run-no-session")
    assert run is not None
    assert run["session_id"] is None


def test_run_manager_start_accepts_session_id(app):
    """WorkflowRunManager.start accepts an optional session_id."""
    app.workflow_registry.register(
        "rm-wf", "RM WF", steps=[{"id": "s1"}], plan="Go.", allowed_tools=[],
    )
    run_id = app.workflow_run_manager.start(
        workflow_id="rm-wf",
        run_id="rm-run-1",
        session_id="sess-direct",
    )
    run = app.workflow_run_manager.get(run_id)
    assert run["session_id"] == "sess-direct"


def test_run_manager_list_runs_by_session(app):
    """WorkflowRunManager.list_runs_by_session returns all runs for a session."""
    app.workflow_registry.register(
        "sess-wf", "Sess WF", steps=[{"id": "s1"}], plan="Go.", allowed_tools=[],
    )
    app.workflow_run_manager.start(
        workflow_id="sess-wf", run_id="run-a", session_id="sess-1",
    )
    app.workflow_run_manager.start(
        workflow_id="sess-wf", run_id="run-b", session_id="sess-1",
    )
    app.workflow_run_manager.start(
        workflow_id="sess-wf", run_id="run-c", session_id="sess-2",
    )
    app.workflow_run_manager.start(
        workflow_id="sess-wf", run_id="run-d",  # no session
    )

    runs = app.workflow_run_manager.list_runs_by_session("sess-1")
    assert {r["id"] for r in runs} == {"run-a", "run-b"}

    runs_empty = app.workflow_run_manager.list_runs_by_session("sess-missing")
    assert runs_empty == []


def test_clarification_pauses_run_with_conversation(app):
    """needs_clarification persists run as waiting_input with conversation."""
    app.workflow_registry.register(
        "clarify-wf", "Clarify WF",
        steps=[{"id": "s1"}],
        plan="Ask the user what format they want.",
        allowed_tools=[],
    )
    wf = app.workflow_registry.get("clarify-wf")
    agent = WorkflowAgent(app=app, workflow_def=wf, run_id="run-clarify")

    mock_resp = _mock_llm_response("NEEDS_CLARIFICATION: What format do you prefer — PDF or HTML?")
    with patch.object(app.llm, "complete", return_value=mock_resp):
        result = agent.execute()

    assert result.status == "needs_clarification"
    assert "format" in result.clarification.lower()

    run = app.workflow_run_manager.get("run-clarify")
    assert run is not None
    assert run["status"] == "waiting_input"
    assert run["clarification"] == "What format do you prefer — PDF or HTML?"
    assert run["conversation"] is not None
    # Conversation should be a list with at least system + user + assistant messages
    conv = run["conversation"]
    if isinstance(conv, str):
        import json
        conv = json.loads(conv)
    assert len(conv) >= 2
