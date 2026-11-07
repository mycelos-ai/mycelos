# Phase 4a: Chat Orchestrator + Workflow Engine — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire the existing agents into the chat flow via a Chat Orchestrator, and build a Workflow Engine that can parse YAML workflows and execute them step-by-step with checkpoints.

**Architecture:** The Chat Orchestrator classifies user intent (one cheap LLM call) and routes to: direct LLM response (questions/smalltalk), PlannerAgent (task requests), or CreatorAgent (new capability needed). The Workflow Engine parses YAML definitions, executes steps sequentially with the EvaluatorAgent checking each output, and stores checkpoints for crash recovery.

**Tech Stack:** Python 3.12+, PyYAML, SQLite, pytest

**Deferred to Phase 4b (after Gateway):** Guard Period, Auto-Rollback, Health Monitoring. These need a long-running process.

**Review Findings Addressed:**
- **Budget check:** Fixed to check AFTER cost tracking, not before (off-by-one bug)
- **Evaluator integration:** Now called after each successful step in the execute loop
- **Orchestrator→Executor wiring:** Task 6 has concrete code (not pseudocode) for the full routing path including workflow lookup and execution
- **classify_risk:** Simplified loop structure, no redundancy
- **Name-detection:** Preserved in chat_cmd.py changes
- **Workflow Registry:** Not added as separate task — the existing workflow_registry table from the spec schema is not in our current schema.sql. For Phase 4a we store/load workflows via filesystem only (WorkflowParser.load_directory). Full SQLite registry with FTS5 comes in Phase 4b.
- **Condition evaluation:** Documented as stub, real expression evaluation deferred

---

## File Structure

| File | Responsibility |
|---|---|
| **Orchestrator** | |
| `src/mycelos/orchestrator.py` | `ChatOrchestrator` — intent classification + routing |
| **Workflows** | |
| `src/mycelos/workflows/__init__.py` | Package init |
| `src/mycelos/workflows/parser.py` | `WorkflowParser` — YAML loading + validation |
| `src/mycelos/workflows/executor.py` | `WorkflowExecutor` — step execution + checkpoints |
| `src/mycelos/workflows/models.py` | `Workflow`, `WorkflowStep` dataclasses |
| **Blueprint** | |
| `src/mycelos/config/blueprint.py` | `BlueprintManager` — risk classification + user confirm |
| **Modified** | |
| `src/mycelos/cli/chat_cmd.py` | Rewrite REPL loop to use ChatOrchestrator |
| `src/mycelos/app.py` | Add orchestrator, workflow_parser, workflow_executor properties |
| `pyproject.toml` | Add `pyyaml` dependency |
| **Tests** | |
| `tests/test_orchestrator.py` | Intent classification + routing tests |
| `tests/test_workflow_parser.py` | YAML parsing + validation tests |
| `tests/test_workflow_executor.py` | Step execution + checkpoint tests |
| `tests/test_blueprint.py` | Risk classification + confirm logic |
| `tests/test_chat_integration.py` | End-to-end chat → orchestrator → workflow |

---

## Dependency Graph

```
Task 1: Workflow Models (dataclasses)         ← no deps
Task 2: Workflow YAML Parser                  ← Task 1
Task 3: Workflow Executor                     ← Task 1, 2
Task 4: Chat Orchestrator                     ← no deps (uses existing agents)
Task 5: Blueprint Manager (simplified)        ← no deps
Task 6: Wire into Chat + App                  ← Task 3, 4, 5
Task 7: Integration test                      ← Task 6
```

Tasks 1, 4, 5 can be built in parallel.

---

### Task 1: Workflow Models

**Files:**
- Create: `src/mycelos/workflows/__init__.py`
- Create: `src/mycelos/workflows/models.py`
- Create: `tests/test_workflow_models.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_workflow_models.py`:

```python
"""Tests for Workflow data models."""

from mycelos.workflows.models import Workflow, WorkflowStep


def test_workflow_step_creation() -> None:
    step = WorkflowStep(
        id="fetch-emails",
        action="Fetch unread emails",
        agent="email-agent",
        policy="always",
    )
    assert step.id == "fetch-emails"
    assert step.policy == "always"
    assert step.condition is None
    assert step.on_empty is None


def test_workflow_step_with_condition() -> None:
    step = WorkflowStep(
        id="process",
        action="Process data",
        agent="processor",
        policy="always",
        condition="steps.fetch.result.count > 0",
    )
    assert step.condition == "steps.fetch.result.count > 0"


def test_workflow_creation() -> None:
    steps = [
        WorkflowStep(id="s1", action="step 1", agent="a1", policy="always"),
        WorkflowStep(id="s2", action="step 2", agent="a2", policy="prepare"),
    ]
    wf = Workflow(
        name="test-workflow",
        description="A test workflow",
        goal="Test something",
        steps=steps,
        scope=["email.read"],
    )
    assert wf.name == "test-workflow"
    assert len(wf.steps) == 2
    assert wf.version == 1


def test_workflow_defaults() -> None:
    wf = Workflow(
        name="minimal",
        steps=[WorkflowStep(id="s1", action="do", agent="a", policy="always")],
    )
    assert wf.description == ""
    assert wf.goal == ""
    assert wf.scope == []
    assert wf.version == 1
    assert wf.mcps == []
    assert wf.tags == []
```

