"""Tests for WorkflowAgent — LLM-powered workflow execution.

Tests cover:
- LLM loop execution with tool calls
- Tool scoping: only allowed_tools visible to LLM
- MCP tool access scoping
- Clarification pause/resume
- Max rounds safety limit
- Schema fields: plan, model, allowed_tools, conversation, clarification
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

@dataclass
class FakeLLMResponse:
    content: str = ""
    total_tokens: int = 50
    model: str = "test-model"
    tool_calls: list[dict] | None = None


def make_tool_call(name: str, args: dict, call_id: str = "tc_1") -> dict:
    return {
        "id": call_id,
        "function": {"name": name, "arguments": json.dumps(args)},
    }


def fake_app(storage=None):
    """Minimal App mock with storage, audit, llm."""
    app = MagicMock()
    app.audit = MagicMock()
    app._mcp_manager = None
    return app


# ---------------------------------------------------------------------------
# Schema tests
# ---------------------------------------------------------------------------

class TestWorkflowAgentSchema:
    """DB schema supports WorkflowAgent fields."""

    def test_workflows_table_has_plan_column(self, tmp_path):
        from mycelos.storage.database import SQLiteStorage
        db = SQLiteStorage(tmp_path / "test.db")
        db.execute(
            """INSERT INTO workflows (id, name, steps, plan, model, allowed_tools)
               VALUES (?, ?, ?, ?, ?, ?)""",
            ("test-wf", "Test", "[]", "You are a test agent.", "haiku", '["http_get", "note_write"]'),
        )
        row = db.fetchone("SELECT plan, model, allowed_tools FROM workflows WHERE id = ?", ("test-wf",))
        assert row["plan"] == "You are a test agent."
        assert row["model"] == "haiku"
        assert json.loads(row["allowed_tools"]) == ["http_get", "note_write"]

    def test_workflow_runs_has_conversation_and_clarification(self, tmp_path):
        from mycelos.storage.database import SQLiteStorage
        db = SQLiteStorage(tmp_path / "test.db")
        # FK: workflow must exist first
        db.execute(
            "INSERT INTO workflows (id, name, steps) VALUES (?, ?, ?)",
            ("wf-1", "Test", "[]"),
        )
        db.execute(
            """INSERT INTO workflow_runs (id, workflow_id, user_id, conversation, clarification)
               VALUES (?, ?, ?, ?, ?)""",
            ("run-1", "wf-1", "default", '[{"role": "system", "content": "plan"}]', "What source?"),
        )
        row = db.fetchone("SELECT conversation, clarification FROM workflow_runs WHERE id = ?", ("run-1",))
        assert json.loads(row["conversation"])[0]["role"] == "system"
        assert row["clarification"] == "What source?"


# ---------------------------------------------------------------------------
# Tool scoping tests
# ---------------------------------------------------------------------------

class TestToolScoping:
    """WorkflowAgent only sees tools listed in allowed_tools."""

    def test_only_allowed_builtin_tools_visible(self):
        from mycelos.workflows.agent import WorkflowAgent

        app = fake_app()
        agent = WorkflowAgent(
            app=app,
            workflow_def={
                "plan": "Fetch news and save.",
                "model": "haiku",
                "allowed_tools": ["http_get", "note_write"],
            },
            run_id="run-1",
        )
        schemas = agent.get_tool_schemas()
        tool_names = {s["function"]["name"] for s in schemas}
        assert "http_get" in tool_names
        assert "note_write" in tool_names
        assert "search_web" not in tool_names
        assert "create_workflow" not in tool_names

    def test_mcp_tools_included_when_allowed(self):
        from mycelos.workflows.agent import WorkflowAgent

        app = fake_app()
        # Simulate MCP manager with playwright tools
        mcp_mgr = MagicMock()
        mcp_mgr.list_tools.return_value = [
            {"name": "playwright.navigate", "schema": {"type": "object", "properties": {"url": {"type": "string"}}}},
            {"name": "playwright.screenshot", "schema": {"type": "object", "properties": {}}},
            {"name": "github.list_issues", "schema": {"type": "object", "properties": {}}},
        ]
        app._mcp_manager = mcp_mgr

        agent = WorkflowAgent(
            app=app,
            workflow_def={
                "plan": "Take screenshots.",
                "model": "haiku",
                "allowed_tools": ["playwright.navigate", "playwright.screenshot"],
            },
            run_id="run-2",
        )
        schemas = agent.get_tool_schemas()
        tool_names = {s["function"]["name"] for s in schemas}
        assert "playwright.navigate" in tool_names
        assert "playwright.screenshot" in tool_names
        assert "github.list_issues" not in tool_names

    def test_empty_allowed_tools_gives_no_tools(self):
        from mycelos.workflows.agent import WorkflowAgent

        agent = WorkflowAgent(
            app=fake_app(),
            workflow_def={"plan": "Do nothing.", "model": "haiku", "allowed_tools": []},
            run_id="run-3",
        )
        assert agent.get_tool_schemas() == []

    def test_wildcard_prefix_matching(self):
        """'playwright.*' allows all playwright tools."""
        from mycelos.workflows.agent import WorkflowAgent

        app = fake_app()
        mcp_mgr = MagicMock()
        mcp_mgr.list_tools.return_value = [
            {"name": "playwright.navigate", "schema": {"type": "object", "properties": {}}},
            {"name": "playwright.screenshot", "schema": {"type": "object", "properties": {}}},
            {"name": "github.list_issues", "schema": {"type": "object", "properties": {}}},
        ]
        app._mcp_manager = mcp_mgr

        agent = WorkflowAgent(
            app=app,
            workflow_def={
                "plan": "Use playwright.",
                "model": "haiku",
                "allowed_tools": ["playwright.*"],
            },
            run_id="run-4",
        )
        schemas = agent.get_tool_schemas()
        tool_names = {s["function"]["name"] for s in schemas}
        assert "playwright.navigate" in tool_names
        assert "playwright.screenshot" in tool_names
        assert "github.list_issues" not in tool_names


# ---------------------------------------------------------------------------
# LLM loop tests
# ---------------------------------------------------------------------------

class TestLLMLoop:
    """WorkflowAgent executes LLM loop: plan → tool calls → done."""

    def test_simple_text_response_completes(self):
        """LLM returns text (no tool calls) → workflow completes."""
        from mycelos.workflows.agent import WorkflowAgent

        app = fake_app()
        mock_llm = MagicMock()
        mock_llm.complete.return_value = FakeLLMResponse(
            content="Done. Summary: 5 top stories saved."
        )
        app.llm = mock_llm

        agent = WorkflowAgent(
            app=app,
            workflow_def={
                "plan": "Summarize news.",
                "model": "haiku",
                "allowed_tools": [],
            },
            run_id="run-5",
        )
        result = agent.execute()
        assert result.status == "completed"
        assert "5 top stories" in result.result

    def test_tool_call_then_done(self):
        """LLM calls a tool, gets result, then responds with text → done."""
        from mycelos.workflows.agent import WorkflowAgent

        app = fake_app()
        mock_llm = MagicMock()

        # Round 1: LLM wants to call http_get
        resp1 = FakeLLMResponse(
            tool_calls=[make_tool_call("http_get", {"url": "https://heise.de", "format": "markdown"})],
        )
        # Round 2: LLM returns final text
        resp2 = FakeLLMResponse(content="Fetched heise.de. Summary complete.")

        mock_llm.complete.side_effect = [resp1, resp2]
        app.llm = mock_llm

        agent = WorkflowAgent(
            app=app,
            workflow_def={
                "plan": "Fetch heise.de and summarize.",
                "model": "haiku",
                "allowed_tools": ["http_get"],
            },
            run_id="run-6",
        )

        # Mock ToolRegistry.execute
        with patch("mycelos.workflows.agent.ToolRegistry") as MockReg:
            MockReg.execute.return_value = "# Heise.de\nTop story: AI advances"
            MockReg.get_schema.return_value = {
                "type": "function",
                "function": {
                    "name": "http_get",
                    "parameters": {"type": "object", "properties": {"url": {"type": "string"}}},
                },
            }
            result = agent.execute()

        assert result.status == "completed"
        assert mock_llm.complete.call_count == 2
        # Verify tool was called
        MockReg.execute.assert_called_once()

    def test_max_rounds_exceeded(self):
        """LLM keeps calling tools beyond max_rounds → fails."""
        from mycelos.workflows.agent import WorkflowAgent

        app = fake_app()
        mock_llm = MagicMock()
        # Always return a tool call
        mock_llm.complete.return_value = FakeLLMResponse(
            tool_calls=[make_tool_call("http_get", {"url": "https://loop.com"})],
        )
        app.llm = mock_llm

        agent = WorkflowAgent(
            app=app,
            workflow_def={
                "plan": "Infinite loop test.",
                "model": "haiku",
                "allowed_tools": ["http_get"],
            },
            run_id="run-7",
            max_rounds=3,
        )

        with patch("mycelos.workflows.agent.ToolRegistry") as MockReg:
            MockReg.execute.return_value = "page content"
            MockReg.get_schema.return_value = {
                "type": "function",
                "function": {
                    "name": "http_get",
                    "parameters": {"type": "object", "properties": {}},
                },
            }
            result = agent.execute()

        assert result.status == "failed"
        assert "max rounds" in result.error.lower()

    def test_denied_tool_not_executed(self):
        """LLM tries to call a tool not in allowed_tools → error fed back."""
        from mycelos.workflows.agent import WorkflowAgent

        app = fake_app()
        mock_llm = MagicMock()

        # Round 1: LLM tries to call search_web (not allowed)
        resp1 = FakeLLMResponse(
            tool_calls=[make_tool_call("search_web", {"query": "hack"})],
        )
        # Round 2: LLM gives up
        resp2 = FakeLLMResponse(content="Cannot search, finishing.")
        mock_llm.complete.side_effect = [resp1, resp2]
        app.llm = mock_llm

        agent = WorkflowAgent(
            app=app,
            workflow_def={
                "plan": "Only fetch.",
                "model": "haiku",
                "allowed_tools": ["http_get"],
            },
            run_id="run-8",
        )

        with patch("mycelos.workflows.agent.ToolRegistry") as MockReg:
            MockReg.get_schema.return_value = None  # not in allowed set anyway
            result = agent.execute()

        assert result.status == "completed"
        # search_web should NOT have been executed
        MockReg.execute.assert_not_called()


# ---------------------------------------------------------------------------
# Clarification / pause-resume tests
# ---------------------------------------------------------------------------

class TestClarification:
    """WorkflowAgent can pause for user input and resume."""

    def test_needs_clarification_pauses(self):
        """LLM asks a question → status becomes needs_clarification."""
        from mycelos.workflows.agent import WorkflowAgent

        app = fake_app()
        mock_llm = MagicMock()
        mock_llm.complete.return_value = FakeLLMResponse(
            content="NEEDS_CLARIFICATION: Heise and Spiegel disagree. Which source should I trust?"
        )
        app.llm = mock_llm

        agent = WorkflowAgent(
            app=app,
            workflow_def={
                "plan": "If conflicting, ask user.",
                "model": "haiku",
                "allowed_tools": [],
            },
            run_id="run-9",
        )
        result = agent.execute()
        assert result.status == "needs_clarification"
        assert "Which source" in result.clarification

    def test_resume_after_clarification(self):
        """Resume with user answer → continues LLM loop → completes."""
        from mycelos.workflows.agent import WorkflowAgent

        app = fake_app()
        mock_llm = MagicMock()

        # First execute: asks for clarification
        resp1 = FakeLLMResponse(
            content="NEEDS_CLARIFICATION: Which source?"
        )
        # After resume: completes
        resp2 = FakeLLMResponse(content="Using Heise. Summary done.")
        mock_llm.complete.side_effect = [resp1, resp2]
        app.llm = mock_llm

        agent = WorkflowAgent(
            app=app,
            workflow_def={
                "plan": "Ask if unsure.",
                "model": "haiku",
                "allowed_tools": [],
            },
            run_id="run-10",
        )

        # First run: pauses
        result1 = agent.execute()
        assert result1.status == "needs_clarification"

        # Resume with answer
        result2 = agent.resume("Trust Heise for tech news.")
        assert result2.status == "completed"
        assert "Heise" in result2.result


# ---------------------------------------------------------------------------
# Model selection tests
# ---------------------------------------------------------------------------

class TestModelSelection:
    """WorkflowAgent passes the workflow-defined model to LLM."""

    def test_model_passed_to_llm(self):
        from mycelos.workflows.agent import WorkflowAgent

        app = fake_app()
        mock_llm = MagicMock()
        mock_llm.complete.return_value = FakeLLMResponse(content="Done.")
        app.llm = mock_llm

        agent = WorkflowAgent(
            app=app,
            workflow_def={
                "plan": "Simple task.",
                "model": "sonnet",
                "allowed_tools": [],
            },
            run_id="run-11",
        )
        agent.execute()

        # Check that complete was called with a model (resolved from "sonnet")
        call_kwargs = mock_llm.complete.call_args
        model_used = call_kwargs.kwargs.get("model")
        assert model_used is not None, "Model should be passed to complete()"


# ---------------------------------------------------------------------------
# Conversation tracking tests
# ---------------------------------------------------------------------------

class TestConversationTracking:
    """WorkflowAgent tracks full conversation for pause/resume."""

    def test_conversation_includes_system_and_tool_results(self):
        from mycelos.workflows.agent import WorkflowAgent

        app = fake_app()
        mock_llm = MagicMock()
        resp1 = FakeLLMResponse(
            tool_calls=[make_tool_call("http_get", {"url": "https://test.com"})],
        )
        resp2 = FakeLLMResponse(content="All done.")
        mock_llm.complete.side_effect = [resp1, resp2]
        app.llm = mock_llm

        agent = WorkflowAgent(
            app=app,
            workflow_def={
                "plan": "Fetch and report.",
                "model": "haiku",
                "allowed_tools": ["http_get"],
            },
            run_id="run-12",
        )

        with patch("mycelos.workflows.agent.ToolRegistry") as MockReg:
            MockReg.execute.return_value = "page content"
            MockReg.get_schema.return_value = {
                "type": "function",
                "function": {
                    "name": "http_get",
                    "parameters": {"type": "object", "properties": {}},
                },
            }
            result = agent.execute()

        # Conversation should have: system, assistant (tool call), tool result, assistant (done)
        conv = result.conversation
        assert conv[0]["role"] == "system"
        assert any(m.get("role") == "tool" for m in conv)
        assert conv[-1]["role"] == "assistant"


# ---------------------------------------------------------------------------
# WorkflowRegistry integration
# ---------------------------------------------------------------------------

class TestWorkflowRegistryAgentFields:
    """WorkflowRegistry stores and retrieves plan/model/allowed_tools."""

    def test_register_with_agent_fields(self, tmp_path):
        from mycelos.storage.database import SQLiteStorage
        from mycelos.workflows.workflow_registry import WorkflowRegistry

        db = SQLiteStorage(tmp_path / "test.db")
        reg = WorkflowRegistry(db)

        reg.register(
            workflow_id="news-summary",
            name="Daily News",
            steps=[],
            plan="You are the news agent. Fetch and summarize.",
            model="haiku",
            allowed_tools=["http_get", "note_write", "playwright.*"],
        )

        wf = reg.get("news-summary")
        assert wf["plan"] == "You are the news agent. Fetch and summarize."
        assert wf["model"] == "haiku"
        assert wf["allowed_tools"] == ["http_get", "note_write", "playwright.*"]

    def test_update_agent_fields(self, tmp_path):
        from mycelos.storage.database import SQLiteStorage
        from mycelos.workflows.workflow_registry import WorkflowRegistry

        db = SQLiteStorage(tmp_path / "test.db")
        reg = WorkflowRegistry(db)

        reg.register(
            workflow_id="wf-1",
            name="Test",
            steps=[],
            plan="Original plan.",
            model="haiku",
            allowed_tools=["http_get"],
        )

        reg.update(
            "wf-1",
            plan="Updated plan with more steps.",
            model="sonnet",
            allowed_tools=["http_get", "search_web", "playwright.*"],
        )

        wf = reg.get("wf-1")
        assert wf["plan"] == "Updated plan with more steps."
        assert wf["model"] == "sonnet"
        assert "playwright.*" in wf["allowed_tools"]
        assert wf["version"] == 2


class TestWorkflowDefValidation:
    """WorkflowAgent must gracefully handle incomplete/legacy workflow rows.

    Early workflow rows were registered without plan/model/allowed_tools.
    When the chat handler loads one of those rows and hands it to
    WorkflowAgent, the agent MUST fail fast with a clear ValueError —
    never crash downstream with a cryptic TypeError.
    """

    def test_missing_plan_raises_valueerror(self):
        from mycelos.workflows.agent import WorkflowAgent
        with pytest.raises(ValueError, match="non-empty 'plan'"):
            WorkflowAgent(
                app=fake_app(),
                workflow_def={"model": "haiku", "allowed_tools": []},
                run_id="run-missing-plan",
            )

    def test_null_plan_raises_valueerror(self):
        from mycelos.workflows.agent import WorkflowAgent
        with pytest.raises(ValueError, match="non-empty 'plan'"):
            WorkflowAgent(
                app=fake_app(),
                workflow_def={"plan": None, "model": "haiku", "allowed_tools": []},
                run_id="run-null-plan",
            )

    def test_empty_plan_raises_valueerror(self):
        from mycelos.workflows.agent import WorkflowAgent
        with pytest.raises(ValueError, match="non-empty 'plan'"):
            WorkflowAgent(
                app=fake_app(),
                workflow_def={"plan": "   ", "model": "haiku", "allowed_tools": []},
                run_id="run-empty-plan",
            )

    def test_null_model_does_not_crash(self):
        """A workflow row with NULL model column must not crash constructor.

        Regression for: TypeError: argument of type 'NoneType' is not iterable
        thrown from `"/" in model_name` in _resolve_model.
        """
        from mycelos.workflows.agent import WorkflowAgent
        # Should not raise
        WorkflowAgent(
            app=fake_app(),
            workflow_def={"plan": "Do a thing.", "model": None, "allowed_tools": []},
            run_id="run-null-model",
        )

    def test_null_allowed_tools_becomes_empty_list(self):
        from mycelos.workflows.agent import WorkflowAgent
        agent = WorkflowAgent(
            app=fake_app(),
            workflow_def={"plan": "Do a thing.", "model": "haiku", "allowed_tools": None},
            run_id="run-null-tools",
        )
        assert agent.allowed_tools == []

    def test_build_system_prompt_with_valid_plan_only(self):
        """_build_system_prompt must never produce a sequence with None items."""
        from mycelos.workflows.agent import WorkflowAgent
        agent = WorkflowAgent(
            app=fake_app(),
            workflow_def={"plan": "Do the thing.", "model": "haiku", "allowed_tools": []},
            run_id="run-prompt",
        )
        prompt = agent._build_system_prompt()
        assert isinstance(prompt, str)
        assert "Do the thing." in prompt


class TestOffTopicInterruptContract:
    """The WorkflowAgent system prompt must instruct the LLM to handle
    off-topic messages — either a brief answer + continue, or an explicit
    pause offer. This is verified by checking the prompt text contains the
    documented guidance; behavior at runtime is the LLM's responsibility."""

    def test_system_prompt_mentions_off_topic_handling(self):
        from mycelos.workflows.agent import WorkflowAgent
        agent = WorkflowAgent(
            app=fake_app(),
            workflow_def={
                "plan": "Collect brainstorming ideas.",
                "model": "haiku",
                "allowed_tools": [],
            },
            run_id="run-interrupt",
        )
        prompt = agent._build_system_prompt()
        assert "off-topic" in prompt.lower() or "off topic" in prompt.lower()
        assert "pause" in prompt.lower()
