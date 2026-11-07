# Workflow Execution Plan — Prio 1: Workflows tatsaechlich ausfuehren

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When a user confirms a plan in chat, actually execute the workflow using WorkflowExecutor with real connectors, update task status, show results progressively.

**Architecture:** Add a `WorkflowRunner` that bridges PlannerAgent output to WorkflowExecutor — it builds an `agent_runner` callable that dispatches to real connectors (search.web, http.get, google.*). Chat REPL detects plan confirmation ("ja", "yes", "mach das") and triggers execution. Task status flows through running → completed/failed with cost tracking.

**Tech Stack:** Python 3.12+, existing WorkflowExecutor, ToolRegistry, connectors, TaskManager

---

## What exists today

- **PlannerAgent** returns a plan dict with `action`, `workflow_id`, `steps[]`
- **WorkflowExecutor** takes an `agent_runner` callable and a `Workflow` object, runs steps sequentially
- **ToolRegistry** + connectors (search.web, search.news, http.get, google.*) are all registered
- **TaskManager** creates tasks and updates status, but nothing transitions to "running" or "completed"
- **chat_cmd.py** shows the plan via LLM but has no confirmation detection or execution trigger
- **RouteResult** carries `task_id` and `plan` but they're never acted upon after display

## What's missing (the gap)

1. **Confirmation detection** — after showing a plan, detect "ja"/"yes"/"mach das" as plan confirmation
2. **WorkflowRunner** — translates plan steps into real connector calls via ToolRegistry
3. **Execution trigger in REPL** — when confirmed, set task to "running", execute, show results, set "completed"
4. **Cost + scoring** — after execution, update task cost and agent reputation

---

## File Structure

- **Create:** `src/mycelos/workflows/runner.py` — WorkflowRunner: builds `agent_runner` from ToolRegistry, executes workflow
- **Modify:** `src/mycelos/cli/chat_cmd.py` — confirmation detection, execution trigger, result display
- **Modify:** `src/mycelos/orchestrator.py` — add `pending_task` state to track awaiting-confirmation tasks
- **Create:** `tests/test_workflow_runner.py` — unit tests for WorkflowRunner
- **Create:** `tests/test_chat_confirmation.py` — unit tests for confirmation detection
- **Create:** `tests/test_chat_helpers.py` — unit tests for _resolve_workflow, _extract_inputs, _format_execution_result

---

## Tasks

### Task 1: WorkflowRunner — bridge connectors to WorkflowExecutor

The core piece: a class that takes a ToolRegistry and builds an `agent_runner` callable that WorkflowExecutor can use. Each workflow step's `agent` field maps to a tool name (e.g., `search-agent` → `search.web`).

**Files:**
- Create: `src/mycelos/workflows/runner.py`
- Create: `tests/test_workflow_runner.py`

**Steps:**

- [ ] **Step 1: Write failing test — runner dispatches to correct tool**