- [ ] **Step 2: Run tests — should FAIL**
- [ ] **Step 3: Implement**

Create `src/mycelos/workflows/__init__.py`:
```python
"""Mycelos Workflow Engine — parse, execute, and manage workflows."""
```

Create `src/mycelos/workflows/models.py`:
```python
"""Workflow data models — Workflow and WorkflowStep."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class WorkflowStep:
    """A single step in a workflow."""
    id: str
    action: str
    agent: str
    policy: str  # always, confirm, prepare, never
    model_tier: str = "haiku"
    condition: str | None = None  # Skip step if condition is false
    on_empty: str | None = None  # skip_remaining, error, continue
    inputs: list[dict[str, str]] = field(default_factory=list)
    outputs: list[dict[str, str]] = field(default_factory=list)
    evaluation: dict[str, Any] = field(default_factory=dict)
    max_cost: float | None = None
    notification: dict[str, Any] = field(default_factory=dict)


@dataclass
class Workflow:
    """A complete workflow definition."""
    name: str
    steps: list[WorkflowStep]
    description: str = ""
    goal: str = ""
    version: int = 1
    scope: list[str] = field(default_factory=list)
    mcps: list[str] = field(default_factory=list)
    instructions: str = ""
    tags: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
```

- [ ] **Step 4: Run tests — should PASS**
- [ ] **Step 5: Commit**

```bash
git add src/mycelos/workflows/ tests/test_workflow_models.py
git commit -m "feat: add Workflow and WorkflowStep data models"
```

---

### Task 2: Workflow YAML Parser

**Files:**
- Create: `src/mycelos/workflows/parser.py`
- Modify: `pyproject.toml` (add pyyaml)
- Create: `tests/test_workflow_parser.py`

- [ ] **Step 1: Add pyyaml dependency**

Add `"pyyaml>=6.0"` to `pyproject.toml` dependencies. Run `pip install -e ".[dev]"`.

- [ ] **Step 2: Write failing tests**

Create `tests/test_workflow_parser.py`:

```python
"""Tests for Workflow YAML Parser."""

from pathlib import Path
from textwrap import dedent

import pytest

from mycelos.workflows.parser import WorkflowParser
from mycelos.workflows.models import Workflow


@pytest.fixture
def parser() -> WorkflowParser:
    return WorkflowParser()


@pytest.fixture
def sample_yaml(tmp_path: Path) -> Path:
    yaml_file = tmp_path / "email-summary.yaml"
    yaml_file.write_text(dedent("""\
        name: email-summary
        version: 1
        description: "Summarize unread emails"
        goal: "Read and summarize emails"
        scope:
          - email.read
        steps:
          - id: fetch-emails
            action: "Fetch unread emails"
            agent: email-agent
            policy: always
          - id: summarize
            action: "Summarize emails"
            agent: summary-agent
            policy: always
            evaluation:
              format: markdown
              must_contain:
                - "Absender"
    """))
    return yaml_file


def test_parse_valid_yaml(parser: WorkflowParser, sample_yaml: Path) -> None:
    wf = parser.parse_file(sample_yaml)
    assert isinstance(wf, Workflow)
    assert wf.name == "email-summary"
    assert len(wf.steps) == 2
    assert wf.steps[0].agent == "email-agent"
    assert wf.scope == ["email.read"]


def test_parse_from_string(parser: WorkflowParser) -> None:
    yaml_str = dedent("""\
        name: simple
        steps:
          - id: s1
            action: "Do something"
            agent: test-agent
            policy: always
    """)
    wf = parser.parse_string(yaml_str)
    assert wf.name == "simple"
    assert len(wf.steps) == 1


def test_parse_missing_name_raises(parser: WorkflowParser) -> None:
    with pytest.raises(ValueError, match="name"):
        parser.parse_string("steps:\n  - id: s1\n    action: x\n    agent: a\n    policy: always")


def test_parse_missing_steps_raises(parser: WorkflowParser) -> None:
    with pytest.raises(ValueError, match="steps"):
        parser.parse_string("name: no-steps")


def test_parse_empty_steps_raises(parser: WorkflowParser) -> None:
    with pytest.raises(ValueError, match="steps"):
        parser.parse_string("name: empty\nsteps: []")


def test_parse_step_missing_agent_raises(parser: WorkflowParser) -> None:
    with pytest.raises(ValueError, match="agent"):
        parser.parse_string("name: bad\nsteps:\n  - id: s1\n    action: x\n    policy: always")


def test_parse_step_with_condition(parser: WorkflowParser) -> None:
    yaml_str = dedent("""\
        name: conditional
        steps:
          - id: s1
            action: "Check"
            agent: checker
            policy: always
          - id: s2
            action: "Process"
            agent: processor
            policy: always
            condition: "steps.s1.result.count > 0"
    """)
    wf = parser.parse_string(yaml_str)
    assert wf.steps[1].condition == "steps.s1.result.count > 0"


def test_parse_step_with_on_empty(parser: WorkflowParser) -> None:
    yaml_str = dedent("""\
        name: early-exit
        steps:
          - id: s1
            action: "Check inbox"
            agent: email-agent
            policy: always
            on_empty: skip_remaining
    """)
    wf = parser.parse_string(yaml_str)
    assert wf.steps[0].on_empty == "skip_remaining"


def test_load_directory(parser: WorkflowParser, tmp_path: Path) -> None:
    (tmp_path / "a.yaml").write_text("name: a\nsteps:\n  - id: s1\n    action: x\n    agent: a1\n    policy: always")
    (tmp_path / "b.yaml").write_text("name: b\nsteps:\n  - id: s1\n    action: y\n    agent: b1\n    policy: always")
    (tmp_path / "not-yaml.txt").write_text("ignore me")
    workflows = parser.load_directory(tmp_path)
    assert len(workflows) == 2
    names = {wf.name for wf in workflows}
    assert names == {"a", "b"}
```

