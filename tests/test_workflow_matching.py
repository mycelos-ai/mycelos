"""Tests for Workflow Matching — classifier routing."""

import pytest
from mycelos.orchestrator import Intent, ChatOrchestrator
from unittest.mock import MagicMock


class TestClassifierRouting:
    def _make_orchestrator(self, intent_response: str):
        mock_llm = MagicMock()
        mock_llm.complete.return_value = MagicMock(
            content=f'{{"intent": "{intent_response}", "confidence": 0.9}}'
        )
        return ChatOrchestrator(mock_llm, classifier_model="test")

    def test_simple_note_routes_conversation(self):
        orch = self._make_orchestrator("conversation")
        assert orch.classify("Remember that we chose Python") == Intent.CONVERSATION

    def test_brainstorm_routes_task_request(self):
        orch = self._make_orchestrator("task_request")
        assert orch.classify("Collect ideas for my project") == Intent.TASK_REQUEST

    def test_research_routes_task_request(self):
        orch = self._make_orchestrator("task_request")
        assert orch.classify("Research AI trends and summarize") == Intent.TASK_REQUEST

    def test_create_agent_still_works(self):
        orch = self._make_orchestrator("create_agent")
        assert orch.classify("Create an agent that reads PDFs") == Intent.CREATE_AGENT

    def test_task_list_is_conversation(self):
        orch = self._make_orchestrator("conversation")
        assert orch.classify("What tasks are due today?") == Intent.CONVERSATION

    def test_classifier_prompt_has_note_examples(self):
        from mycelos.orchestrator import _CLASSIFIER_PROMPT
        assert "note_write" in _CLASSIFIER_PROMPT
        assert "note_list" in _CLASSIFIER_PROMPT
        assert "note_search" in _CLASSIFIER_PROMPT

    def test_classifier_prompt_has_workflow_examples(self):
        from mycelos.orchestrator import _CLASSIFIER_PROMPT
        assert "Brainstorm" in _CLASSIFIER_PROMPT or "brainstorm" in _CLASSIFIER_PROMPT
        assert "Research" in _CLASSIFIER_PROMPT or "research" in _CLASSIFIER_PROMPT


import os
import tempfile
from pathlib import Path


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

    def test_every_builtin_has_plan_model_and_tools(self):
        """Every shipped workflow must have the fields WorkflowAgent requires,
        otherwise /run <workflow_id> crashes at runtime. This is the regression
        test for the 'brainstorming-interview has no plan' bug."""
        from mycelos.workflows.templates import BUILTIN_WORKFLOWS
        for wf in BUILTIN_WORKFLOWS:
            assert wf.get("plan"), f"Workflow {wf['id']} missing 'plan'"
            assert isinstance(wf["plan"], str) and len(wf["plan"]) > 50, \
                f"Workflow {wf['id']} plan is too short to be meaningful"
            assert wf.get("model"), f"Workflow {wf['id']} missing 'model'"
            assert wf.get("allowed_tools"), f"Workflow {wf['id']} missing 'allowed_tools'"

    def test_every_builtin_is_workflow_agent_compatible(self):
        """Every builtin workflow can be loaded into a WorkflowAgent without
        raising. Catches the second class of the 'legacy row' bug — a row
        that seeds OK but crashes when actually used."""
        from mycelos.workflows.templates import BUILTIN_WORKFLOWS
        from mycelos.workflows.agent import WorkflowAgent
        from unittest.mock import MagicMock
        import json as _json

        mock_app = MagicMock()
        mock_app.storage = MagicMock()
        mock_app.mcp_manager = None
        mock_app.model_registry.resolve_models.return_value = ["anthropic/claude-haiku-4-5"]

        for wf in BUILTIN_WORKFLOWS:
            # Convert the JSON-string allowed_tools (DB format) to a list
            # (runtime format) the way workflow_registry.get() would.
            runtime_def = dict(wf)
            if isinstance(runtime_def.get("allowed_tools"), str):
                runtime_def["allowed_tools"] = _json.loads(runtime_def["allowed_tools"])
            agent = WorkflowAgent(app=mock_app, workflow_def=runtime_def, run_id=f"test-{wf['id']}")
            prompt = agent._build_system_prompt()
            assert isinstance(prompt, str) and wf["plan"][:20] in prompt

    def test_seed_registers_workflows(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["MYCELOS_MASTER_KEY"] = "test-key-wf-seed"
            from mycelos.app import App
            app = App(Path(tmp))
            app.initialize()
            from mycelos.workflows.templates import BUILTIN_WORKFLOWS
            for wf in BUILTIN_WORKFLOWS:
                existing = app.workflow_registry.get(wf["id"])
                assert existing is not None, f"Workflow {wf['id']} not seeded"

    def test_seed_resyncs_system_owned_workflows(self):
        """System-owned builtin workflows are re-synced on each seed so that
        plan/steps fixes propagate to existing installs without a manual
        rollback."""
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["MYCELOS_MASTER_KEY"] = "test-key-wf-resync"
            from mycelos.app import App
            app = App(Path(tmp))
            app.initialize()
            # Simulate a drifted install (local description override)
            app.storage.execute(
                "UPDATE workflows SET description = 'custom' WHERE id = 'brainstorming-interview'"
            )
            from mycelos.workflows.templates import seed_builtin_workflows, BUILTIN_WORKFLOWS
            seed_builtin_workflows(app)
            wf = app.workflow_registry.get("brainstorming-interview")
            expected = next(w for w in BUILTIN_WORKFLOWS if w["id"] == "brainstorming-interview")
            assert wf["description"] == expected["description"]


class TestPlannerWorkflowContext:
    def test_planner_context_includes_workflows(self):
        from mycelos.agents.planner_context import build_planner_context
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["MYCELOS_MASTER_KEY"] = "test-key-pc"
            from mycelos.app import App
            app = App(Path(tmp))
            app.initialize()
            context = build_planner_context(app)
            assert "available_workflows" in context
            workflows = context["available_workflows"]
            assert len(workflows) >= 3

    def test_planner_context_formatted_has_brainstorming(self):
        from mycelos.agents.planner_context import build_planner_context, format_context_for_prompt
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["MYCELOS_MASTER_KEY"] = "test-key-pc2"
            from mycelos.app import App
            app = App(Path(tmp))
            app.initialize()
            context = build_planner_context(app)
            formatted = format_context_for_prompt(context)
            assert "brainstorming" in formatted.lower()
            assert "research" in formatted.lower()

    def test_planner_prompt_mentions_matching(self):
        from mycelos.prompts import PromptLoader
        prompt = PromptLoader().load("planner", system_context="")
        assert "workflow descriptions" in prompt.lower() or "match by intent" in prompt.lower()