```python
# tests/test_workflow_runner.py
"""Tests for WorkflowRunner — bridges ToolRegistry to WorkflowExecutor."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from mycelos.workflows.models import Workflow, WorkflowStep
from mycelos.workflows.runner import WorkflowRunner


def _make_registry(*tools: tuple[str, callable]) -> MagicMock:
    """Create a mock ToolRegistry with given tools."""
    registry = MagicMock()
    tool_map = {}
    for name, handler in tools:
        td = MagicMock()
        td.handler = handler
        td.name = name
        tool_map[name] = td
    registry.get.side_effect = lambda n: tool_map.get(n)
    return registry


def test_runner_dispatches_search_step():
    """WorkflowRunner calls search.web for a search step."""
    def fake_search(query, max_results=5):
        return [{"title": "AI News", "url": "https://example.com", "snippet": "Big news"}]

    registry = _make_registry(("search.web", fake_search))
    runner = WorkflowRunner(registry)

    step = WorkflowStep(id="search", action="Search news", agent="search-agent", policy="always")
    workflow = Workflow(name="test", steps=[step])

    result = runner.execute(workflow, inputs={"query": "AI news"})
    assert result.success
    assert "search" in result.step_results
    assert result.step_results["search"].success


def test_runner_chains_steps():
    """WorkflowRunner passes context between steps."""
    def fake_search(query, max_results=5):
        return [{"title": "Result", "url": "https://example.com", "snippet": "Found it"}]

    def fake_summarize(text):
        return f"Summary of: {text}"

    registry = _make_registry(("search.web", fake_search), ("llm.summarize", fake_summarize))
    runner = WorkflowRunner(registry)

    steps = [
        WorkflowStep(id="search", action="Search", agent="search-agent", policy="always"),
        WorkflowStep(id="summarize", action="Summarize", agent="summary-agent", policy="always"),
    ]
    workflow = Workflow(name="test", steps=steps)

    result = runner.execute(workflow, inputs={"query": "test"})
    assert result.success
    assert len(result.step_results) == 2


def test_runner_handles_missing_tool():
    """WorkflowRunner fails gracefully when tool not found."""
    registry = _make_registry()  # empty
    runner = WorkflowRunner(registry)

    step = WorkflowStep(id="s1", action="Do something", agent="nonexistent-agent", policy="always")
    workflow = Workflow(name="test", steps=[step])

    result = runner.execute(workflow, inputs={})
    assert not result.success
    assert "not found" in result.error.lower() or "s1" in result.error


def test_runner_tracks_cost():
    """WorkflowRunner accumulates cost from steps."""
    def fake_search(query, max_results=5):
        return [{"title": "R", "url": "u", "snippet": "s"}]

    registry = _make_registry(("search.web", fake_search))
    runner = WorkflowRunner(registry)

    step = WorkflowStep(id="search", action="Search", agent="search-agent", policy="always")
    workflow = Workflow(name="test", steps=[step])

    result = runner.execute(workflow, inputs={"query": "test"})
    assert result.total_cost >= 0.0


def test_runner_returns_final_output():
    """WorkflowRunner provides the last step's result as final_output."""
    def fake_search(query, max_results=5):
        return [{"title": "Found", "url": "https://ex.com", "snippet": "Done"}]

    registry = _make_registry(("search.web", fake_search))
    runner = WorkflowRunner(registry)

    step = WorkflowStep(id="search", action="Search", agent="search-agent", policy="always")
    workflow = Workflow(name="test", steps=[step])

    result = runner.execute(workflow, inputs={"query": "test"})
    assert result.success
    # final_output should be the last step's result
    assert result.step_results["search"].result is not None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_workflow_runner.py -v`
Expected: ImportError — `cannot import name 'WorkflowRunner' from 'mycelos.workflows.runner'`

- [ ] **Step 3: Implement WorkflowRunner**