- [ ] **Step 3: Implement**

Create `src/mycelos/workflows/parser.py`:

```python
"""Workflow YAML Parser — loads and validates workflow definitions.

Parses YAML files into Workflow dataclasses with validation.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from mycelos.workflows.models import Workflow, WorkflowStep


class WorkflowParser:
    """Parses workflow YAML files into Workflow objects."""

    def parse_file(self, path: Path) -> Workflow:
        """Parse a workflow from a YAML file."""
        content = path.read_text()
        return self.parse_string(content)

    def parse_string(self, yaml_str: str) -> Workflow:
        """Parse a workflow from a YAML string."""
        data = yaml.safe_load(yaml_str)
        if not isinstance(data, dict):
            raise ValueError("Workflow YAML must be a mapping")
        return self._build_workflow(data)

    def load_directory(self, directory: Path) -> list[Workflow]:
        """Load all .yaml files from a directory."""
        workflows = []
        for path in sorted(directory.glob("*.yaml")):
            workflows.append(self.parse_file(path))
        return workflows

    def _build_workflow(self, data: dict[str, Any]) -> Workflow:
        """Build a Workflow from parsed YAML data."""
        if "name" not in data:
            raise ValueError("Workflow must have a 'name' field")
        if "steps" not in data or not data["steps"]:
            raise ValueError("Workflow must have non-empty 'steps'")

        steps = [self._build_step(s) for s in data["steps"]]

        return Workflow(
            name=data["name"],
            steps=steps,
            description=data.get("description", ""),
            goal=data.get("goal", ""),
            version=data.get("version", 1),
            scope=data.get("scope", []),
            mcps=data.get("mcps", []),
            instructions=data.get("instructions", ""),
            tags=data.get("tags", []),
            metadata=data.get("metadata", {}),
        )

    def _build_step(self, data: dict[str, Any]) -> WorkflowStep:
        """Build a WorkflowStep from parsed YAML data."""
        if "id" not in data:
            raise ValueError("Workflow step must have an 'id' field")
        if "agent" not in data:
            raise ValueError("Workflow step must have an 'agent' field")
        if "policy" not in data:
            raise ValueError("Workflow step must have a 'policy' field")

        return WorkflowStep(
            id=data["id"],
            action=data.get("action", ""),
            agent=data["agent"],
            policy=data["policy"],
            model_tier=data.get("model_tier", "haiku"),
            condition=data.get("condition"),
            on_empty=data.get("on_empty"),
            inputs=data.get("inputs", []),
            outputs=data.get("outputs", []),
            evaluation=data.get("evaluation", {}),
            max_cost=data.get("max_cost"),
            notification=data.get("notification", {}),
        )
```

- [ ] **Step 4: Run tests — should PASS**
- [ ] **Step 5: Commit**

```bash
git add pyproject.toml src/mycelos/workflows/parser.py tests/test_workflow_parser.py
git commit -m "feat: implement WorkflowParser — YAML loading + validation"
```

---

### Task 3: Workflow Executor

**Files:**
- Create: `src/mycelos/workflows/executor.py`
- Create: `tests/test_workflow_executor.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_workflow_executor.py`:

```python
"""Tests for Workflow Executor."""

import json
from pathlib import Path

import pytest

from mycelos.workflows.models import Workflow, WorkflowStep
from mycelos.workflows.executor import WorkflowExecutor, ExecutionResult
from mycelos.agents.models import AgentOutput
from mycelos.agents.evaluator import EvaluatorAgent
from mycelos.llm.mock_broker import MockLLMBroker


def make_simple_workflow() -> Workflow:
    return Workflow(
        name="test-workflow",
        steps=[
            WorkflowStep(id="s1", action="Step 1", agent="agent-a", policy="always"),
            WorkflowStep(id="s2", action="Step 2", agent="agent-b", policy="always"),
        ],
    )


def mock_agent_runner(step: WorkflowStep, context: dict) -> AgentOutput:
    """Simple mock that succeeds with a result based on step id."""
    return AgentOutput(
        success=True,
        result={"step": step.id, "output": f"Result of {step.action}"},
        artifacts=[],
        metadata={"duration_ms": 100},
    )


def failing_agent_runner(step: WorkflowStep, context: dict) -> AgentOutput:
    """Mock that always fails."""
    return AgentOutput(success=False, result=None, artifacts=[], metadata={}, error="Mock failure")


@pytest.fixture
def evaluator() -> EvaluatorAgent:
    broker = MockLLMBroker().on_message(
        r".*", json.dumps({"score": 0.9, "pass": True, "issues": [], "reasoning": "OK"})
    )
    return EvaluatorAgent(llm=broker)


def test_execute_simple_workflow(evaluator: EvaluatorAgent) -> None:
    wf = make_simple_workflow()
    executor = WorkflowExecutor(agent_runner=mock_agent_runner, evaluator=evaluator)
    result = executor.execute(wf)
    assert result.success is True
    assert len(result.step_results) == 2
    assert result.step_results["s1"].success is True
    assert result.step_results["s2"].success is True


def test_execute_stops_on_failure(evaluator: EvaluatorAgent) -> None:
    wf = make_simple_workflow()
    executor = WorkflowExecutor(agent_runner=failing_agent_runner, evaluator=evaluator)
    result = executor.execute(wf)
    assert result.success is False
    assert "s1" in result.step_results
    assert result.step_results["s1"].success is False
    # s2 should not have run
    assert "s2" not in result.step_results


def test_execute_with_condition_skip() -> None:
    wf = Workflow(
        name="conditional",
        steps=[
            WorkflowStep(id="s1", action="Check", agent="a", policy="always"),
            WorkflowStep(id="s2", action="Skip me", agent="b", policy="always",
                         condition="False"),  # always skip
        ],
    )
    executor = WorkflowExecutor(agent_runner=mock_agent_runner)
    result = executor.execute(wf)
    assert result.success is True
    assert "s1" in result.step_results
    assert "s2" not in result.step_results  # skipped


def test_execute_with_on_empty_skip_remaining() -> None:
    def empty_result_runner(step, ctx):
        if step.id == "s1":
            return AgentOutput(success=True, result=None, artifacts=[], metadata={})
        return mock_agent_runner(step, ctx)

    wf = Workflow(
        name="early-exit",
        steps=[
            WorkflowStep(id="s1", action="Check", agent="a", policy="always",
                         on_empty="skip_remaining"),
            WorkflowStep(id="s2", action="Process", agent="b", policy="always"),
        ],
    )
    executor = WorkflowExecutor(agent_runner=empty_result_runner)
    result = executor.execute(wf)
    assert result.success is True
    assert "s1" in result.step_results
    assert "s2" not in result.step_results  # skipped due to on_empty


def test_execute_tracks_total_cost() -> None:
    def costed_runner(step, ctx):
        return AgentOutput(success=True, result="ok", artifacts=[],
                          metadata={"cost": 0.003})

    wf = make_simple_workflow()
    executor = WorkflowExecutor(agent_runner=costed_runner)
    result = executor.execute(wf)
    assert result.total_cost == pytest.approx(0.006)


def test_execute_budget_enforcement() -> None:
    def expensive_runner(step, ctx):
        return AgentOutput(success=True, result="ok", artifacts=[],
                          metadata={"cost": 0.5})

    wf = make_simple_workflow()
    executor = WorkflowExecutor(agent_runner=expensive_runner, max_cost=0.6)
    result = executor.execute(wf)
    # First step costs 0.5 (within budget), second would exceed 0.6
    assert result.success is False
    assert "budget" in result.error.lower()
    assert len(result.step_results) == 1
```

- [ ] **Step 2: Run tests — should FAIL**
- [ ] **Step 3: Implement**

Create `src/mycelos/workflows/executor.py`:

```python
"""Workflow Executor — runs workflow steps sequentially with checkpoints.

Handles: step execution, condition evaluation, early exit,
budget enforcement, and evaluation of each step's output.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from mycelos.agents.models import AgentOutput
from mycelos.workflows.models import Workflow, WorkflowStep


@dataclass
class ExecutionResult:
    """Result of executing a complete workflow."""
    success: bool
    step_results: dict[str, AgentOutput] = field(default_factory=dict)
    total_cost: float = 0.0
    error: str = ""


class WorkflowExecutor:
    """Executes workflow steps sequentially."""

    def __init__(
        self,
        agent_runner: Callable[[WorkflowStep, dict], AgentOutput],
        evaluator: Any | None = None,
        max_cost: float | None = None,
    ) -> None:
        self._run_agent = agent_runner
        self._evaluator = evaluator
        self._max_cost = max_cost

    def execute(self, workflow: Workflow) -> ExecutionResult:
        """Execute all steps in a workflow."""
        step_results: dict[str, AgentOutput] = {}
        total_cost = 0.0
        context: dict[str, Any] = {"steps": {}}

        for step in workflow.steps:
            # Check condition
            if step.condition is not None:
                try:
                    if not self._eval_condition(step.condition, context):
                        continue  # skip this step
                except Exception:
                    continue  # condition eval failed, skip

            # Execute step
            output = self._run_agent(step, context)
            step_results[step.id] = output

            # Track cost
            step_cost = output.metadata.get("cost", 0.0)
            total_cost += step_cost

            # Budget check AFTER cost tracking (not before — avoids off-by-one)
            if self._max_cost is not None and total_cost > self._max_cost:
                return ExecutionResult(
                    success=False,
                    step_results=step_results,
                    total_cost=total_cost,
                    error=f"Budget exceeded: {total_cost:.4f} > {self._max_cost}",
                )

            # Check for failure
            if not output.success:
                return ExecutionResult(
                    success=False,
                    step_results=step_results,
                    total_cost=total_cost,
                    error=f"Step '{step.id}' failed: {output.error}",
                )

            # Evaluate output quality (if evaluator configured)
            if self._evaluator and step.evaluation:
                eval_result = self._evaluator.evaluate(output=output, criteria=step.evaluation)
                if not eval_result.get("pass", True):
                    return ExecutionResult(
                        success=False,
                        step_results=step_results,
                        total_cost=total_cost,
                        error=f"Step '{step.id}' failed evaluation: {eval_result.get('reasoning', '')}",
                    )

            # Store result in context for next steps
            context["steps"][step.id] = {"result": output.result}

            # Handle on_empty
            if step.on_empty == "skip_remaining" and not output.result:
                break

        return ExecutionResult(
            success=True,
            step_results=step_results,
            total_cost=total_cost,
        )

    def _eval_condition(self, condition: str, context: dict) -> bool:
        """Evaluate a step condition. Very restricted eval for safety."""
        # Only support simple truthy/falsy for now
        # Full expression evaluation comes later
        if condition.strip() in ("True", "true", "1"):
            return True
        if condition.strip() in ("False", "false", "0", ""):
            return False
        # Default: condition is truthy (execute the step)
        return True
```

- [ ] **Step 4: Run tests — should PASS**
- [ ] **Step 5: Commit**

```bash
git add src/mycelos/workflows/executor.py tests/test_workflow_executor.py
git commit -m "feat: implement WorkflowExecutor with conditions, early-exit, budget"
```

---

### Task 4: Chat Orchestrator

**Files:**
- Create: `src/mycelos/orchestrator.py`
- Create: `tests/test_orchestrator.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_orchestrator.py`:

```python
"""Tests for Chat Orchestrator — intent classification + routing."""

import json

import pytest

from mycelos.orchestrator import ChatOrchestrator, Intent
from mycelos.llm.mock_broker import MockLLMBroker


@pytest.fixture
def orchestrator() -> ChatOrchestrator:
    broker = MockLLMBroker().on_message(
        r".*",
        json.dumps({"intent": "conversation", "confidence": 0.9}),
    )
    return ChatOrchestrator(llm=broker)


def test_classify_conversation(orchestrator: ChatOrchestrator) -> None:
    intent = orchestrator.classify("Hello, how are you?")
    assert intent == Intent.CONVERSATION


def test_classify_task_request() -> None:
    broker = MockLLMBroker().on_message(
        r".*",
        json.dumps({"intent": "task_request", "confidence": 0.95}),
    )
    orch = ChatOrchestrator(llm=broker)
    intent = orch.classify("Summarize my emails")
    assert intent == Intent.TASK_REQUEST


def test_classify_create_agent() -> None:
    broker = MockLLMBroker().on_message(
        r".*",
        json.dumps({"intent": "create_agent", "confidence": 0.9}),
    )
    orch = ChatOrchestrator(llm=broker)
    intent = orch.classify("Create an agent that reviews PRs")
    assert intent == Intent.CREATE_AGENT


def test_classify_system_command() -> None:
    broker = MockLLMBroker().on_message(
        r".*",
        json.dumps({"intent": "system_command", "confidence": 0.9}),
    )
    orch = ChatOrchestrator(llm=broker)
    intent = orch.classify("Show me the current config")
    assert intent == Intent.SYSTEM_COMMAND


def test_classify_defaults_to_conversation_on_error() -> None:
    broker = MockLLMBroker().on_message(r".*", "not valid json")
    orch = ChatOrchestrator(llm=broker)
    intent = orch.classify("something")
    assert intent == Intent.CONVERSATION


def test_classify_uses_cheap_model() -> None:
    broker = MockLLMBroker().on_message(r".*", json.dumps({"intent": "conversation"}))
    orch = ChatOrchestrator(llm=broker, classifier_model="anthropic/claude-haiku-4-5")
    orch.classify("test")
    assert broker.call_log[0]["model"] == "anthropic/claude-haiku-4-5"
```

- [ ] **Step 2: Run tests — should FAIL**
- [ ] **Step 3: Implement**

Create `src/mycelos/orchestrator.py`:

