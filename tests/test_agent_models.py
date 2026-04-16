"""Tests for Agent data models."""

from mycelos.agents.models import AgentInput, AgentOutput


def test_agent_input_creation() -> None:
    inp = AgentInput(
        task_goal="Summarize emails",
        task_inputs={"limit": 10},
        artifacts=["/input/data.txt"],
        context={"user_name": "Stefan"},
        config={"model_tier": "haiku"},
    )
    assert inp.task_goal == "Summarize emails"
    assert inp.artifacts == ["/input/data.txt"]


def test_agent_output_success() -> None:
    out = AgentOutput(
        success=True,
        result={"email_count": 5},
        artifacts=["/output/summary.md"],
        metadata={"duration_ms": 120, "model": "haiku"},
    )
    assert out.success is True
    assert out.error is None


def test_agent_output_failure() -> None:
    out = AgentOutput(
        success=False,
        result=None,
        artifacts=[],
        metadata={},
        error="Connection timeout",
    )
    assert out.success is False
    assert out.error == "Connection timeout"


def test_agent_input_defaults() -> None:
    inp = AgentInput(task_goal="test")
    assert inp.task_inputs == {}
    assert inp.artifacts == []
    assert inp.context == {}
    assert inp.config == {}


def test_agent_protocol_exists() -> None:
    """The Agent protocol should be importable from protocols."""
    from mycelos.protocols import Agent

    assert Agent is not None
