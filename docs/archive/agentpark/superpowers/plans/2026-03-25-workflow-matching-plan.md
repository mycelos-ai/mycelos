# Workflow Matching — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Planner automatically finds matching workflows from the database. Built-in workflow templates ship with Mycelos. Classifier properly routes notes vs complex requests.

**Architecture:** Update classifier prompt for better routing. Add workflow list to planner context. Seed 3 built-in workflows on init. Add multi-turn workflow execution handler in ChatService.

**Tech Stack:** Existing orchestrator, planner, workflow infrastructure

**Spec:** `docs/superpowers/specs/2026-03-25-workflow-matching-design.md`

---

## File Structure

### New Files
- `src/mycelos/workflows/templates.py` — Built-in workflow definitions
- `tests/test_workflow_matching.py` — Tests

### Modified Files
- `src/mycelos/orchestrator.py` — Classifier prompt update
- `src/mycelos/agents/planner.py` — Workflow list in prompt
- `src/mycelos/agents/planner_context.py` — Include workflows in context
- `src/mycelos/chat/service.py` — Workflow execution handler
- `src/mycelos/app.py` — Seed workflows on initialize

---

## Task 1: Classifier Prompt Update

**Files:**
- Modify: `src/mycelos/orchestrator.py`
- Create: `tests/test_workflow_matching.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_workflow_matching.py
"""Tests for Workflow Matching — classifier, planner, templates."""

import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from mycelos.orchestrator import Intent


class TestClassifierRouting:
    """Verify classifier routes correctly for notes vs workflows."""

    def _classify(self, message: str, expected_intent: str):
        """Helper: mock LLM to return expected classification."""
        from mycelos.orchestrator import ChatOrchestrator
        mock_llm = MagicMock()
        mock_llm.complete.return_value = MagicMock(
            content=f'{{"intent": "{expected_intent}", "confidence": 0.9}}'
        )
        orch = ChatOrchestrator(mock_llm, classifier_model="test")
        result = orch.classify(message)
        return result, mock_llm

    def test_simple_note_is_conversation(self):
        """'Remember X' should be conversation (note_write tool)."""
        result, llm = self._classify("Remember that we chose Python", "conversation")
        assert result == Intent.CONVERSATION

    def test_brainstorm_is_task_request(self):
        """'Collect ideas for X' should be task_request (needs workflow)."""
        result, llm = self._classify("Collect ideas for my project", "task_request")
        assert result == Intent.TASK_REQUEST

    def test_research_is_task_request(self):
        """'Research X and summarize' should be task_request."""
        result, llm = self._classify("Research AI trends and summarize", "task_request")
        assert result == Intent.TASK_REQUEST

    def test_create_agent_still_works(self):
        """'Build an agent' should still be create_agent."""
        result, llm = self._classify("Create an agent that reads PDFs", "create_agent")
        assert result == Intent.CREATE_AGENT

    def test_due_tasks_is_conversation(self):
        """'What tasks are due today?' is conversation (note_list tool)."""
        result, llm = self._classify("What tasks are due today?", "conversation")
        assert result == Intent.CONVERSATION
```

- [ ] **Step 2: Update classifier prompt in orchestrator.py**

Find `_CLASSIFIER_PROMPT` (line 38) and update with better examples:

Add these examples to the existing list:

```python
# Add to _CLASSIFIER_PROMPT examples:
- "Remember that we decided X" → conversation (simple note_write tool)
- "Note: meeting with Alex on Friday" → conversation (simple note_write)
- "What tasks are due today?" → conversation (simple note_list tool)
- "What did we decide about security?" → conversation (simple note_search tool)
- "Collect ideas for my project" → task_request (needs structured workflow)
- "Brainstorm features for X" → task_request (needs structured approach)
- "Research AI trends and summarize" → task_request (multi-step: search + summarize)
- "Give me a daily briefing" → task_request (needs workflow)
- "Analyze my Git repos, who committed most" → task_request (multi-step analysis)
```

- [ ] **Step 3: Run tests**

Run: `pytest tests/test_workflow_matching.py -v`
Expected: All pass

- [ ] **Step 4: Commit**

```bash
git add src/mycelos/orchestrator.py tests/test_workflow_matching.py
git commit -m "feat: Classifier prompt — better routing for notes vs workflows"
```

---

## Task 2: Built-in Workflow Templates + Seed

**Files:**
- Create: `src/mycelos/workflows/templates.py`
- Modify: `src/mycelos/app.py`
- Modify: `tests/test_workflow_matching.py`

- [ ] **Step 1: Write failing tests**

Add to `tests/test_workflow_matching.py`:

```python
class TestBuiltinWorkflows:
    def test_templates_exist(self):
        from mycelos.workflows.templates import BUILTIN_WORKFLOWS
        assert len(BUILTIN_WORKFLOWS) >= 3
        ids = [w["id"] for w in BUILTIN_WORKFLOWS]
        assert "brainstorming-interview" in ids
        assert "research-summary" in ids
        assert "daily-briefing" in ids

    def test_templates_have_required_fields(self):
        from mycelos.workflows.templates import BUILTIN_WORKFLOWS
        for wf in BUILTIN_WORKFLOWS:
            assert "id" in wf
            assert "name" in wf
            assert "description" in wf
            assert "steps" in wf
            assert "tags" in wf
            assert isinstance(wf["steps"], str)  # JSON string

    def test_seed_registers_workflows(self):
        import json
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["MYCELOS_MASTER_KEY"] = "test-key-wf"
            from mycelos.app import App
            app = App(Path(tmp))
            app.initialize()
            # Check that built-in workflows were seeded
            from mycelos.workflows.templates import BUILTIN_WORKFLOWS
            for wf in BUILTIN_WORKFLOWS:
                existing = app.workflow_registry.get(wf["id"])
                assert existing is not None, f"Workflow {wf['id']} not seeded"

    def test_seed_does_not_overwrite_existing(self):
        import json
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["MYCELOS_MASTER_KEY"] = "test-key-wf2"
            from mycelos.app import App
            app = App(Path(tmp))
            app.initialize()
            # Modify a workflow
            app.storage.execute(
                "UPDATE workflows SET description = 'custom' WHERE id = 'brainstorming-interview'"
            )
            # Re-seed (simulate restart)
            from mycelos.workflows.templates import seed_builtin_workflows
            seed_builtin_workflows(app)
            # Should NOT overwrite
            wf = app.workflow_registry.get("brainstorming-interview")
            assert wf["description"] == "custom"
```

- [ ] **Step 2: Create templates.py**

Create `src/mycelos/workflows/templates.py`:

```python
"""Built-in workflow templates that ship with Mycelos."""

from __future__ import annotations

import json
from typing import Any

BUILTIN_WORKFLOWS: list[dict[str, Any]] = [
    {
        "id": "brainstorming-interview",
        "name": "Brainstorming Interview",
        "description": (
            "Structured idea collection through follow-up questions. "
            "Asks about vision, target audience, core features, UI/UX ideas, "
            "technical approach, and open questions. Creates a knowledge base "
            "note with organized results."
        ),
        "goal": "Collect and organize ideas through structured conversation",
        "steps": json.dumps([
            {
                "id": "interview",
                "action": "conversation",
                "description": (
                    "Ask the user structured questions one at a time: "
                    "1. Vision — what problem does this solve? "
                    "2. Target audience — who is this for? "
                    "3. Core features — what are the must-haves? "
                    "4. UI/UX — how should it look/feel? "
                    "5. Technical approach — any constraints? "
                    "6. Open questions — what's unclear? "
                    "Collect all answers."
                ),
            },
            {
                "id": "create_note",
                "action": "note_write",
                "description": (
                    "Create a structured KB note from the interview results. "
                    "Type: note, tags based on topic, priority based on urgency."
                ),
            },
        ]),
        "scope": '["knowledge.write"]',
        "tags": '["builtin", "brainstorming", "ideas", "interview"]',
        "status": "active",
        "created_by": "system",
    },
    {
        "id": "research-summary",
        "name": "Research & Summary",
        "description": (
            "Search the web for a topic, collect key findings from multiple "
            "sources, and create a summarized knowledge base note with sources."
        ),
        "goal": "Research a topic and create a summary note",
        "steps": json.dumps([
            {
                "id": "search",
                "action": "search_web",
                "description": "Search for the topic, collect top results",
            },
            {
                "id": "deep_read",
                "action": "http_get",
                "description": "Read the most relevant pages for details",
            },
            {
                "id": "summarize",
                "action": "note_write",
                "description": (
                    "Create a KB note with findings, organized by subtopic. "
                    "Include source links as references."
                ),
            },
        ]),
        "scope": '["search.web", "http.get", "knowledge.write"]',
        "tags": '["builtin", "research", "summary", "analysis"]',
        "status": "active",
        "created_by": "system",
    },
    {
        "id": "daily-briefing",
        "name": "Daily Briefing",
        "description": (
            "Morning overview: check open and overdue tasks from knowledge base, "
            "optionally search for relevant news, and present as a briefing."
        ),
        "goal": "Give the user a daily status overview",
        "steps": json.dumps([
            {
                "id": "tasks",
                "action": "note_list",
                "description": "Get open and overdue tasks from knowledge base",
            },
            {
                "id": "summarize",
                "action": "conversation",
                "description": (
                    "Present tasks and any news as a concise briefing. "
                    "Highlight overdue items and priorities."
                ),
            },
        ]),
        "scope": '["knowledge.read"]',
        "tags": '["builtin", "briefing", "daily", "tasks"]',
        "status": "active",
        "created_by": "system",
    },
]


def seed_builtin_workflows(app: Any) -> int:
    """Register built-in workflows if they don't exist yet.

    Never overwrites existing workflows (user may have customized them).
    Returns number of newly registered workflows.
    """
    count = 0
    for wf in BUILTIN_WORKFLOWS:
        existing = app.workflow_registry.get(wf["id"])
        if existing is None:
            app.storage.execute(
                """INSERT INTO workflows (id, name, description, goal, steps, scope, tags, status, created_by)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (wf["id"], wf["name"], wf["description"], wf["goal"],
                 wf["steps"], wf["scope"], wf["tags"], wf["status"], wf["created_by"]),
            )
            count += 1
    return count
```

