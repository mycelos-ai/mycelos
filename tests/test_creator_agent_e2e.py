"""End-to-end integration tests for the Creator Agent flow.

Tests the full path: user message -> handoff to Builder -> create_agent tool
-> CreatorPipeline -> agent registered. All LLM calls are mocked.
"""

import json
import os
import tempfile
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from mycelos.app import App
from mycelos.chat.service import ChatService


# ---------------------------------------------------------------------------
# Shared test data (Gherkin, tests, code) for the pipeline
# ---------------------------------------------------------------------------

VALID_GHERKIN = """\
Feature: PDF Text Extractor
  Scenario: Extract text from a simple PDF
    Given a PDF file with text content
    When the agent extracts text
    Then the result should contain the text content
"""

VALID_TESTS = """\
from agent_code import PdfTextExtractor

def test_extract_text():
    agent = PdfTextExtractor()
    inp = type('Input', (), {'task': 'sample.pdf', 'context': {}})()
    result = agent.execute(inp)
    assert result.success
    assert len(result.result) > 0

def test_handle_empty_input():
    agent = PdfTextExtractor()
    inp = type('Input', (), {'task': '', 'context': {}})()
    result = agent.execute(inp)
    assert result.success
"""

VALID_CODE = """\
class PdfTextExtractor:
    agent_id = "pdf-text-extractor"
    agent_type = "deterministic"
    capabilities_required = ["filesystem.read"]

    def execute(self, input):
        path = input.task
        if not path:
            text = ""
        else:
            text = f"Extracted text from {path}"
        return type('Result', (), {
            'success': True,
            'result': text,
            'artifacts': [],
            'metadata': {},
            'error': '',
        })()
"""


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def app():
    """Create a fresh App with temp directory and initialized DB."""
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["MYCELOS_MASTER_KEY"] = "test-key-creator-e2e"
        a = App(Path(tmp))
        a.initialize()
        yield a


# ---------------------------------------------------------------------------
# Helpers: mock LLM responses
# ---------------------------------------------------------------------------

def _make_llm_response(
    content: str = "",
    tool_calls: list[dict] | None = None,
) -> MagicMock:
    """Build a mock LLM response object."""
    resp = MagicMock()
    resp.content = content
    resp.total_tokens = 150
    resp.model = "test-model"
    resp.cost = 0.001
    resp.tool_calls = tool_calls
    return resp


def _make_tool_call(
    name: str,
    arguments: dict,
    call_id: str = "call_001",
) -> dict:
    """Build a tool_call dict in OpenAI format."""
    return {
        "id": call_id,
        "type": "function",
        "function": {
            "name": name,
            "arguments": json.dumps(arguments),
        },
    }


def _mock_llm_sequence(*responses: MagicMock) -> MagicMock:
    """Create a mock LLM whose .complete() returns responses in sequence."""
    mock = MagicMock()
    mock.total_tokens = 0
    mock.total_cost = 0.0
    idx = [0]

    def side_effect(*args: Any, **kwargs: Any) -> MagicMock:
        i = min(idx[0], len(responses) - 1)
        idx[0] += 1
        mock.total_tokens += responses[i].total_tokens
        return responses[i]

    mock.complete.side_effect = side_effect
    return mock


def _approve_auditor() -> MagicMock:
    """Create a mock auditor that approves everything."""
    auditor = MagicMock()
    auditor.review_code_and_tests.return_value = {"approved": True, "findings": []}
    return auditor


# ---------------------------------------------------------------------------
# Test 1: Handoff to Builder on agent-creation request
# ---------------------------------------------------------------------------

