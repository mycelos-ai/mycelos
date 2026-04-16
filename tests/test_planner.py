"""Tests for PlannerAgent."""

import json

import pytest

from mycelos.agents.planner import PlannerAgent
from mycelos.llm.mock_broker import MockLLMBroker


@pytest.fixture
def planner() -> PlannerAgent:
    broker = MockLLMBroker().on_message(
        r".*email.*",
        json.dumps({
            "action": "execute_workflow",
            "workflow_id": "email-summary",
            "steps": [{"id": "fetch", "agent": "email-summary", "action": "fetch"}],
            "estimated_cost": "low",
        }),
    ).on_message(
        r".*calendar.*",
        json.dumps({
            "action": "needs_new_agent",
            "description": "No calendar agent exists.",
            "suggested_capabilities": ["calendar.read"],
        }),
    ).on_message(
        r".*",
        json.dumps({
            "action": "execute_workflow",
            "workflow_id": None,
            "steps": [{"id": "generic", "agent": "generic", "action": "process"}],
            "estimated_cost": "medium",
        }),
    )
    return PlannerAgent(llm=broker)


def test_plan_for_known_request(planner: PlannerAgent) -> None:
    result = planner.plan("Summarize my emails", context={})
    assert result["action"] == "execute_workflow"
    assert result["workflow_id"] == "email-summary"


def test_plan_for_unknown_capability(planner: PlannerAgent) -> None:
    result = planner.plan("What's on my calendar tomorrow?", context={})
    assert result["action"] == "needs_new_agent"
    assert "calendar" in result["description"].lower()


def test_plan_returns_valid_structure(planner: PlannerAgent) -> None:
    result = planner.plan("Do something", context={})
    assert "action" in result


def test_plan_with_context(planner: PlannerAgent) -> None:
    result = planner.plan("Summarize emails", context={
        "available_agents": ["email-summary"],
        "user_name": "Stefan",
    })
    assert result is not None
    assert len(planner._llm.call_log) >= 1


def test_plan_uses_system_prompt(planner: PlannerAgent) -> None:
    planner.plan("test", context={})
    call = planner._llm.call_log[0]
    messages = call["messages"]
    system_msg = next((m for m in messages if m["role"] == "system"), None)
    assert system_msg is not None
    assert len(system_msg["content"]) > 50  # not empty
