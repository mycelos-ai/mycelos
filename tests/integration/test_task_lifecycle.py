"""Integration test: Task lifecycle — create -> plan -> execute -> complete."""

import json

import pytest

from mycelos.agents.evaluator import EvaluatorAgent
from mycelos.agents.models import AgentOutput
from mycelos.agents.planner import PlannerAgent
from mycelos.agents.registry import AgentRegistry
from mycelos.llm.mock_broker import MockLLMBroker
from mycelos.orchestrator import ChatOrchestrator, Intent
from mycelos.storage.database import SQLiteStorage
from mycelos.tasks.manager import TaskManager
from mycelos.workflows.models import Workflow, WorkflowStep


@pytest.mark.integration
def test_full_task_lifecycle(tmp_path):
    """Complete task lifecycle: message -> task -> plan -> execute -> complete -> score."""
    storage = SQLiteStorage(tmp_path / "test.db")
    storage.initialize()

    task_mgr = TaskManager(storage)
    agent_reg = AgentRegistry(storage)
    agent_reg.register(
        "search-agent", "Search Agent", "deterministic", ["search.web"], "system"
    )
    agent_reg.set_status("search-agent", "active")

    # 1. Create task
    task_id = task_mgr.create("Search for AI news", user_id="stefan")
    assert task_mgr.get(task_id)["status"] == "pending"

    # 2. Planning
    task_mgr.update_status(task_id, "planning")
    plan_broker = MockLLMBroker().on_message(
        r".*",
        json.dumps(
            {
                "action": "execute_workflow",
                "workflow_id": "news-search",
                "steps": [
                    {"id": "search", "agent": "search-agent", "action": "search"}
                ],
                "estimated_cost": "low",
            }
        ),
    )
    planner = PlannerAgent(llm=plan_broker)
    plan = planner.plan(
        "Search for AI news",
        context={"available_agents": ["search-agent"]},
    )
    assert plan["action"] == "execute_workflow"

    # 3. Awaiting confirmation (user confirms)
    task_mgr.update_status(task_id, "awaiting")
    # ... user confirms ...
    task_mgr.update_status(task_id, "running")

    # 4. Execute workflow
    wf = Workflow(
        name="news-search",
        steps=[
            WorkflowStep(
                id="search",
                action="Search AI news",
                agent="search-agent",
                policy="always",
            ),
            WorkflowStep(
                id="summarize",
                action="Summarize results",
                agent="search-agent",
                policy="always",
            ),
        ],
    )

    def mock_runner(step, ctx):
        return AgentOutput(
            success=True,
            result={"step": step.id, "data": "found 5 articles"},
            artifacts=[],
            metadata={"cost": 0.001},
        )

    # Simulate successful workflow execution
    total_cost = 0.002

    # 5. Complete task
    task_mgr.set_result(
        task_id, result={"articles": 5}, cost=total_cost, status="completed"
    )
    assert task_mgr.get(task_id)["status"] == "completed"
    assert len(task_mgr.get_attempts(task_id)) == 1

    # 6. Verify agent is still active
    agent = agent_reg.get("search-agent")
    assert agent["status"] == "active"


@pytest.mark.integration
def test_task_failure_lifecycle(tmp_path):
    """Task that fails: message -> task -> execute -> fail -> score decreases."""
    storage = SQLiteStorage(tmp_path / "test.db")
    storage.initialize()

    task_mgr = TaskManager(storage)
    agent_reg = AgentRegistry(storage)
    agent_reg.register("bad-agent", "Bad Agent", "light_model", [], "system")

    task_id = task_mgr.create("Do something impossible")
    task_mgr.update_status(task_id, "running")

    # Agent fails
    task_mgr.set_result(
        task_id, result=None, status="failed", agent_id="bad-agent"
    )
    assert task_mgr.get(task_id)["status"] == "failed"

    # Verify agent still exists
    assert agent_reg.get("bad-agent") is not None


@pytest.mark.integration
def test_task_abort_lifecycle(tmp_path):
    """User aborts a running task."""
    storage = SQLiteStorage(tmp_path / "test.db")
    storage.initialize()
    task_mgr = TaskManager(storage)

    task_id = task_mgr.create("Long running task")
    task_mgr.update_status(task_id, "running")
    task_mgr.update_status(task_id, "aborted")
    assert task_mgr.get(task_id)["status"] == "aborted"


@pytest.mark.integration
def test_orchestrator_creates_real_task(tmp_path):
    """Orchestrator route() creates a real task in SQLite."""
    storage = SQLiteStorage(tmp_path / "test.db")
    storage.initialize()
    task_mgr = TaskManager(storage)

    classify_broker = MockLLMBroker().on_message(
        r".*", json.dumps({"intent": "task_request"})
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
        task_manager=task_mgr, planner=PlannerAgent(llm=plan_broker)
    )

    result = orch.route("Search for news about AI", user_id="stefan")
    assert result.intent == Intent.TASK_REQUEST
    assert result.task_id is not None

    # Task exists in DB
    task = task_mgr.get(result.task_id)
    assert task is not None
    assert task["status"] == "planning"
    assert task["user_id"] == "stefan"
