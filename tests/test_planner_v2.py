"""Tests for Planner V2 — context-aware planning with needs_new_agent."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from mycelos.agents.planner import PlannerAgent
from mycelos.app import App


@pytest.fixture
def app() -> App:
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["MYCELOS_MASTER_KEY"] = "test-key-planner-v2"
        a = App(Path(tmp))
        a.initialize()
        yield a


def _mock_llm(response_json: dict) -> MagicMock:
    mock = MagicMock()
    r = MagicMock()
    r.content = json.dumps(response_json)
    r.total_tokens = 50
    r.model = "test"
    r.tool_calls = None
    mock.complete.return_value = r
    return mock


# --- Context in prompt ---


def test_planner_includes_agents_in_prompt(app: App) -> None:
    """Available agents should appear in the planner prompt."""
    app.agent_registry.register(
        "news-agent", "News", "deterministic", ["search.web"], "system"
    )
    app.agent_registry.set_status("news-agent", "active")

    mock = _mock_llm(
        {
            "action": "execute_workflow",
            "steps": [],
            "estimated_cost": "low",
            "explanation": "test",
        }
    )
    planner = PlannerAgent(mock)

    from mycelos.agents.planner_context import build_planner_context

    ctx = build_planner_context(app)
    planner.plan("search news", context=ctx)

    call_args = mock.complete.call_args
    messages = call_args[0][0] if call_args[0] else call_args.kwargs["messages"]
    system_msg = messages[0]["content"]
    assert "news-agent" in system_msg


def test_planner_includes_workflows_in_prompt(app: App) -> None:
    """Available workflows should appear in the planner prompt."""
    app.workflow_registry.register(
        "news-wf", "News WF", [{"id": "s1"}], scope=["search.web"]
    )

    mock = _mock_llm(
        {
            "action": "execute_workflow",
            "steps": [],
            "estimated_cost": "low",
            "explanation": "",
        }
    )
    planner = PlannerAgent(mock)

    from mycelos.agents.planner_context import build_planner_context

    ctx = build_planner_context(app)
    planner.plan("search news", context=ctx)

    call_args = mock.complete.call_args
    messages = call_args[0][0] if call_args[0] else call_args.kwargs["messages"]
    system_msg = messages[0]["content"]
    assert "news-wf" in system_msg


def test_planner_includes_capabilities_in_prompt(app: App) -> None:
    """Available capabilities should appear in the planner prompt."""
    app.connector_registry.register(
        "ddg", "DDG", "search", ["search.web", "search.news"]
    )

    mock = _mock_llm(
        {
            "action": "execute_workflow",
            "steps": [],
            "estimated_cost": "low",
            "explanation": "",
        }
    )
    planner = PlannerAgent(mock)

    from mycelos.agents.planner_context import build_planner_context

    ctx = build_planner_context(app)
    planner.plan("test", context=ctx)

    call_args = mock.complete.call_args
    messages = call_args[0][0] if call_args[0] else call_args.kwargs["messages"]
    system_msg = messages[0]["content"]
    assert "search.web" in system_msg


# --- needs_new_agent ---


def test_planner_returns_needs_new_agent() -> None:
    """Planner should return needs_new_agent with missing_agents list."""
    mock = _mock_llm(
        {
            "action": "needs_new_agent",
            "steps": [],
            "missing_agents": [
                {
                    "name": "pdf-processor",
                    "description": "Process PDFs",
                    "capabilities": ["http.get"],
                }
            ],
            "estimated_cost": "medium",
            "explanation": "Need a PDF processor agent",
        }
    )
    planner = PlannerAgent(mock)
    plan = planner.plan("process my PDFs", context={})

    assert plan["action"] == "needs_new_agent"
    assert len(plan["missing_agents"]) == 1
    assert plan["missing_agents"][0]["name"] == "pdf-processor"


def test_planner_returns_execute_with_existing() -> None:
    """Planner should return execute_workflow with existing workflow reference."""
    mock = _mock_llm(
        {
            "action": "execute_workflow",
            "workflow_id": "news-summary",
            "steps": [
                {"id": "search", "agent": "search-agent", "action": "Search news"}
            ],
            "missing_agents": [],
            "estimated_cost": "low",
            "explanation": "Using existing news-summary workflow",
        }
    )
    planner = PlannerAgent(mock)
    plan = planner.plan("search news", context={})

    assert plan["action"] == "execute_workflow"
    assert plan["workflow_id"] == "news-summary"
    assert plan["missing_agents"] == []


def test_planner_ensures_missing_agents_field() -> None:
    """Even if LLM omits missing_agents, it should be added."""
    mock = _mock_llm(
        {
            "action": "execute_workflow",
            "steps": [],
            "estimated_cost": "low",
        }
    )
    planner = PlannerAgent(mock)
    plan = planner.plan("test", context={})
    assert "missing_agents" in plan
    assert plan["missing_agents"] == []


def test_planner_ensures_explanation_field() -> None:
    """Even if LLM omits explanation, it should be added."""
    mock = _mock_llm(
        {"action": "execute_workflow", "steps": [], "estimated_cost": "low"}
    )
    planner = PlannerAgent(mock)
    plan = planner.plan("test", context={})
    assert "explanation" in plan


def test_planner_invalid_json_fallback() -> None:
    """Invalid JSON from LLM should produce a safe fallback plan."""
    mock = MagicMock()
    r = MagicMock()
    r.content = "This is not JSON at all"
    r.total_tokens = 10
    mock.complete.return_value = r
    planner = PlannerAgent(mock)
    plan = planner.plan("test", context={})
    assert plan["action"] == "execute_workflow"
    assert "error" in plan
    assert plan["missing_agents"] == []


def test_planner_empty_context_works() -> None:
    """Planner should work with empty context (backward compat)."""
    mock = _mock_llm(
        {
            "action": "execute_workflow",
            "steps": [],
            "estimated_cost": "low",
            "explanation": "",
        }
    )
    planner = PlannerAgent(mock)
    plan = planner.plan("test", context={})
    assert plan["action"] == "execute_workflow"


# --- Orchestrator integration ---


def test_orchestrator_passes_context(app: App) -> None:
    """Orchestrator should pass rich context to planner."""
    app.connector_registry.register("ddg", "DDG", "search", ["search.web"])

    # Mock the LLM for both orchestrator classify and planner
    mock = MagicMock()
    call_count = [0]

    def side_effect(*args, **kwargs):
        call_count[0] += 1
        r = MagicMock()
        r.tool_calls = None
        if call_count[0] == 1:
            # Classifier call
            r.content = json.dumps({"intent": "task_request", "confidence": 0.9})
        else:
            # Planner call
            r.content = json.dumps(
                {
                    "action": "execute_workflow",
                    "steps": [],
                    "estimated_cost": "low",
                    "explanation": "test",
                }
            )
        r.total_tokens = 10
        return r

    mock.complete.side_effect = side_effect

    app._llm = mock
    # Force re-creation of orchestrator with new LLM
    app._orchestrator = None

    result = app.orchestrator.route("search for news", user_id="default")
    assert result.plan is not None

    # Planner should have received context (second LLM call)
    assert call_count[0] >= 2
    planner_call = mock.complete.call_args_list[1]
    messages = (
        planner_call[0][0] if planner_call[0] else planner_call.kwargs["messages"]
    )
    system_msg = messages[0]["content"]
    # Should contain context about available resources
    assert (
        "search.web" in system_msg
        or "DDG" in system_msg
        or "Available" in system_msg
    )
