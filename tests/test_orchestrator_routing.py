"""Tests for Orchestrator routing -- real dispatching."""

import json
from pathlib import Path

import pytest

from mycelos.agents.planner import PlannerAgent
from mycelos.llm.mock_broker import MockLLMBroker
from mycelos.orchestrator import ChatOrchestrator, Intent, RouteResult
from mycelos.storage.database import SQLiteStorage
from mycelos.tasks.manager import TaskManager


def make_orchestrator(db_path: Path):
    storage = SQLiteStorage(db_path)
    storage.initialize()

    task_mgr = TaskManager(storage)

    classify_broker = (
        MockLLMBroker()
        .on_message(
            r".*summar.*|.*email.*|.*search.*",
            json.dumps({"intent": "task_request"}),
        )
        .on_message(
            r".*config.*|.*list.*agent.*",
            json.dumps({"intent": "system_command"}),
        )
        .on_message(r".*", json.dumps({"intent": "conversation"}))
    )

    plan_broker = MockLLMBroker().on_message(
        r".*",
        json.dumps(
            {
                "action": "execute_workflow",
                "workflow_id": "test",
                "steps": [{"id": "s1", "agent": "a", "action": "do"}],
                "estimated_cost": "low",
            }
        ),
    )

    orch = ChatOrchestrator(llm=classify_broker)
    orch.set_services(
        task_manager=task_mgr,
        planner=PlannerAgent(llm=plan_broker),
    )
    return orch, task_mgr


def test_route_conversation(db_path: Path) -> None:
    orch, _ = make_orchestrator(db_path)
    result = orch.route("Hello!")
    assert result.intent == Intent.CONVERSATION
    assert result.task_id is None


def test_route_task_creates_task(db_path: Path) -> None:
    orch, mgr = make_orchestrator(db_path)
    result = orch.route("Summarize my emails")
    assert result.intent == Intent.TASK_REQUEST
    assert result.task_id is not None
    task = mgr.get(result.task_id)
    assert task is not None
    assert task["status"] == "planning"


def test_route_task_returns_plan(db_path: Path) -> None:
    orch, _ = make_orchestrator(db_path)
    result = orch.route("Summarize emails")
    assert result.plan is not None
    assert result.plan["action"] == "execute_workflow"


def test_route_system_command(db_path: Path) -> None:
    orch, _ = make_orchestrator(db_path)
    result = orch.route("List my agents")
    assert result.intent == Intent.SYSTEM_COMMAND


def test_route_without_services_falls_back(db_path: Path) -> None:
    """Without services injected, task_request falls back to conversation."""
    broker = MockLLMBroker().on_message(
        r".*", json.dumps({"intent": "task_request"})
    )
    orch = ChatOrchestrator(llm=broker)
    result = orch.route("Summarize emails")
    assert result.intent == Intent.CONVERSATION  # no task_manager -> fallback
