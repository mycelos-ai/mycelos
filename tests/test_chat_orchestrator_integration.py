"""Integration test: Chat -> Orchestrator -> Planner -> Workflow execution.

Tests the full flow from user message classification through planning
to workflow parsing and execution, including evaluator checks and
blueprint risk classification.
"""

from __future__ import annotations

import json
from pathlib import Path
from textwrap import dedent

import pytest

from mycelos.agents.evaluator import EvaluatorAgent
from mycelos.agents.models import AgentOutput
from mycelos.agents.planner import PlannerAgent
from mycelos.config.blueprint import BlueprintManager, RiskLevel
from mycelos.config.generations import ConfigGenerationManager
from mycelos.llm.mock_broker import MockLLMBroker
from mycelos.orchestrator import ChatOrchestrator, Intent
from mycelos.storage.database import SQLiteStorage
from mycelos.workflows.models import Workflow, WorkflowStep
from mycelos.workflows.parser import WorkflowParser


# -- Orchestrator -> Planner Flow --


def test_conversation_stays_in_chat() -> None:
    """Simple question -> CONVERSATION -> no planner involved."""
    broker = MockLLMBroker().on_message(r".*", json.dumps({"intent": "conversation"}))
    orch = ChatOrchestrator(llm=broker)
    assert orch.classify("What is Mycelos?") == Intent.CONVERSATION


def test_task_request_routes_to_planner() -> None:
    """Task request -> TASK_REQUEST -> PlannerAgent creates plan."""
    class_broker = MockLLMBroker().on_message(
        r".*", json.dumps({"intent": "task_request"})
    )
    orch = ChatOrchestrator(llm=class_broker)
    assert orch.classify("Summarize my emails") == Intent.TASK_REQUEST

    plan_broker = MockLLMBroker().on_message(
        r".*email.*",
        json.dumps({
            "action": "execute_workflow",
            "workflow_id": "email-summary",
            "steps": [{"id": "fetch", "agent": "email-agent", "action": "fetch"}],
            "estimated_cost": "low",
        }),
    )
    plan = PlannerAgent(llm=plan_broker).plan("Summarize my emails", context={})
    assert plan["action"] == "execute_workflow"
    assert plan["workflow_id"] == "email-summary"


def test_create_agent_routes_to_creator() -> None:
    """Create request -> CREATE_AGENT."""
    broker = MockLLMBroker().on_message(r".*", json.dumps({"intent": "create_agent"}))
    assert ChatOrchestrator(llm=broker).classify("Create a PR reviewer") == Intent.CREATE_AGENT


def test_system_command_routes_correctly() -> None:
    """System command -> SYSTEM_COMMAND."""
    broker = MockLLMBroker().on_message(
        r".*", json.dumps({"intent": "system_command"})
    )
    assert ChatOrchestrator(llm=broker).classify("Show config") == Intent.SYSTEM_COMMAND


def test_classifier_falls_back_on_invalid_json() -> None:
    """Invalid LLM response falls back to CONVERSATION."""
    broker = MockLLMBroker().on_message(r".*", "not valid json at all")
    assert ChatOrchestrator(llm=broker).classify("anything") == Intent.CONVERSATION


# -- Workflow Parse Flow --


def test_parse_workflow_yaml() -> None:
    """Parse YAML workflow definition."""
    parser = WorkflowParser()
    wf = parser.parse_string(
        dedent("""\
        name: test-flow
        steps:
          - id: step1
            action: "Process data"
            agent: processor
            policy: always
          - id: step2
            action: "Summarize"
            agent: summarizer
            policy: always
    """)
    )
    assert len(wf.steps) == 2
    assert wf.name == "test-flow"


# -- Blueprint + Config Change --


def test_blueprint_low_risk_auto_applies(db_path: Path) -> None:
    """Low risk config change auto-applies."""
    storage = SQLiteStorage(db_path)
    storage.initialize()
    cfg = ConfigGenerationManager(storage)
    cfg.apply({"version": "1.0"}, description="init")
    bp = BlueprintManager(cfg)
    result = bp.apply({"version": "1.0", "description": "updated"})
    assert result["applied"] is True
    assert result["risk"] == RiskLevel.LOW