- [ ] **Step 3: Add seed call to App.initialize()**

In `src/mycelos/app.py`, at the end of `initialize()`:

```python
# Seed built-in workflows
from mycelos.workflows.templates import seed_builtin_workflows
seed_builtin_workflows(self)
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_workflow_matching.py -v`
Expected: All pass

- [ ] **Step 5: Commit**

```bash
git add src/mycelos/workflows/templates.py src/mycelos/app.py tests/test_workflow_matching.py
git commit -m "feat: Built-in workflow templates — brainstorming, research, briefing"
```

---

## Task 3: Planner Workflow Context

**Files:**
- Modify: `src/mycelos/agents/planner.py`
- Modify: `src/mycelos/agents/planner_context.py`
- Modify: `tests/test_workflow_matching.py`

- [ ] **Step 1: Write failing tests**

```python
class TestPlannerWorkflowContext:
    def test_planner_context_includes_workflows(self):
        from mycelos.agents.planner_context import build_planner_context
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["MYCELOS_MASTER_KEY"] = "test-key-pc"
            from mycelos.app import App
            app = App(Path(tmp))
            app.initialize()
            context = build_planner_context(app)
            assert "workflows" in context or "available_workflows" in context

    def test_planner_context_has_workflow_descriptions(self):
        from mycelos.agents.planner_context import build_planner_context
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["MYCELOS_MASTER_KEY"] = "test-key-pc2"
            from mycelos.app import App
            app = App(Path(tmp))
            app.initialize()
            context = build_planner_context(app)
            wf_text = context.get("workflows", context.get("available_workflows", ""))
            assert "brainstorming" in str(wf_text).lower()
```

- [ ] **Step 2: Update planner_context.py**

In `src/mycelos/agents/planner_context.py`, add workflow list to the context:

```python
# Add to build_planner_context():
try:
    workflows = app.workflow_registry.list_workflows()
    if workflows:
        wf_lines = []
        for wf in workflows:
            desc = wf.get("description", "")[:100]
            tags = wf.get("tags", "")
            wf_lines.append(f"- {wf['id']}: {desc} (tags: {tags})")
        context["available_workflows"] = "\n".join(wf_lines)
except Exception:
    context["available_workflows"] = ""
```

- [ ] **Step 3: Update planner prompt**

In `src/mycelos/agents/planner.py`, update the planner prompt to include workflow matching instructions. Find the system prompt and add:

```
Before creating a new plan, check if an existing workflow matches the user's request.

Available Workflows:
{available_workflows}

If a workflow matches:
  {{"action": "execute_workflow", "workflow_id": "<id>", "inputs": {{"topic": "..."}}}}

If no workflow matches but you can create a plan:
  {{"action": "execute_workflow", "workflow_id": null, "steps": [...]}}
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_workflow_matching.py -v`
Expected: All pass

- [ ] **Step 5: Commit**

```bash
git add src/mycelos/agents/planner.py src/mycelos/agents/planner_context.py tests/test_workflow_matching.py
git commit -m "feat: Planner receives workflow list for matching"
```

---

## Task 4: CHANGELOG + Final Tests + Push

**Files:**
- Modify: `CHANGELOG.md`

- [ ] **Step 1: Run full test suite**

Run: `pytest tests/ --tb=short -q`
Expected: All pass

- [ ] **Step 2: Update CHANGELOG**

```markdown
### Workflow Matching
- Classifier prompt updated: notes → conversation, complex requests → task_request
- 3 built-in workflow templates: brainstorming-interview, research-summary, daily-briefing
- Workflows seeded on mycelos init (never overwrite user customizations)
- Planner receives available workflows as context for matching
```

- [ ] **Step 3: Commit + push**

```bash
git add CHANGELOG.md
git commit -m "feat: Workflow Matching — classifier, templates, planner context"
git push origin main
```
