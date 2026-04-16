"""Test workflow pause/resume lifecycle.

Tests cover:
- Reconstructing a WorkflowAgent from a persisted DB run (from_run)
- Full pause → resume → complete cycle
- from_run with nonexistent run returns None
"""

import json
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
        os.environ["MYCELOS_MASTER_KEY"] = "test-resume-key"
        a = App(Path(tmp))
        a.initialize()
        yield a


def _mock_llm(content="Done.", tool_calls=None):
    resp = MagicMock()
    resp.content = content
    resp.tool_calls = tool_calls
    resp.total_tokens = 30
    resp.cost = 0.001
    return resp


def test_from_run_restores_agent(app):
    """from_run creates agent with conversation from DB."""
    app.workflow_registry.register(
        "restore-wf", "Restore WF",
        steps=[{"id": "s1"}],
        plan="Ask for format.",
        allowed_tools=[],
    )
    wf = app.workflow_registry.get("restore-wf")

    # Execute until clarification
    agent1 = WorkflowAgent(app=app, workflow_def=wf, run_id="run-restore")
    with patch.object(app.llm, "complete", return_value=_mock_llm(
        "NEEDS_CLARIFICATION: PDF or HTML?"
    )):
        result1 = agent1.execute()

    assert result1.status == "needs_clarification"

    # Restore from DB
    agent2 = WorkflowAgent.from_run(app, "run-restore")
    assert agent2 is not None
    assert len(agent2.conversation) >= 2  # system + user + assistant


def test_resume_completes_workflow(app):
    """Resume from DB conversation completes the workflow."""
    app.workflow_registry.register(
        "resume-wf", "Resume WF",
        steps=[{"id": "s1"}],
        plan="Ask for format, then proceed.",
        allowed_tools=[],
    )
    wf = app.workflow_registry.get("resume-wf")

    # Phase 1: execute until clarification
    agent1 = WorkflowAgent(app=app, workflow_def=wf, run_id="run-resume")
    with patch.object(app.llm, "complete", return_value=_mock_llm(
        "NEEDS_CLARIFICATION: PDF or HTML?"
    )):
        result1 = agent1.execute()
    assert result1.status == "needs_clarification"

    # Phase 2: create new agent from DB, resume
    agent2 = WorkflowAgent.from_run(app, "run-resume")
    assert agent2 is not None

    # Transition run status back to running (as _resume_workflow would do)
    app.workflow_run_manager.resume("run-resume")

    with patch.object(app.llm, "complete", return_value=_mock_llm("Done! PDF it is.")):
        result2 = agent2.resume("PDF please")

    assert result2.status == "completed"
    assert "PDF" in result2.result

    # Run should be completed in DB
    run = app.workflow_run_manager.get("run-resume")
    assert run["status"] == "completed"


def test_from_run_nonexistent_returns_none(app):
    """from_run with invalid run_id returns None."""
    assert WorkflowAgent.from_run(app, "nonexistent") is None


def test_from_run_missing_workflow_returns_none(app):
    """from_run returns None when the workflow definition is not in registry."""
    # Create a run record that references a workflow_id not in the registry.
    # We need the FK to be satisfied, so insert a minimal workflow row directly.
    app.storage.execute(
        """INSERT INTO workflows (id, name, steps) VALUES (?, ?, ?)""",
        ("ghost-wf", "Ghost", "[]"),
    )
    app.storage.execute(
        """INSERT INTO workflow_runs (id, workflow_id, user_id, completed_steps, artifacts)
           VALUES (?, ?, ?, ?, ?)""",
        ("run-ghost", "ghost-wf", "default", "[]", "{}"),
    )
    # The workflow exists in DB but not in the registry cache,
    # however workflow_registry.get() reads from DB. So we need to
    # verify that from_run handles a workflow that returns None from get().
    # Patch workflow_registry.get to return None for this ID.
    from unittest.mock import patch as _patch
    with _patch.object(app.workflow_registry, "get", return_value=None):
        assert WorkflowAgent.from_run(app, "run-ghost") is None


def test_resumed_agent_has_correct_conversation_length(app):
    """Resumed agent preserves all conversation messages from the paused run."""
    app.workflow_registry.register(
        "conv-wf", "Conv WF",
        steps=[{"id": "s1"}],
        plan="Multi-step plan.",
        allowed_tools=[],
    )
    wf = app.workflow_registry.get("conv-wf")

    agent1 = WorkflowAgent(app=app, workflow_def=wf, run_id="run-conv")
    with patch.object(app.llm, "complete", return_value=_mock_llm(
        "NEEDS_CLARIFICATION: Which language?"
    )):
        agent1.execute()

    # The conversation should have: system, user, assistant
    run = app.workflow_run_manager.get("run-conv")
    conv = run["conversation"]
    if isinstance(conv, str):
        conv = json.loads(conv)
    assert len(conv) == 3

    agent2 = WorkflowAgent.from_run(app, "run-conv")
    assert len(agent2.conversation) == 3
    assert agent2.conversation[0]["role"] == "system"
    assert agent2.conversation[1]["role"] == "user"
    assert agent2.conversation[2]["role"] == "assistant"