class TestHandoffToBuilder:
    """Verify that Mycelos hands off to Builder when the user wants to build an agent."""

    def test_handoff_to_builder_on_agent_request(self, app: App) -> None:
        """User asks to build an agent -> LLM calls handoff -> Builder becomes active.

        Flow:
        1. User sends "Build me a PDF text extractor agent"
        2. Mycelos LLM returns a handoff tool call targeting "builder"
        3. Builder LLM responds with a clarifying question (text, no tool calls)
        4. We verify: active agent is "builder", response contains builder attribution
        """
        svc = ChatService(app)
        session_id = svc.create_session()

        # Response 1 (Mycelos): call handoff to builder
        handoff_response = _make_llm_response(
            content="Let me transfer you to the Builder.",
            tool_calls=[_make_tool_call(
                "handoff",
                {"target_agent": "builder", "reason": "User wants to build a PDF agent"},
                call_id="call_handoff_01",
            )],
        )

        # Response 2 (Builder, after handoff): clarifying question (no tool calls)
        builder_response = _make_llm_response(
            content="I can help build that! What kind of PDFs will you be extracting text from?",
        )

        app._llm = _mock_llm_sequence(handoff_response, builder_response)

        events = svc.handle_message(
            "Build me an agent that can extract text from PDF files",
            session_id=session_id,
        )

        # Verify: Builder is now the active agent
        active = svc._get_active_agent(session_id)
        assert active == "builder", f"Expected builder, got {active}"

        # Verify: response events contain builder agent attribution
        agent_events = [e for e in events if e.type == "agent"]
        agent_names = [e.data.get("agent", "") for e in agent_events]
        assert any("Builder" in name for name in agent_names), (
            f"Expected Builder-Agent attribution in events, got: {agent_names}"
        )

        # Verify: the final text response is from the builder
        text_events = [e for e in events if e.type == "text"]
        assert any("help build" in e.data.get("content", "").lower() or
                    "pdf" in e.data.get("content", "").lower()
                    for e in text_events), (
            f"Expected builder's clarifying question in text events"
        )

        # Verify: audit logged the handoff
        rows = app.storage.fetchall(
            "SELECT event_type, details FROM audit_events WHERE event_type = 'agent.handoff'"
        )
        assert len(rows) >= 1
        details = json.loads(rows[-1]["details"]) if isinstance(rows[-1]["details"], str) else rows[-1]["details"]
        assert details["to"] == "builder"


# ---------------------------------------------------------------------------
# Test 2: Builder creates agent via pipeline
# ---------------------------------------------------------------------------

class TestBuilderCreatesAgent:
    """Verify that the Builder's create_agent tool triggers the pipeline and registers the agent."""

    def test_builder_creates_agent_via_pipeline(self, app: App) -> None:
        """Builder calls create_agent -> pipeline runs -> agent registered in DB.

        We call _execute_tool directly (same pattern as test_creator_tool_integration.py)
        to isolate the pipeline execution from the LLM conversation loop.

        Pipeline LLM calls (in order):
        1. Effort classification -> "trivial"
        2. Gherkin generation -> VALID_GHERKIN
        3. Test generation -> VALID_TESTS
        4. Code generation -> VALID_CODE
        """
        # Mock the pipeline LLM: effort check + gherkin + tests + code
        app._llm = _mock_pipeline_llm()
        app._auditor = _approve_auditor()

        svc = ChatService(app)
        result = svc._execute_tool("create_agent", {
            "name": "pdf-text-extractor",
            "description": "Extracts text content from PDF files",
            "capabilities": ["filesystem.read"],
            "input_format": "Path to a PDF file",
            "output_format": "Extracted text string",
        })

        # Verify: pipeline succeeded
        assert result["status"] == "success", f"Expected success, got: {result}"
        assert result["agent_id"] == "pdf-text-extractor"

        # Verify: agent is registered in DB
        agent = app.agent_registry.get("pdf-text-extractor")
        assert agent is not None, "Agent should be registered after pipeline success"

        # Verify: progress events were emitted
        assert hasattr(svc, '_pending_events')
        step_ids = [e.data.get("step_id", "") for e in svc._pending_events]
        assert "feasibility" in step_ids, f"Missing feasibility step in: {step_ids}"
        assert "gherkin" in step_ids, f"Missing gherkin step in: {step_ids}"
        assert "tests" in step_ids, f"Missing tests step in: {step_ids}"
        assert "register" in step_ids, f"Missing register step in: {step_ids}"

        # Verify: audit logged agent creation
        rows = app.storage.fetchall(
            "SELECT event_type FROM audit_events WHERE event_type LIKE 'agent.%'"
        )
        event_types = [r["event_type"] for r in rows]
        assert any("agent" in et for et in event_types), (
            f"Expected agent audit events, got: {event_types}"
        )


# ---------------------------------------------------------------------------
# Test 3: Full flow — user message -> handoff -> builder -> create_agent -> result
# ---------------------------------------------------------------------------

