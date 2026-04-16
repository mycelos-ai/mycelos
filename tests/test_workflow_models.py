"""Tests for Workflow data models."""
from mycelos.workflows.models import Workflow, WorkflowStep


def test_workflow_step_creation() -> None:
    step = WorkflowStep(
        id="fetch", action="Fetch emails", agent="email-agent", policy="always"
    )
    assert step.id == "fetch"
    assert step.condition is None


def test_workflow_step_with_condition() -> None:
    step = WorkflowStep(
        id="p",
        action="Process",
        agent="proc",
        policy="always",
        condition="steps.fetch.result.count > 0",
    )
    assert step.condition is not None


def test_workflow_creation() -> None:
    steps = [
        WorkflowStep(id="s1", action="a", agent="a1", policy="always"),
        WorkflowStep(id="s2", action="b", agent="a2", policy="prepare"),
    ]
    wf = Workflow(
        name="test",
        description="Test",
        goal="Test goal",
        steps=steps,
        scope=["email.read"],
    )
    assert wf.name == "test"
    assert len(wf.steps) == 2
    assert wf.version == 1


def test_workflow_defaults() -> None:
    wf = Workflow(
        name="minimal",
        steps=[WorkflowStep(id="s1", action="do", agent="a", policy="always")],
    )
    assert wf.description == ""
    assert wf.scope == []
    assert wf.mcps == []
    assert wf.tags == []