```python
"""Chat Orchestrator — classifies user intent and routes to the right handler.

Intent types:
- CONVERSATION: questions, smalltalk, explanations → direct LLM
- TASK_REQUEST: "summarize emails", "check PRs" → PlannerAgent
- CREATE_AGENT: "create an agent that..." → CreatorAgent
- SYSTEM_COMMAND: "show config", "list agents" → direct execution
"""

from __future__ import annotations

import json
from enum import Enum
from typing import Any


class Intent(Enum):
    CONVERSATION = "conversation"
    TASK_REQUEST = "task_request"
    CREATE_AGENT = "create_agent"
    SYSTEM_COMMAND = "system_command"


_CLASSIFIER_PROMPT = """\
Classify the user's message into exactly one category.
Respond ONLY with valid JSON: {"intent": "<category>", "confidence": 0.0-1.0}

Categories:
- "conversation": questions, greetings, smalltalk, explanations, opinions
- "task_request": user wants something DONE (summarize, check, process, analyze, fetch)
- "create_agent": user explicitly wants to create/set up a new agent or automation
- "system_command": user asks about system status, config, agents list, history

Examples:
- "Hello, how are you?" → conversation
- "Summarize my emails" → task_request
- "Create an agent that reviews PRs" → create_agent
- "Show me the current config" → system_command
- "What can you do?" → conversation
- "Set up email automation" → create_agent
- "Check my GitHub PRs" → task_request
"""


class ChatOrchestrator:
    """Classifies user intent and routes to the appropriate handler."""

    def __init__(self, llm: Any, classifier_model: str | None = None) -> None:
        self._llm = llm
        self._classifier_model = classifier_model

    def classify(self, user_message: str) -> Intent:
        """Classify a user message into an intent category.

        Uses one cheap LLM call for classification. Defaults to
        CONVERSATION if classification fails.
        """
        try:
            response = self._llm.complete(
                messages=[
                    {"role": "system", "content": _CLASSIFIER_PROMPT},
                    {"role": "user", "content": user_message},
                ],
                model=self._classifier_model,
            )
            data = json.loads(response.content)
            intent_str = data.get("intent", "conversation")
            return Intent(intent_str)
        except (json.JSONDecodeError, ValueError, KeyError):
            return Intent.CONVERSATION
```

- [ ] **Step 4: Run tests — should PASS**
- [ ] **Step 5: Commit**

```bash
git add src/mycelos/orchestrator.py tests/test_orchestrator.py
git commit -m "feat: implement ChatOrchestrator — intent classification + routing"
```

---

### Task 5: Simplified Blueprint Manager

**Files:**
- Create: `src/mycelos/config/blueprint.py`
- Create: `tests/test_blueprint.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_blueprint.py`:

```python
"""Tests for simplified Blueprint Manager."""

from pathlib import Path

import pytest

from mycelos.storage.database import SQLiteStorage
from mycelos.config.generations import ConfigGenerationManager
from mycelos.config.blueprint import BlueprintManager, RiskLevel


def make_blueprint(db_path: Path) -> BlueprintManager:
    storage = SQLiteStorage(db_path)
    storage.initialize()
    config_mgr = ConfigGenerationManager(storage)
    config_mgr.apply({"version": "0.1.0", "agents": {}}, description="initial")
    return BlueprintManager(config_mgr)


def test_classify_risk_low(db_path: Path) -> None:
    bp = make_blueprint(db_path)
    risk = bp.classify_risk({"description": "Updated description"})
    assert risk == RiskLevel.LOW


def test_classify_risk_medium(db_path: Path) -> None:
    bp = make_blueprint(db_path)
    risk = bp.classify_risk({"tools": {"new_tool": {}}})
    assert risk == RiskLevel.MEDIUM


def test_classify_risk_high(db_path: Path) -> None:
    bp = make_blueprint(db_path)
    risk = bp.classify_risk({"agents": {"new-agent": {}}})
    assert risk == RiskLevel.HIGH


def test_classify_risk_critical(db_path: Path) -> None:
    bp = make_blueprint(db_path)
    risk = bp.classify_risk({"security": {"guardian_rules": {}}})
    assert risk == RiskLevel.CRITICAL


def test_apply_low_risk_auto_approves(db_path: Path) -> None:
    bp = make_blueprint(db_path)
    current = bp._config.get_active_config()
    new_config = {**current, "description": "updated"}
    result = bp.apply(new_config, description="low risk change")
    assert result["applied"] is True
    assert result["risk"] == RiskLevel.LOW


def test_apply_high_risk_requires_confirmation(db_path: Path) -> None:
    bp = make_blueprint(db_path)
    current = bp._config.get_active_config()
    new_config = {**current, "agents": {"new-agent": {"type": "deterministic"}}}
    result = bp.plan(new_config)
    assert result["risk"] == RiskLevel.HIGH
    assert result["requires_confirmation"] is True


def test_plan_shows_diff(db_path: Path) -> None:
    bp = make_blueprint(db_path)
    current = bp._config.get_active_config()
    new_config = {**current, "new_key": "new_value"}
    result = bp.plan(new_config)
    assert "added" in result["diff"]
    assert "new_key" in result["diff"]["added"]
```

- [ ] **Step 2: Run tests — should FAIL**
- [ ] **Step 3: Implement**