```python
# src/mycelos/workflows/runner.py
"""WorkflowRunner — bridges ToolRegistry connectors to WorkflowExecutor.

Maps workflow step agent names to real tools and executes the workflow.
"""

from __future__ import annotations

from typing import Any

from mycelos.agents.models import AgentOutput
from mycelos.execution.tools import ToolRegistry
from mycelos.workflows.executor import ExecutionResult, WorkflowExecutor
from mycelos.workflows.models import Workflow, WorkflowStep

# Map workflow agent names to tool names
AGENT_TOOL_MAP: dict[str, str] = {
    "search-agent": "search.web",
    "news-agent": "search.news",
    "http-agent": "http.get",
    "summary-agent": "llm.summarize",
    "gmail-agent": "google.gmail.search",
    "calendar-agent": "google.calendar.list",
    "drive-agent": "google.drive.list",
}


class WorkflowRunner:
    """Executes workflows using real connectors from the ToolRegistry.

    Bridges the gap between PlannerAgent output (step definitions) and
    WorkflowExecutor (which needs an agent_runner callable).
    """

    def __init__(
        self,
        tool_registry: ToolRegistry,
        llm: Any = None,
        evaluator: Any = None,
        max_cost: float | None = None,
    ) -> None:
        self._registry = tool_registry
        self._llm = llm
        self._evaluator = evaluator
        self._max_cost = max_cost

    def execute(
        self,
        workflow: Workflow,
        inputs: dict[str, Any] | None = None,
        on_step_complete: Any = None,
    ) -> ExecutionResult:
        """Execute a workflow with real connectors.

        Args:
            workflow: The workflow to execute.
            inputs: Input parameters (e.g., query, topic).
            on_step_complete: Optional callback(step_id, output) for progress.

        Returns:
            ExecutionResult from WorkflowExecutor.
        """
        self._inputs = inputs or {}
        self._on_step_complete = on_step_complete

        executor = WorkflowExecutor(
            agent_runner=self._run_step,
            evaluator=self._evaluator,
            max_cost=self._max_cost,
        )
        return executor.execute(workflow)

    def _run_step(
        self, step: WorkflowStep, context: dict[str, Any]
    ) -> AgentOutput:
        """Execute a single workflow step using the appropriate tool.

        Args:
            step: The workflow step to execute.
            context: Context from previous steps.

        Returns:
            AgentOutput with the step's result.
        """
        tool_name = AGENT_TOOL_MAP.get(step.agent, step.agent)
        tool_def = self._registry.get(tool_name)

        if tool_def is None:
            return AgentOutput(
                success=False,
                result=None,
                artifacts=[],
                metadata={},
                error=f"Tool not found for agent '{step.agent}' (looked up '{tool_name}')",
            )

        try:
            args = self._build_args(step, context)
            result = tool_def.handler(**args)

            if self._on_step_complete:
                self._on_step_complete(step.id, result)

            return AgentOutput(
                success=True,
                result=result,
                artifacts=[],
                metadata={"cost": 0.0, "tool": tool_name},
            )
        except Exception as e:
            return AgentOutput(
                success=False,
                result=None,
                artifacts=[],
                metadata={"tool": tool_name},
                error=str(e),
            )

    def _build_args(
        self, step: WorkflowStep, context: dict[str, Any]
    ) -> dict[str, Any]:
        """Build tool arguments from step inputs and context.

        Uses the workflow inputs for the first step, and previous step
        results for subsequent steps.
        """
        tool_name = AGENT_TOOL_MAP.get(step.agent, step.agent)
        args: dict[str, Any] = {}

        # Search tools get the query from inputs
        if tool_name in ("search.web", "search.news"):
            args["query"] = self._inputs.get("query", self._inputs.get("topic", ""))
            args["max_results"] = self._inputs.get("max_results", 5)

        # HTTP tools get URL from previous step results
        elif tool_name == "http.get":
            prev_results = context.get("steps", {})
            for step_data in prev_results.values():
                result = step_data.get("result")
                if isinstance(result, list) and result:
                    first = result[0]
                    if isinstance(first, dict) and "url" in first:
                        args["url"] = first["url"]
                        break
            if "url" not in args:
                args["url"] = self._inputs.get("url", "")

        # Google tools get query from inputs
        elif tool_name.startswith("google.gmail"):
            args["query"] = self._inputs.get("query", "")

        elif tool_name.startswith("google.calendar"):
            args["max_results"] = self._inputs.get("max_results", 10)

        elif tool_name.startswith("google.drive"):
            args["query"] = self._inputs.get("query", "")

        # LLM summarize: gather previous results as text
        elif tool_name == "llm.summarize":
            prev_results = context.get("steps", {})
            text_parts = []
            for step_data in prev_results.values():
                result = step_data.get("result")
                if isinstance(result, list):
                    for item in result:
                        if isinstance(item, dict):
                            text_parts.append(
                                f"- {item.get('title', '')}: {item.get('snippet', '')}"
                            )
                elif isinstance(result, str):
                    text_parts.append(result)
            args["text"] = "\n".join(text_parts)

        return args
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_workflow_runner.py -v`
Expected: All 5 tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/mycelos/workflows/runner.py tests/test_workflow_runner.py
git commit -m "feat: add WorkflowRunner — bridges connectors to WorkflowExecutor"
```

---

### Task 2: Confirmation Detection + Pending Task Tracking

The chat REPL needs to know when the user is confirming a previously shown plan. Track the "pending" task+plan after display, then detect confirmation words.

**Files:**
- Create: `tests/test_chat_confirmation.py`
- Modify: `src/mycelos/orchestrator.py`

**Steps:**

- [ ] **Step 1: Write failing test — confirmation detection**

```python
# tests/test_chat_confirmation.py
"""Tests for plan confirmation detection in the orchestrator."""

from __future__ import annotations

import pytest

from mycelos.orchestrator import ChatOrchestrator, Intent, RouteResult, is_plan_confirmation


def test_confirmation_ja():
    assert is_plan_confirmation("Ja") is True

def test_confirmation_yes():
    assert is_plan_confirmation("Yes") is True

def test_confirmation_mach_das():
    assert is_plan_confirmation("Mach das") is True

def test_confirmation_ja_bitte():
    assert is_plan_confirmation("Ja bitte") is True