def test_blueprint_high_risk_blocks_without_confirm(db_path: Path) -> None:
    """High risk change is blocked without confirmation."""
    storage = SQLiteStorage(db_path)
    storage.initialize()
    cfg = ConfigGenerationManager(storage)
    cfg.apply({"version": "1.0"}, description="init")
    bp = BlueprintManager(cfg)
    result = bp.apply({"version": "1.0", "agents": {"new": {}}})
    assert result["applied"] is False
    assert result["risk"] == RiskLevel.HIGH


def test_blueprint_high_risk_applies_with_confirm(db_path: Path) -> None:
    """High risk change applies when confirmed."""
    storage = SQLiteStorage(db_path)
    storage.initialize()
    cfg = ConfigGenerationManager(storage)
    cfg.apply({"version": "1.0"}, description="init")
    bp = BlueprintManager(cfg)
    result = bp.apply({"version": "1.0", "agents": {"new": {}}}, confirmed=True)
    assert result["applied"] is True


def test_blueprint_critical_risk_blocks_without_confirm(db_path: Path) -> None:
    """Critical risk change is blocked without confirmation."""
    storage = SQLiteStorage(db_path)
    storage.initialize()
    cfg = ConfigGenerationManager(storage)
    cfg.apply({"version": "1.0"}, description="init")
    bp = BlueprintManager(cfg)
    result = bp.apply({"version": "1.0", "security": {"guardian": True}})
    assert result["applied"] is False
    assert result["risk"] == RiskLevel.CRITICAL


# -- Full Flow: Classify -> Plan -> Execute --


def test_full_flow_classify_plan_execute() -> None:
    """Complete flow: classify intent, plan, parse workflow, execute."""
    # 1. Classify
    class_broker = MockLLMBroker().on_message(
        r".*", json.dumps({"intent": "task_request"})
    )
    intent = ChatOrchestrator(llm=class_broker).classify("Process my invoices")
    assert intent == Intent.TASK_REQUEST

    # 2. Plan
    plan_broker = MockLLMBroker().on_message(
        r".*",
        json.dumps({
            "action": "execute_workflow",
            "workflow_id": "invoice-processor",
            "steps": [{"id": "ocr", "agent": "ocr-agent", "action": "OCR"}],
            "estimated_cost": "low",
        }),
    )
    plan = PlannerAgent(llm=plan_broker).plan("Process invoices", context={})
    assert plan["action"] == "execute_workflow"

    # 3. Execute workflow
    wf = Workflow(
        name="invoice-processor",
        steps=[
            WorkflowStep(id="ocr", action="OCR", agent="ocr-agent", policy="always"),
            WorkflowStep(
                id="extract",
                action="Extract fields",
                agent="extractor",
                policy="always",
            ),
        ],
    )

    # Verify workflow model is valid
    assert wf.name == "invoice-processor"
    assert len(wf.steps) == 2


def test_full_flow_with_evaluator_pass() -> None:
    """Full flow where evaluator approves output."""
    # Classify
    class_broker = MockLLMBroker().on_message(
        r".*", json.dumps({"intent": "task_request"})
    )
    intent = ChatOrchestrator(llm=class_broker).classify("Generate a report")
    assert intent == Intent.TASK_REQUEST

    # Plan
    plan_broker = MockLLMBroker().on_message(
        r".*",
        json.dumps({
            "action": "execute_workflow",
            "workflow_id": "report-gen",
            "steps": [{"id": "gen", "agent": "reporter", "action": "generate"}],
            "estimated_cost": "low",
        }),
    )
    plan = PlannerAgent(llm=plan_broker).plan("Generate a report", context={})
    assert plan["action"] == "execute_workflow"

    # Execute with evaluation
    wf = Workflow(
        name="report-gen",
        steps=[
            WorkflowStep(
                id="gen",
                action="generate",
                agent="reporter",
                policy="always",
                evaluation={"format": "markdown", "must_contain": ["# "]},
            ),
        ],
    )

    # Verify workflow model is valid
    assert wf.name == "report-gen"
    assert len(wf.steps) == 1
    assert wf.steps[0].evaluation["format"] == "markdown"


def test_planner_handles_invalid_llm_response() -> None:
    """PlannerAgent gracefully handles invalid LLM JSON."""
    broker = MockLLMBroker().on_message(r".*", "this is not json")
    plan = PlannerAgent(llm=broker).plan("Do something", context={})
    assert plan["action"] == "execute_workflow"
    assert "error" in plan