class TestCreatorFullFlow:
    """Full end-to-end: message -> handoff -> builder calls create_agent -> pipeline -> result."""

    def test_creator_full_flow(self, app: App) -> None:
        """Complete flow mimicking the browser test observation.

        Sequence:
        1. User: "Build me a PDF text extractor agent"
        2. Mycelos LLM -> handoff tool call to builder
        3. Builder LLM -> create_agent tool call (skips interview for simplicity)
        4. Pipeline runs (mocked LLM: effort + gherkin + tests + code)
        5. Pipeline returns success
        6. Builder LLM -> text response announcing success (no more tool calls)

        The pipeline LLM calls happen inside _execute_tool -> CreatorPipeline.run(),
        so we need the mock to handle BOTH the conversation-level calls AND the
        pipeline-level calls in the right order.
        """
        app._auditor = _approve_auditor()

        svc = ChatService(app)
        session_id = svc.create_session()

        # Build the sequence of LLM responses:
        # 1. Mycelos: handoff to builder
        resp_handoff = _make_llm_response(
            content="Transferring to Builder-Agent.",
            tool_calls=[_make_tool_call(
                "handoff",
                {"target_agent": "builder", "reason": "User wants a PDF extractor agent"},
                call_id="call_h1",
            )],
        )

        # 2. Builder: call create_agent (after receiving handoff context)
        resp_builder_create = _make_llm_response(
            content="I'll create that agent for you now.",
            tool_calls=[_make_tool_call(
                "create_agent",
                {
                    "name": "pdf-text-extractor",
                    "description": "Extracts text content from PDF files",
                    "capabilities": ["filesystem.read"],
                },
                call_id="call_ca1",
            )],
        )

        # 3-6. Pipeline LLM calls (effort + gherkin + tests + code)
        resp_effort = _make_llm_response(content="trivial")
        resp_gherkin = _make_llm_response(content=VALID_GHERKIN)
        resp_tests = _make_llm_response(content=VALID_TESTS)
        resp_code = _make_llm_response(content=VALID_CODE)

        # 7. Builder: final text response after create_agent returns
        resp_builder_done = _make_llm_response(
            content="Your PDF Text Extractor agent has been created and registered successfully!",
        )

        app._llm = _mock_llm_sequence(
            resp_handoff,         # Mycelos -> handoff
            resp_builder_create,  # Builder -> create_agent
            resp_effort,          # Pipeline: effort classification
            resp_gherkin,         # Pipeline: Gherkin generation
            resp_tests,           # Pipeline: test generation
            resp_code,            # Pipeline: code generation
            resp_builder_done,    # Builder -> final response
        )

        events = svc.handle_message(
            "Build me an agent that extracts text from PDF files",
            session_id=session_id,
        )

        # Verify: Builder became active during the flow
        # (may have handed back to mycelos at the end, but builder was active)
        handoff_rows = app.storage.fetchall(
            "SELECT details FROM audit_events WHERE event_type = 'agent.handoff'"
        )
        assert len(handoff_rows) >= 1, "At least one handoff should have occurred"
        handoff_targets = []
        for row in handoff_rows:
            d = json.loads(row["details"]) if isinstance(row["details"], str) else row["details"]
            handoff_targets.append(d.get("to", ""))
        assert "builder" in handoff_targets, (
            f"Expected handoff to builder, got targets: {handoff_targets}"
        )

        # Verify: agent is registered
        agent = app.agent_registry.get("pdf-text-extractor")
        assert agent is not None, "Agent should be registered after full flow"

        # Verify: progress events were emitted during the flow
        # Note: the handoff tool loop does not flush _pending_events into the
        # main events list (that only happens in the outer tool loop), so
        # pipeline progress events land on svc._pending_events instead.
        # We check both locations.
        progress_events = [e for e in events if e.type == "step-progress"]
        progress_steps = [e.data.get("step_id", "") for e in progress_events]

        pending = getattr(svc, "_pending_events", [])
        pending_steps = [e.data.get("step_id", "") for e in pending]
        all_steps = progress_steps + pending_steps

        assert any("create_agent" in s or "feasibility" in s or "gherkin" in s
                    or "handoff" in s
                    for s in all_steps), (
            f"Expected pipeline or handoff progress events, got: {all_steps}"
        )

        # Verify: final response includes success text
        text_events = [e for e in events if e.type == "text"]
        assert len(text_events) >= 1, "Should have at least one text response"
        all_text = " ".join(e.data.get("content", "") for e in text_events)
        assert "created" in all_text.lower() or "registered" in all_text.lower() or "success" in all_text.lower(), (
            f"Expected success message in response, got: {all_text[:200]}"
        )

        # Verify: LLM was called multiple times (conversation + pipeline)
        assert app._llm.complete.call_count >= 4, (
            f"Expected at least 4 LLM calls (handoff + builder + pipeline), "
            f"got {app._llm.complete.call_count}"
        )