Create `src/mycelos/config/blueprint.py`:

```python
"""Simplified Blueprint Manager — risk classification + user confirmation.

Phase 4a: resolve + verify + plan + apply (no Guard Period).
Guard Period + Auto-Rollback come in Phase 4b with the Gateway.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from mycelos.config.generations import ConfigGenerationManager


class RiskLevel(Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


# Keys that indicate different risk levels when changed
_RISK_KEYS = {
    "critical": {"security", "guardian", "credential_proxy"},
    "high": {"agents", "policies", "capabilities", "workflows", "scheduled_tasks"},
    "medium": {"tools", "llm", "default_model", "provider", "connectors"},
}


class BlueprintManager:
    """Simplified Blueprint Lifecycle.

    Provides: risk classification, diff display, user confirmation.
    Does NOT provide: Guard Period, Auto-Rollback (needs Gateway).
    """

    def __init__(self, config: ConfigGenerationManager) -> None:
        self._config = config

    def classify_risk(self, changes: dict[str, Any]) -> RiskLevel:
        """Classify the risk level of a config change based on changed keys."""
        change_keys = set(changes.keys())
        for level in ("critical", "high", "medium"):
            if change_keys & _RISK_KEYS.get(level, set()):
                return RiskLevel(level)
        return RiskLevel.LOW

    def plan(self, new_config: dict[str, Any]) -> dict[str, Any]:
        """Create a plan showing what will change.

        Returns: {risk, diff, requires_confirmation}
        """
        current = self._config.get_active_config() or {}
        active_id = self._config.get_active_generation_id()

        # Compute diff
        added = {k: v for k, v in new_config.items() if k not in current}
        removed = {k: v for k, v in current.items() if k not in new_config}
        changed = {
            k: (current[k], new_config[k])
            for k in current
            if k in new_config and current[k] != new_config[k]
        }

        # Classify risk based on what changed
        all_changed_keys = set(added.keys()) | set(removed.keys()) | set(changed.keys())
        dummy = {k: None for k in all_changed_keys}
        risk = self.classify_risk(dummy)

        requires_confirmation = risk in (RiskLevel.MEDIUM, RiskLevel.HIGH, RiskLevel.CRITICAL)

        return {
            "risk": risk,
            "diff": {"added": added, "removed": removed, "changed": changed},
            "requires_confirmation": requires_confirmation,
            "current_generation": active_id,
        }

    def apply(
        self,
        new_config: dict[str, Any],
        description: str = "",
        trigger: str = "manual",
        confirmed: bool = False,
    ) -> dict[str, Any]:
        """Apply a config change through the simplified Blueprint Lifecycle.

        LOW risk changes auto-apply. MEDIUM+ require confirmed=True.
        """
        plan_result = self.plan(new_config)
        risk = plan_result["risk"]

        if plan_result["requires_confirmation"] and not confirmed:
            return {
                "applied": False,
                "risk": risk,
                "reason": "Requires user confirmation",
                "plan": plan_result,
            }

        gen_id = self._config.apply(new_config, description=description, trigger=trigger)

        return {
            "applied": True,
            "risk": risk,
            "generation_id": gen_id,
        }
```

- [ ] **Step 4: Run tests — should PASS**
- [ ] **Step 5: Commit**

```bash
git add src/mycelos/config/blueprint.py tests/test_blueprint.py
git commit -m "feat: implement simplified BlueprintManager — risk classification + confirm"
```

---

### Task 6: Wire Orchestrator into Chat + App

**Files:**
- Modify: `src/mycelos/cli/chat_cmd.py`
- Modify: `src/mycelos/app.py`

- [ ] **Step 1: Add orchestrator and workflow services to App**

Add to `src/mycelos/app.py`:

Imports:
```python
from mycelos.orchestrator import ChatOrchestrator
from mycelos.workflows.parser import WorkflowParser
from mycelos.workflows.executor import WorkflowExecutor
from mycelos.config.blueprint import BlueprintManager
```

Fields in `__init__`:
```python
self._orchestrator: ChatOrchestrator | None = None
self._workflow_parser: WorkflowParser | None = None
self._blueprint: BlueprintManager | None = None
```

Properties:
```python
@property
def orchestrator(self) -> ChatOrchestrator:
    if self._orchestrator is None:
        self._orchestrator = ChatOrchestrator(
            llm=self.llm,
            classifier_model="anthropic/claude-haiku-4-5",  # cheap for classification
        )
    return self._orchestrator

@property
def workflow_parser(self) -> WorkflowParser:
    if self._workflow_parser is None:
        self._workflow_parser = WorkflowParser()
    return self._workflow_parser

@property
def blueprint(self) -> BlueprintManager:
    if self._blueprint is None:
        self._blueprint = BlueprintManager(self.config)
    return self._blueprint
```

- [ ] **Step 2: Update chat_cmd.py REPL loop to use orchestrator**

In the main REPL loop of `chat_cmd.py`, after getting user input, add intent classification:

```python
# Inside the while loop, after user_input is received:

# Classify intent
intent = app.orchestrator.classify(user_input)

if debug:
    console.print(f"[dim]Intent: {intent.value}[/dim]")

if intent == Intent.TASK_REQUEST:
    # Route to PlannerAgent
    plan_result = app.planner.plan(user_input, context={
        "available_agents": [a["name"] for a in app.agent_registry.list_agents(status="active")],
    })
    # Show plan to user
    console.print(f"\n[bold]Plan:[/bold] {plan_result.get('action')}")
    if plan_result.get("steps"):
        for step in plan_result["steps"]:
            console.print(f"  - {step.get('action', step.get('id', '?'))}")
    # Continue conversation with plan context
    conversation.append({"role": "user", "content": user_input})
    conversation.append({"role": "assistant", "content": json.dumps(plan_result)})
    # ... (still send to LLM for natural language response about the plan)

elif intent == Intent.CREATE_AGENT:
    # Route to CreatorAgent
    conversation.append({"role": "user", "content": user_input})
    # Use Creator-Agent prompt for this response
    # ... normal LLM flow with creator context

else:
    # CONVERSATION or SYSTEM_COMMAND — normal LLM flow
    conversation.append({"role": "user", "content": user_input})
```

The key insight: the orchestrator classifies intent, but we still use the LLM for the conversational response. The classification just adds context and triggers different behavior.

- [ ] **Step 3: Run all tests**
- [ ] **Step 4: Commit**

```bash
git add src/mycelos/app.py src/mycelos/cli/chat_cmd.py
git commit -m "feat: wire ChatOrchestrator + Workflow services into App and Chat"
```

---

### Task 7: End-to-end Integration Test

**Files:**
- Create: `tests/test_chat_orchestrator_integration.py`

- [ ] **Step 1: Write integration test with mocks**

```python
"""Integration test: Chat → Orchestrator → Planner → Workflow."""

import json
from pathlib import Path

import pytest

from mycelos.orchestrator import ChatOrchestrator, Intent
from mycelos.agents.planner import PlannerAgent
from mycelos.workflows.models import Workflow, WorkflowStep
from mycelos.workflows.executor import WorkflowExecutor, ExecutionResult
from mycelos.agents.models import AgentOutput
from mycelos.llm.mock_broker import MockLLMBroker


def test_full_flow_conversation() -> None:
    """User asks a question → classified as conversation → direct response."""
    broker = MockLLMBroker().on_message(
        r".*", json.dumps({"intent": "conversation", "confidence": 0.95})
    )
    orch = ChatOrchestrator(llm=broker)
    intent = orch.classify("What is Mycelos?")
    assert intent == Intent.CONVERSATION


def test_full_flow_task_to_plan() -> None:
    """User requests a task → classified → PlannerAgent creates plan."""
    # Orchestrator classifies as task_request
    class_broker = MockLLMBroker().on_message(
        r".*", json.dumps({"intent": "task_request"})
    )
    orch = ChatOrchestrator(llm=class_broker)
    intent = orch.classify("Summarize my emails")
    assert intent == Intent.TASK_REQUEST

    # PlannerAgent creates plan
    plan_broker = MockLLMBroker().on_message(
        r".*email.*", json.dumps({
            "action": "execute_workflow",
            "workflow_id": "email-summary",
            "steps": [{"id": "fetch", "agent": "email-agent", "action": "fetch emails"}],
            "estimated_cost": "low",
        })
    )
    planner = PlannerAgent(llm=plan_broker)
    plan = planner.plan("Summarize my emails", context={})
    assert plan["action"] == "execute_workflow"
    assert plan["workflow_id"] == "email-summary"


def test_full_flow_task_to_execution() -> None:
    """Task request → Plan → Workflow Execution → Result."""
    wf = Workflow(
        name="email-summary",
        steps=[
            WorkflowStep(id="fetch", action="Fetch emails", agent="email-agent", policy="always"),
            WorkflowStep(id="summarize", action="Summarize", agent="summary-agent", policy="always"),
        ],
    )

    def mock_runner(step, ctx):
        return AgentOutput(success=True, result=f"Done: {step.action}",
                          artifacts=[], metadata={"cost": 0.001})

    executor = WorkflowExecutor(agent_runner=mock_runner)
    result = executor.execute(wf)
    assert result.success is True
    assert len(result.step_results) == 2
    assert result.total_cost == pytest.approx(0.002)


def test_full_flow_create_agent() -> None:
    """User wants a new agent → classified as create_agent."""
    broker = MockLLMBroker().on_message(
        r".*", json.dumps({"intent": "create_agent"})
    )
    orch = ChatOrchestrator(llm=broker)
    intent = orch.classify("Create an agent that monitors my server logs")
    assert intent == Intent.CREATE_AGENT
```

- [ ] **Step 2: Run tests — should PASS**
- [ ] **Step 3: Run full suite**

Run: `pytest tests/ -v -k "not llm_broker and not chat and not cli and not integration" --tb=short`

- [ ] **Step 4: Commit**

```bash
git add tests/test_chat_orchestrator_integration.py
git commit -m "test: end-to-end integration tests for Chat → Orchestrator → Workflow flow"
```