def test_confirmation_do_it():
    assert is_plan_confirmation("Do it") is True

def test_confirmation_ausfuehren():
    assert is_plan_confirmation("Ausfuehren") is True

def test_not_confirmation_question():
    assert is_plan_confirmation("Was kostet das?") is False

def test_not_confirmation_long_text():
    assert is_plan_confirmation("Ich moechte lieber etwas anderes machen, lass uns nochmal ueberlegen") is False

def test_not_confirmation_nein():
    assert is_plan_confirmation("Nein") is False

def test_not_confirmation_empty():
    assert is_plan_confirmation("") is False

def test_not_confirmation_conditional_yes():
    """'Ja, aber aendere Schritt 2' is NOT a simple confirmation."""
    assert is_plan_confirmation("Ja, aber aendere Schritt 2") is False

def test_not_confirmation_abbrechen():
    assert is_plan_confirmation("Abbrechen") is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_chat_confirmation.py -v`
Expected: ImportError — `cannot import name 'is_plan_confirmation'`

- [ ] **Step 3: Implement is_plan_confirmation in orchestrator**

Add to `src/mycelos/orchestrator.py`:

```python
# Confirmation patterns — user says "yes" to a pending plan
_CONFIRM_PATTERNS = {
    "ja", "yes", "ok", "okay", "sure", "go", "do it", "mach das",
    "ja bitte", "ja mach", "ausfuehren", "starten", "start",
    "los", "run", "run it", "execute", "ja klar", "klar",
    "genau", "passt", "machen", "lgtm",
}


def is_plan_confirmation(text: str) -> bool:
    """Check if the user's message is a confirmation of a pending plan.

    Matches against short affirmative phrases. Long messages or questions
    are not confirmations.
    """
    cleaned = text.strip().lower().rstrip("!. ")
    if not cleaned or len(cleaned) > 40:
        return False
    # Reject if contains hedging/negation words
    _REJECT_WORDS = {"aber", "but", "nicht", "not", "nein", "no", "warte",
                     "wait", "aender", "change", "stopp", "stop", "abbrech", "cancel"}
    if any(w in cleaned for w in _REJECT_WORDS):
        return False
    if cleaned in _CONFIRM_PATTERNS:
        return True
    # Check if starts with a confirm word
    first_word = cleaned.split()[0] if cleaned.split() else ""
    if first_word in {"ja", "yes", "ok", "okay", "sure", "klar"} and len(cleaned.split()) <= 4:
        return True
    return False
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_chat_confirmation.py -v`
Expected: All 12 tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/mycelos/orchestrator.py tests/test_chat_confirmation.py
git commit -m "feat: add plan confirmation detection + workflow_name to RouteResult"
```

---

### Task 3: Wire Execution into Chat REPL

The final piece: when the user confirms a plan, trigger WorkflowRunner, show progress, update task status.

**Files:**
- Modify: `src/mycelos/cli/chat_cmd.py`
- Modify: `src/mycelos/app.py` (add workflow_runner property)

**Steps:**

- [ ] **Step 1: Add WorkflowRunner to App container**

In `src/mycelos/app.py`, add:

```python
from mycelos.workflows.runner import WorkflowRunner
```

And add the property + field:

```python
# In __init__:
self._workflow_runner: WorkflowRunner | None = None

# Property:
@property
def workflow_runner(self) -> WorkflowRunner:
    if self._workflow_runner is None:
        from mycelos.execution.tools import ToolRegistry
        from mycelos.connectors.registry import register_builtin_tools
        registry = ToolRegistry()
        register_builtin_tools(registry, credential_proxy=self._credentials)
        self._workflow_runner = WorkflowRunner(
            tool_registry=registry,
            llm=self.llm,
            evaluator=self.evaluator,
        )
    return self._workflow_runner
```

- [ ] **Step 2: Add pending task state and execution to chat_cmd.py**

Add these variables before the REPL loop:

```python
# Track pending plan awaiting confirmation
pending_task_id: str | None = None
pending_plan: dict | None = None
pending_workflow_name: str | None = None
```

In the REPL loop, BEFORE the orchestrator.route() call, check for confirmation:

