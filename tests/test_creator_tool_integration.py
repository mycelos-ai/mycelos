"""Integration tests for the create_agent tool + Creator pipeline.

Tests the full flow: handoff to creator → create_agent tool → pipeline → result.
Uses mocked LLM but real App, DB, and ChatService.
"""

import json
import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from mycelos.app import App
from mycelos.chat.service import ChatService

# Reuse valid test data from test_creator_integration
VALID_GHERKIN = """\
Feature: Greeting Agent
  Scenario: Generate a greeting
    Given a user name "Stefan"
    When the agent generates a greeting
    Then the greeting should contain "Stefan"
"""

VALID_TESTS = """\
from agent_code import GreetingAgent

def test_generate_greeting():
    agent = GreetingAgent()
    inp = type('Input', (), {'task': 'Stefan', 'context': {}})()
    result = agent.execute(inp)
    assert result.success
    assert 'Stefan' in result.result

def test_handle_empty_name():
    agent = GreetingAgent()
    inp = type('Input', (), {'task': '', 'context': {}})()
    result = agent.execute(inp)
    assert result.success
"""

VALID_CODE = """\
class GreetingAgent:
    agent_id = "greeting-agent"
    agent_type = "deterministic"
    capabilities_required = []

    def execute(self, input):
        name = input.task
        if name:
            greeting = f"Hello, {name}! Welcome to Mycelos."
        else:
            greeting = "Hello! Welcome to Mycelos."
        return type('Result', (), {
            'success': True,
            'result': greeting,
            'artifacts': [],
            'metadata': {},
            'error': '',
        })()
"""

FAILING_CODE = """\
class GreetingAgent:
    agent_id = "greeting-agent"
    def execute(self, input):
        raise RuntimeError("intentional failure")
"""


@pytest.fixture
def app():
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["MYCELOS_MASTER_KEY"] = "test-key-creator-tool"
        a = App(Path(tmp))
        a.initialize()
        yield a


def _mock_llm(*responses: str) -> MagicMock:
    mock = MagicMock()
    mock.total_tokens = 0
    mock.total_cost = 0.0
    # Prepend effort classification response
    all_responses = ("trivial",) + responses
    idx = [0]

    def side_effect(*args, **kwargs):
        i = min(idx[0], len(all_responses) - 1)
        idx[0] += 1
        mock.total_tokens += 100
        r = MagicMock()
        r.content = all_responses[i]
        r.total_tokens = 100
        r.model = "test-model"
        r.tool_calls = None
        return r

    mock.complete.side_effect = side_effect
    return mock


def _approve_audit() -> MagicMock:
    auditor = MagicMock()
    auditor.review_code_and_tests.return_value = {"approved": True, "findings": []}
    return auditor


class TestCreateAgentTool:
    """Integration: create_agent tool dispatched via ChatService."""

    def test_create_agent_tool_in_builder_tools(self, app):
        """Builder handler includes create_agent tool."""
        handlers = app.get_agent_handlers()
        builder = handlers["builder"]
        tool_names = [t["function"]["name"] for t in builder.get_tools()]
        assert "create_agent" in tool_names
        assert "create_workflow" in tool_names

    def test_create_agent_happy_path(self, app):
        """Tool builds spec, runs pipeline, registers agent."""
        app._llm = _mock_llm(VALID_GHERKIN, VALID_TESTS, VALID_CODE)
        app._auditor = _approve_audit()

        svc = ChatService(app)
        result = svc._execute_tool("create_agent", {
            "name": "greeting-agent",
            "description": "Generates personalized greetings",
            "capabilities": ["llm.complete"],
            "input_format": "User name string",
            "output_format": "Greeting text",
        })

        assert result["status"] == "success"
        assert result["agent_id"] == "greeting-agent"
        assert "registered" in result["message"].lower() or "created" in result["message"].lower()

        # Agent should be in DB
        agent = app.agent_registry.get("greeting-agent")
        assert agent is not None

    def test_create_agent_retries_exhausted(self, app):
        """After 3 failures, returns cost and pause status."""
        # All code attempts produce failing code
        app._llm = _mock_llm(VALID_GHERKIN, VALID_TESTS, FAILING_CODE, FAILING_CODE, FAILING_CODE)
        app._auditor = _approve_audit()

        svc = ChatService(app)
        result = svc._execute_tool("create_agent", {
            "name": "failing-agent",
            "description": "An agent that will fail tests",
        })

        assert result["status"] == "retries_exhausted"
        assert "cost_so_far" in result
        assert "ask the user" in result["message"].lower() or "retry" in result["message"].lower()

    def test_create_agent_emits_progress_events(self, app):
        """Progress events are emitted for each pipeline step."""
        app._llm = _mock_llm(VALID_GHERKIN, VALID_TESTS, VALID_CODE)
        app._auditor = _approve_audit()

        svc = ChatService(app)
        svc._execute_tool("create_agent", {
            "name": "progress-agent",
            "description": "Test progress events",
        })

        # _pending_events should have been populated
        assert hasattr(svc, '_pending_events')
        step_ids = [e.data.get("step_id", "") for e in svc._pending_events]
        assert "feasibility" in step_ids
        assert "gherkin" in step_ids
        assert "tests" in step_ids
        assert "register" in step_ids

    def test_create_agent_unrealistic_effort(self, app):
        """Unrealistic agents are rejected immediately."""
        svc = ChatService(app)
        result = svc._execute_tool("create_agent", {
            "name": "os-agent",
            "description": "Rebuild the operating system from scratch",
        })

        assert result["status"] == "failed"
        assert "komplex" in result["message"].lower() or "failed" in result["message"].lower()

    def test_handoff_then_builder_has_create_agent(self, app):
        """After handoff to builder, the session agent changes and tools include create_agent."""
        svc = ChatService(app)
        session_id = svc.create_session()

        svc._execute_handoff(session_id, "builder", "build agent")
        assert svc._get_active_agent(session_id) == "builder"

        handlers = app.get_agent_handlers()
        builder = handlers["builder"]
        tool_names = [t["function"]["name"] for t in builder.get_tools()]
        assert "create_agent" in tool_names
        assert "handoff" in tool_names

    def test_create_agent_large_effort_paused(self, app):
        """Large agents get paused with needs_splitting."""
        svc = ChatService(app)
        result = svc._execute_tool("create_agent", {
            "name": "mega-platform",
            "description": "A complete CRM platform with dashboard and integrations",
            "capabilities": ["http.post", "db.write", "email.send", "slack.post", "github.read", "calendar.write"],
        })

        assert result["status"] == "paused"
        assert result["pause_reason"] == "needs_splitting"