# ---------------------------------------------------------------------------
# Helper: pipeline-specific LLM mock
# ---------------------------------------------------------------------------

def _mock_pipeline_llm() -> MagicMock:
    """Create a mock LLM for pipeline-only tests.

    Returns responses in order: effort -> gherkin -> tests -> code.
    Prepends the effort classification response (expected by CreatorPipeline).
    """
    mock = MagicMock()
    mock.total_tokens = 0
    mock.total_cost = 0.0

    responses = ["trivial", VALID_GHERKIN, VALID_TESTS, VALID_CODE]
    idx = [0]

    def side_effect(*args: Any, **kwargs: Any) -> MagicMock:
        i = min(idx[0], len(responses) - 1)
        idx[0] += 1
        mock.total_tokens += 100
        r = MagicMock()
        r.content = responses[i]
        r.total_tokens = 100
        r.model = "test-model"
        r.cost = 0.001
        r.tool_calls = None
        return r

    mock.complete.side_effect = side_effect
    return mock


# ---------------------------------------------------------------------------
# Test 4: Dependency management — missing packages raise PermissionRequired
# ---------------------------------------------------------------------------

class TestDependencyManagement:
    """Verify that the dependency check in create_agent works correctly."""

    def test_missing_dependency_raises_permission_required(self, app: App) -> None:
        """create_agent with uninstalled dependencies raises PermissionRequired.

        The package 'nonexistent-pkg-xyz' is not installed, so the tool must
        raise PermissionRequired with action_type='package' before starting
        the pipeline.
        """
        from mycelos.security.permissions import PermissionRequired

        app._llm = _mock_pipeline_llm()
        app._auditor = _approve_auditor()

        svc = ChatService(app)

        with pytest.raises(PermissionRequired) as exc_info:
            svc._execute_tool("create_agent", {
                "name": "dep-test-agent",
                "description": "Agent that needs a missing package",
                "dependencies": ["nonexistent-pkg-xyz"],
            })

        perm = exc_info.value
        assert perm.tool == "create_agent"
        assert perm.action_type == "package"
        assert "nonexistent-pkg-xyz" in perm.target
        assert "nonexistent-pkg-xyz" in perm.reason
        assert "nonexistent-pkg-xyz" in perm.action
        assert perm.original_args["name"] == "dep-test-agent"

    def test_installed_dependency_passes(self, app: App) -> None:
        """create_agent with stdlib module as dependency does not raise.

        'json' is always available in the stdlib, so no PermissionRequired
        should be raised and the pipeline runs normally.
        """
        app._llm = _mock_pipeline_llm()
        app._auditor = _approve_auditor()

        svc = ChatService(app)
        result = svc._execute_tool("create_agent", {
            "name": "stdlib-dep-agent",
            "description": "Agent using only stdlib modules",
            "dependencies": ["json"],
            "capabilities": ["filesystem.read"],
        })

        # Pipeline should run and succeed (not raise PermissionRequired)
        assert result["status"] == "success", f"Expected success, got: {result}"
        assert result["agent_id"] == "stdlib-dep-agent"

    def test_multiple_missing_dependencies(self, app: App) -> None:
        """All missing packages are listed in the PermissionRequired."""
        from mycelos.security.permissions import PermissionRequired

        app._llm = _mock_pipeline_llm()
        app._auditor = _approve_auditor()

        svc = ChatService(app)

        with pytest.raises(PermissionRequired) as exc_info:
            svc._execute_tool("create_agent", {
                "name": "multi-dep-agent",
                "description": "Agent needing multiple missing packages",
                "dependencies": ["nonexistent-aaa", "json", "nonexistent-bbb"],
            })

        perm = exc_info.value
        assert "nonexistent-aaa" in perm.target
        assert "nonexistent-bbb" in perm.target
        # json is installed, so it should NOT be in the missing list
        assert "json" not in perm.target.split(", ")