```python
from mycelos.orchestrator import is_plan_confirmation

# Check for plan confirmation FIRST
if pending_task_id and is_plan_confirmation(user_input):
    # User confirmed — execute the workflow!
    console.print(f"\n[bold green]Workflow wird ausgefuehrt...[/bold green]")

    app.task_manager.update_status(pending_task_id, "running")

    try:
        # Find or build workflow
        workflow = _resolve_workflow(app, pending_plan, pending_workflow_name)

        if workflow is None:
            error_msg = "Kein passendes Workflow gefunden. Bitte erstelle zuerst einen Workflow."
            console.print(f"[red]{error_msg}[/red]")
            app.task_manager.set_result(pending_task_id, result=error_msg, status="failed")
            conversation.append({"role": "assistant", "content": error_msg})
            app.session_store.append_message(session_id, role="assistant", content=error_msg)
        else:
            # Extract inputs from the original task goal
            inputs = _extract_inputs(pending_plan)

            completed_steps: list[str] = []

            def on_step(step_id: str, result: Any) -> None:
                completed_steps.append(step_id)

            with console.status("[bold green]Workflow laeuft...", spinner="dots"):
                exec_result = app.workflow_runner.execute(
                    workflow, inputs=inputs, on_step_complete=on_step
                )

            # Show step progress after spinner stops
            for sid in completed_steps:
                console.print(f"  [dim]Step '{sid}' done[/dim]")

            if exec_result.success:
                # Format and show results
                result_text = _format_execution_result(exec_result)
                console.print(f"\n[bold magenta]System>[/bold magenta]")
                console.print(Markdown(result_text))
                console.print(f"[dim](Kosten: {exec_result.total_cost:.4f})[/dim]\n")

                app.task_manager.set_result(
                    pending_task_id,
                    result=result_text,
                    cost=exec_result.total_cost,
                    status="completed",
                )
                conversation.append({"role": "assistant", "content": result_text})
                app.session_store.append_message(session_id, role="assistant", content=result_text)
            else:
                error_msg = f"Workflow fehlgeschlagen: {exec_result.error}"
                console.print(f"[red]{error_msg}[/red]\n")
                app.task_manager.set_result(
                    pending_task_id, result=error_msg, cost=exec_result.total_cost, status="failed"
                )
                conversation.append({"role": "assistant", "content": error_msg})
                app.session_store.append_message(session_id, role="assistant", content=error_msg)

    except Exception as exc:
        error_msg = f"Ausfuehrungsfehler: {exc}"
        console.print(f"[red]{error_msg}[/red]\n")
        app.task_manager.set_result(pending_task_id, result=str(exc), status="failed")
        conversation.append({"role": "assistant", "content": error_msg})
        app.session_store.append_message(session_id, role="assistant", content=error_msg)

    # Clear pending state
    pending_task_id = None
    pending_plan = None
    pending_workflow_name = None
    continue
```

After the plan is shown (in the TASK_REQUEST branch), save the pending state:

```python
# After the plan display LLM call, save pending state
if route_result and route_result.intent == Intent.TASK_REQUEST and route_result.plan:
    pending_task_id = route_result.task_id
    pending_plan = route_result.plan
    pending_workflow_name = route_result.plan.get("workflow_id")
    app.task_manager.update_status(route_result.task_id, "awaiting")
```

- [ ] **Step 3: Add helper functions to chat_cmd.py**

