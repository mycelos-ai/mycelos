"""Security tests for WorkflowAgent — tool scoping, error sanitization, audit logging."""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from mycelos.app import App
from mycelos.tools.registry import ToolRegistry, ToolPermission
from mycelos.workflows.agent import WorkflowAgent, WorkflowAgentResult


@dataclass
class FakeLLMResponse:
    content: str = ""
    total_tokens: int = 50
    model: str = "test-model"
    tool_calls: list[dict] | None = None


@pytest.fixture
def app() -> App:
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["MYCELOS_MASTER_KEY"] = "test-key-workflow-sec"
        a = App(Path(tmp))
        a.initialize()
        yield a


def _make_workflow_def(
    plan: str = "Execute the task.",
    allowed_tools: list[str] | None = None,
    model: str = "haiku",
) -> dict:
    return {
        "plan": plan,
        "model": model,
        "allowed_tools": allowed_tools or [],
    }


class TestWorkflowToolScoping:
    """WorkflowAgent only sees and executes tools in allowed_tools."""

    def test_denied_tool_not_executed(self, app: App) -> None:
        """LLM tries to call a tool not in allowed_tools — denied."""
        mock_llm = MagicMock()
        # Round 1: LLM tries to call search_web (not in allowed_tools)
        mock_llm.complete.side_effect = [
            FakeLLMResponse(
                tool_calls=[{
                    "id": "tc_1",
                    "function": {"name": "search_web", "arguments": '{"query": "hack"}'},
                }],
            ),
            FakeLLMResponse(content="Cannot search, denied."),
        ]
        app._llm = mock_llm

        agent = WorkflowAgent(
            app=app,
            workflow_def=_make_workflow_def(allowed_tools=["http_get"]),
            run_id="sec-1",
        )
        result = agent.execute()

        # Tool should have been denied (error fed back to LLM, not executed)
        tool_msgs = [m for m in result.conversation if m.get("role") == "tool"]
        assert len(tool_msgs) == 1
        tool_content = json.loads(tool_msgs[0]["content"])
        assert "not allowed" in tool_content["error"]

    def test_empty_allowed_tools_blocks_everything(self, app: App) -> None:
        """With empty allowed_tools, no tools are visible."""
        agent = WorkflowAgent(
            app=app,
            workflow_def=_make_workflow_def(allowed_tools=[]),
            run_id="sec-2",
        )
        schemas = agent.get_tool_schemas()
        assert schemas == [], "No tools should be visible with empty allowed_tools"

    def test_wildcard_prefix_works(self, app: App) -> None:
        """Wildcard 'playwright.*' allows all playwright tools but nothing else."""
        mcp_mgr = MagicMock()
        mcp_mgr.list_tools.return_value = [
            {"name": "playwright.navigate", "schema": {"type": "object", "properties": {}}},
            {"name": "playwright.screenshot", "schema": {"type": "object", "properties": {}}},
            {"name": "github.list_issues", "schema": {"type": "object", "properties": {}}},
        ]
        app._mcp_manager = mcp_mgr

        agent = WorkflowAgent(
            app=app,
            workflow_def=_make_workflow_def(allowed_tools=["playwright.*"]),
            run_id="sec-3",
        )
        tool_names = {s["function"]["name"] for s in agent.get_tool_schemas()}
        assert "playwright.navigate" in tool_names
        assert "playwright.screenshot" in tool_names
        assert "github.list_issues" not in tool_names


class TestWorkflowErrorSanitization:
    """Errors are sanitized before being sent to LLM to prevent credential leaks."""

    def test_error_sanitization_removes_api_keys(self, app: App) -> None:
        """Tool errors with API keys get sanitized."""
        from mycelos.security.sanitizer import ResponseSanitizer

        sanitizer = ResponseSanitizer()
        error = "Connection failed with key sk-proj-abc123def456ghi789jklmnop"
        sanitized = sanitizer.sanitize_text(error)
        assert "sk-proj-" not in sanitized
        assert "[REDACTED]" in sanitized

    def test_error_sanitization_removes_bearer_tokens(self, app: App) -> None:
        """Tool errors with Bearer tokens get sanitized."""
        from mycelos.security.sanitizer import ResponseSanitizer

        sanitizer = ResponseSanitizer()
        error = "Auth: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.payload.sig"
        sanitized = sanitizer.sanitize_text(error)
        assert "eyJhbGci" not in sanitized
        assert "[REDACTED]" in sanitized


class TestWorkflowAuditLogging:
    """WorkflowAgent logs audit events for key actions."""

    def test_completed_workflow_is_audited(self, app: App) -> None:
        """Successful workflow completion logs an audit event."""
        mock_llm = MagicMock()
        mock_llm.complete.return_value = FakeLLMResponse(content="All done.")
        app._llm = mock_llm

        agent = WorkflowAgent(
            app=app,
            workflow_def=_make_workflow_def(allowed_tools=[]),
            run_id="sec-audit-1",
        )
        result = agent.execute()
        assert result.status == "completed"

        events = app.storage.fetchall(
            "SELECT * FROM audit_events WHERE event_type = 'workflow.completed'"
        )
        assert len(events) >= 1

    def test_denied_tool_is_audited(self, app: App) -> None:
        """Tool denial in workflow logs an audit event."""
        mock_llm = MagicMock()
        mock_llm.complete.side_effect = [
            FakeLLMResponse(
                tool_calls=[{
                    "id": "tc_1",
                    "function": {"name": "search_web", "arguments": '{"query": "test"}'},
                }],
            ),
            FakeLLMResponse(content="Done."),
        ]
        app._llm = mock_llm

        agent = WorkflowAgent(
            app=app,
            workflow_def=_make_workflow_def(allowed_tools=["http_get"]),
            run_id="sec-audit-2",
        )
        agent.execute()

        events = app.storage.fetchall(
            "SELECT * FROM audit_events WHERE event_type = 'workflow.tool_denied'"
        )
        assert len(events) >= 1
        details = json.loads(events[0]["details"])
        assert details["tool"] == "search_web"

    def test_max_rounds_exceeded_is_audited(self, app: App) -> None:
        """Exceeding max rounds logs an audit event."""
        mock_llm = MagicMock()
        mock_llm.complete.return_value = FakeLLMResponse(
            tool_calls=[{
                "id": "tc_1",
                "function": {"name": "http_get", "arguments": '{"url": "https://loop.com"}'},
            }],
        )
        app._llm = mock_llm

        agent = WorkflowAgent(
            app=app,
            workflow_def=_make_workflow_def(allowed_tools=["http_get"]),
            run_id="sec-audit-3",
            max_rounds=2,
        )

        with patch("mycelos.workflows.agent.ToolRegistry") as MockReg:
            MockReg.execute.return_value = "content"
            MockReg.get_schema.return_value = {
                "type": "function",
                "function": {"name": "http_get", "parameters": {"type": "object", "properties": {}}},
            }
            result = agent.execute()

        assert result.status == "failed"
        events = app.storage.fetchall(
            "SELECT * FROM audit_events WHERE event_type = 'workflow.max_rounds'"
        )
        assert len(events) >= 1