```python
def _resolve_workflow(app: App, plan: dict | None, workflow_name: str | None) -> Workflow | None:
    """Find or build a Workflow from the plan and available YAML definitions."""
    from mycelos.workflows.models import Workflow, WorkflowStep

    # Try to load from YAML files
    workflows_dir = app.data_dir / "artifacts" / "workflows"
    if workflow_name and workflows_dir.exists():
        yaml_path = workflows_dir / f"{workflow_name}.yaml"
        if yaml_path.exists():
            return app.workflow_parser.parse_file(yaml_path)

    # Also check the standard location
    if workflows_dir.exists():
        for wf in app.workflow_parser.load_directory(workflows_dir):
            if wf.name == workflow_name:
                return wf

    # Build from plan steps if no YAML match
    if plan and plan.get("steps"):
        steps = []
        for s in plan["steps"]:
            steps.append(WorkflowStep(
                id=s.get("id", f"step-{len(steps)}"),
                action=s.get("action", ""),
                agent=s.get("agent", ""),
                policy="always",
            ))
        return Workflow(name=workflow_name or "ad-hoc", steps=steps)

    return None


def _extract_inputs(plan: dict | None) -> dict[str, Any]:
    """Extract execution inputs from the plan."""
    if not plan:
        return {}
    inputs: dict[str, Any] = {}
    # The plan description often contains the query/topic
    if plan.get("description"):
        inputs["query"] = plan["description"]
    if plan.get("steps"):
        for step in plan["steps"]:
            action = step.get("action", "")
            if "search" in action.lower() or "such" in action.lower():
                # Extract topic from action text
                inputs.setdefault("query", action)
    return inputs


def _format_execution_result(result) -> str:
    """Format ExecutionResult into readable markdown."""
    parts = ["**Ergebnis:**\n"]
    for step_id, output in result.step_results.items():
        parts.append(f"### {step_id}")
        if isinstance(output.result, list):
            for item in output.result:
                if isinstance(item, dict):
                    title = item.get("title", "")
                    url = item.get("url", "")
                    snippet = item.get("snippet", "")
                    parts.append(f"- **{title}**")
                    if url:
                        parts.append(f"  {url}")
                    if snippet:
                        parts.append(f"  {snippet[:200]}")
                else:
                    parts.append(f"- {str(item)[:200]}")
        elif isinstance(output.result, str):
            parts.append(output.result)
        elif output.result is not None:
            parts.append(str(output.result)[:500])
        parts.append("")
    return "\n".join(parts)
```

- [ ] **Step 4: Run existing chat tests to ensure nothing breaks**

Run: `python3 -m pytest tests/test_chat.py -v`
Expected: All existing tests PASS

- [ ] **Step 5: Run full test suite**

Run: `python3 -m pytest -v`
Expected: All tests PASS

- [ ] **Step 6: Commit**

```bash
git add src/mycelos/cli/chat_cmd.py src/mycelos/app.py
git commit -m "feat: wire workflow execution into chat REPL — confirm plan, run, show results"
```

---

### Task 4: Unit Tests for Chat Helper Functions

TDD mandate: every helper function needs tests before wiring into the REPL.

**Files:**
- Create: `tests/test_chat_helpers.py`

**Steps:**

- [ ] **Step 1: Write tests for helper functions**

```python
# tests/test_chat_helpers.py
"""Tests for chat_cmd helper functions."""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from mycelos.cli.chat_cmd import _resolve_workflow, _extract_inputs, _format_execution_result


def test_extract_inputs_from_description():
    plan = {"description": "AI news", "steps": []}
    inputs = _extract_inputs(plan)
    assert inputs["query"] == "AI news"


def test_extract_inputs_from_search_step():
    plan = {"steps": [{"action": "Search for latest AI developments"}]}
    inputs = _extract_inputs(plan)
    assert "query" in inputs


def test_extract_inputs_empty_plan():
    assert _extract_inputs(None) == {}
    assert _extract_inputs({}) == {}


def test_format_execution_result_list():
    """Format list results (search results) into markdown."""
    mock_output = MagicMock()
    mock_output.result = [
        {"title": "Article 1", "url": "https://ex.com/1", "snippet": "First article"},
        {"title": "Article 2", "url": "https://ex.com/2", "snippet": "Second article"},
    ]
    mock_result = MagicMock()
    mock_result.step_results = {"search": mock_output}

    text = _format_execution_result(mock_result)
    assert "Article 1" in text
    assert "Article 2" in text
    assert "https://ex.com/1" in text


def test_format_execution_result_string():
    """Format string results into markdown."""
    mock_output = MagicMock()
    mock_output.result = "Summary of all articles"
    mock_result = MagicMock()
    mock_result.step_results = {"summarize": mock_output}

    text = _format_execution_result(mock_result)
    assert "Summary of all articles" in text


def test_resolve_workflow_from_plan_steps():
    """Build ad-hoc workflow from plan steps when no YAML match."""
    app = MagicMock()
    app.data_dir = Path(tempfile.mkdtemp())

    plan = {
        "steps": [
            {"id": "s1", "agent": "search-agent", "action": "Search"},
            {"id": "s2", "agent": "summary-agent", "action": "Summarize"},
        ]
    }
    wf = _resolve_workflow(app, plan, None)
    assert wf is not None
    assert wf.name == "ad-hoc"
    assert len(wf.steps) == 2
    assert wf.steps[0].id == "s1"


def test_resolve_workflow_returns_none_for_empty():
    """Return None when plan has no steps."""
    app = MagicMock()
    app.data_dir = Path(tempfile.mkdtemp())
    assert _resolve_workflow(app, None, None) is None
    assert _resolve_workflow(app, {}, None) is None
```

- [ ] **Step 2: Run tests to verify they fail (functions not yet extracted)**

Run: `python3 -m pytest tests/test_chat_helpers.py -v`
Expected: ImportError or FAIL

- [ ] **Step 3: Verify tests pass after Task 3 implementation**

Run: `python3 -m pytest tests/test_chat_helpers.py -v`
Expected: All 7 tests PASS

- [ ] **Step 4: Commit**

```bash
git add tests/test_chat_helpers.py
git commit -m "test: add unit tests for chat helper functions"
```

---

### Task 5: Integration Test — Full Flow

End-to-end test with mocked connectors to verify the full chain: route → plan → confirm → execute → result.

**Files:**
- Create: `tests/test_workflow_execution_flow.py`

**Steps:**

- [ ] **Step 1: Write integration test**

```python
# tests/test_workflow_execution_flow.py
"""Integration test: plan confirmation → workflow execution → result."""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from mycelos.app import App
from mycelos.orchestrator import Intent, RouteResult, is_plan_confirmation
from mycelos.workflows.models import Workflow, WorkflowStep
from mycelos.workflows.runner import WorkflowRunner


def test_full_execution_flow():
    """Route → plan → confirm → execute → completed task."""
    with tempfile.TemporaryDirectory() as tmp:
        import os
        os.environ["MYCELOS_MASTER_KEY"] = "test-key-for-execution"

        app = App(Path(tmp))
        app.initialize()

        # 1. Create task (like orchestrator does)
        task_id = app.task_manager.create("Search AI news", user_id="default")
        app.task_manager.update_status(task_id, "planning")

        # 2. Fake plan (like PlannerAgent returns)
        plan = {
            "action": "execute_workflow",
            "workflow_id": None,
            "steps": [
                {"id": "search", "agent": "search-agent", "action": "Search for AI news"},
            ],
            "estimated_cost": "low",
        }

        # 3. Set to awaiting (like chat_cmd does after showing plan)
        app.task_manager.update_status(task_id, "awaiting")

        # 4. User confirms
        assert is_plan_confirmation("Ja, mach das")

        # 5. Execute workflow with mocked search
        app.task_manager.update_status(task_id, "running")

        def fake_search(query, max_results=5):
            return [
                {"title": "AI Breakthrough", "url": "https://example.com/ai", "snippet": "New AI model"},
                {"title": "Tech News", "url": "https://example.com/tech", "snippet": "Latest tech"},
            ]

        from mycelos.execution.tools import ToolRegistry, ToolDefinition
        registry = ToolRegistry()
        registry.register(ToolDefinition(
            name="search.web",
            description="Search web",
            handler=fake_search,
            required_capability="search.web",
        ))

        runner = WorkflowRunner(registry)

        # Build workflow from plan
        wf_steps = [
            WorkflowStep(
                id=s["id"], action=s["action"], agent=s["agent"], policy="always"
            )
            for s in plan["steps"]
        ]
        workflow = Workflow(name="ad-hoc", steps=wf_steps)

        result = runner.execute(workflow, inputs={"query": "AI news"})

        # 6. Verify
        assert result.success
        assert "search" in result.step_results
        search_output = result.step_results["search"]
        assert search_output.success
        assert len(search_output.result) == 2

        # 7. Update task
        app.task_manager.set_result(
            task_id, result="2 articles found", cost=result.total_cost, status="completed"
        )
        task = app.task_manager.get(task_id)
        assert task["status"] == "completed"


def test_confirmation_rejects_negation():
    """Negative responses don't trigger execution."""
    assert not is_plan_confirmation("Nein, lieber nicht")
    assert not is_plan_confirmation("Abbrechen")
    assert not is_plan_confirmation("Warte, ich habe noch eine Frage")
```

- [ ] **Step 2: Run integration test**

Run: `python3 -m pytest tests/test_workflow_execution_flow.py -v`
Expected: All tests PASS

- [ ] **Step 3: Run full test suite**

Run: `python3 -m pytest -v`
Expected: All tests PASS (no regressions)

- [ ] **Step 4: Commit**

```bash
git add tests/test_workflow_execution_flow.py
git commit -m "test: add integration test for full workflow execution flow"
```
