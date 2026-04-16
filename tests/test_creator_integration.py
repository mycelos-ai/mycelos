"""Comprehensive integration tests for the Creator-Agent workflow.

These tests run the full Creator Pipeline with mocked LLM but REAL
test execution (pytest runs in subprocess). This verifies the entire
chain: spec -> gherkin -> tests -> code -> sandbox execution -> audit -> register.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from mycelos.agents.agent_spec import AgentSpec, classify_effort, estimate_cost
from mycelos.agents.creator_pipeline import CreatorPipeline, CreatorResult
from mycelos.agents.gherkin_generator import generate_gherkin, parse_gherkin_scenarios
from mycelos.agents.test_generator import generate_tests
from mycelos.agents.code_generator import generate_code
from mycelos.agents.test_runner import run_agent_tests
from mycelos.app import App
from mycelos.chat.service import ChatService


@pytest.fixture
def app() -> App:
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["MYCELOS_MASTER_KEY"] = "test-key-creator-integration"
        a = App(Path(tmp))
        a.initialize()
        yield a


# ---------------------------------------------------------------------------
# Realistic mock LLM responses that produce VALID, PASSING code
# ---------------------------------------------------------------------------

VALID_GHERKIN = """\
Feature: Greeting Agent
  An agent that generates personalized greetings.

  Scenario: Generate a greeting
    Given a user name "Stefan"
    When the agent generates a greeting
    Then the greeting should contain the name "Stefan"

  Scenario: Handle empty name
    Given no user name is provided
    When the agent generates a greeting
    Then the greeting should be a generic welcome message
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
    assert len(result.result) > 0
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

# Code that will FAIL tests (for retry scenarios)
FAILING_CODE = """\
class GreetingAgent:
    agent_id = "greeting-agent"
    def execute(self, input):
        return type('Result', (), {
            'success': True,
            'result': 'wrong output',
            'artifacts': [],
            'metadata': {},
            'error': '',
        })()
"""


def _mock_llm(*responses: str) -> MagicMock:
    """Create mock LLM that returns responses in sequence.

    Prepends a 'trivial' response for classify_effort's LLM call.
    """
    mock = MagicMock()
    mock.total_tokens = 0
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
    m = MagicMock()
    m.review_code_and_tests.return_value = {"approved": True, "findings": []}
    return m


def _reject_audit(reason: str = "Dangerous import detected") -> MagicMock:
    m = MagicMock()
    m.review_code_and_tests.return_value = {
        "approved": False,
        "findings": [{"severity": "HIGH", "message": reason}],
    }
    return m


# ---------------------------------------------------------------------------
# Full pipeline -- happy path
# ---------------------------------------------------------------------------


class TestFullPipelineHappyPath:
    """Complete pipeline run with valid code that passes tests."""

    def test_creates_agent_successfully(self, app):
        spec = AgentSpec(name="greeting-agent", description="Generate greetings")
        app._llm = _mock_llm(VALID_GHERKIN, VALID_TESTS, VALID_CODE)
        app._auditor = _approve_audit()

        result = CreatorPipeline(app).run(spec)

        assert result.success, f"Pipeline failed: {result.error}"
        assert result.agent_id == "greeting-agent"

    def test_agent_registered_in_db(self, app):
        spec = AgentSpec(name="greeting-agent", description="Generate greetings")
        app._llm = _mock_llm(VALID_GHERKIN, VALID_TESTS, VALID_CODE)
        app._auditor = _approve_audit()

        CreatorPipeline(app).run(spec)

        agent = app.agent_registry.get("greeting-agent")
        assert agent is not None
        assert agent["status"] == "active"

    def test_code_stored_in_object_store(self, app):
        spec = AgentSpec(name="greeting-agent", description="Generate greetings")
        app._llm = _mock_llm(VALID_GHERKIN, VALID_TESTS, VALID_CODE)
        app._auditor = _approve_audit()

        CreatorPipeline(app).run(spec)

        agent = app.agent_registry.get("greeting-agent")
        assert agent["code_hash"] is not None
        assert agent["tests_hash"] is not None

        # Verify code loadable from Object Store
        from mycelos.storage.object_store import ObjectStore

        store = ObjectStore(app.data_dir)
        code = store.load(agent["code_hash"])
        assert code is not None
        assert "GreetingAgent" in code

    def test_config_generation_created(self, app):
        spec = AgentSpec(name="greeting-agent", description="Generate greetings")
        app._llm = _mock_llm(VALID_GHERKIN, VALID_TESTS, VALID_CODE)
        app._auditor = _approve_audit()

        gen_before = app.config.get_active_generation_id()
        CreatorPipeline(app).run(spec)
        gen_after = app.config.get_active_generation_id()

        assert gen_after is not None
        assert gen_after != gen_before

    def test_audit_event_logged(self, app):
        spec = AgentSpec(name="greeting-agent", description="Generate greetings")
        app._llm = _mock_llm(VALID_GHERKIN, VALID_TESTS, VALID_CODE)
        app._auditor = _approve_audit()

        CreatorPipeline(app).run(spec)

        events = app.storage.fetchall(
            "SELECT * FROM audit_events WHERE event_type = 'agent.created'"
        )
        assert len(events) >= 1

    def test_all_artifacts_in_result(self, app):
        spec = AgentSpec(name="greeting-agent", description="Generate greetings")
        app._llm = _mock_llm(VALID_GHERKIN, VALID_TESTS, VALID_CODE)
        app._auditor = _approve_audit()

        result = CreatorPipeline(app).run(spec)

        assert result.gherkin != ""
        assert result.tests != ""
        assert result.code != ""
        assert "Feature" in result.gherkin
        assert "def test_" in result.tests
        assert "class" in result.code


# ---------------------------------------------------------------------------
# Test execution -- real pytest in subprocess
# ---------------------------------------------------------------------------


class TestRealTestExecution:
    """Tests that actually run pytest to verify generated code."""

    def test_valid_code_passes_tests(self):
        result = run_agent_tests(VALID_CODE, VALID_TESTS)
        assert result.passed, f"Tests should pass but failed:\n{result.output}\n{result.error}"
        assert result.tests_run >= 2
        assert result.tests_failed == 0

    def test_failing_code_fails_tests(self):
        result = run_agent_tests(FAILING_CODE, VALID_TESTS)
        assert not result.passed
        assert result.tests_failed >= 1

    def test_syntax_error_fails(self):
        bad_code = "class Broken:\n  def execute(self\n    return"
        result = run_agent_tests(bad_code, VALID_TESTS)
        assert not result.passed

    def test_import_error_fails(self):
        tests_bad_import = "from agent_code import NonExistent\ndef test_x(): pass"
        result = run_agent_tests(VALID_CODE, tests_bad_import)
        assert not result.passed

    def test_timeout_handled(self):
        slow_code = """\
class GreetingAgent:
    agent_id = "greeting-agent"
    def execute(self, input):
        import time; time.sleep(100)
        return type('R', (), {'success': True, 'result': '', 'artifacts': [], 'metadata': {}, 'error': ''})()
"""
        slow_tests = """\
from agent_code import GreetingAgent
def test_slow():
    agent = GreetingAgent()
    inp = type('I', (), {'task': 'x', 'context': {}})()
    agent.execute(inp)
"""
        result = run_agent_tests(slow_code, slow_tests, timeout=2)
        assert not result.passed
        assert "timed out" in result.error.lower()


# ---------------------------------------------------------------------------
# Feasibility classification
# ---------------------------------------------------------------------------


class TestFeasibility:
    """Effort classification prevents wasteful generation."""

    def test_unrealistic_rejected_by_capability_count(self, app):
        spec = AgentSpec(
            name="mega", description="Agent with too many capabilities",
            capabilities_needed=["a", "b", "c", "d", "e", "f", "g", "h", "i"],
        )
        # Without LLM, heuristic: 9 caps → large (fallback caps everything)
        assert classify_effort(spec) == "large"

    def test_large_paused_for_splitting(self, app):
        spec = AgentSpec(
            name="complex", description="Complex agent",
            capabilities_needed=["a", "b", "c", "d", "e", "f"],
        )
        result = CreatorPipeline(app).run(spec)
        assert not result.success
        assert result.paused
        assert result.pause_reason == "needs_splitting"

    def test_trivial_runs_directly(self):
        spec = AgentSpec(
            name="simple",
            description="Search the web",
            capabilities_needed=["search.web"],
        )
        assert classify_effort(spec) == "trivial"

    def test_budget_check_before_generation(self, app):
        spec = AgentSpec(
            name="x",
            description="test",
            capabilities_needed=["a", "b", "c"],
            model_tier="opus",
            effort="medium",
        )
        result = CreatorPipeline(app).run(spec, budget_limit=0.001)
        assert result.paused
        assert "budget" in result.pause_reason

    def test_effort_levels_spectrum(self):
        """Verify effort heuristic fallback (no LLM)."""
        trivial = AgentSpec(name="a", description="lookup", capabilities_needed=[])
        assert classify_effort(trivial) == "trivial"

        small = AgentSpec(name="b", description="do stuff", capabilities_needed=["x", "y"])
        assert classify_effort(small) == "small"

        medium = AgentSpec(name="c", description="process", capabilities_needed=["a", "b", "c"])
        assert classify_effort(medium) == "medium"

        large = AgentSpec(name="d", description="big", capabilities_needed=["a", "b", "c", "d", "e", "f"])
        assert classify_effort(large) == "large"

    def test_estimate_cost_scales_with_tier(self):
        """Opus should cost more than Haiku for the same effort."""
        spec_haiku = AgentSpec(name="a", description="test", model_tier="haiku", effort="medium")
        spec_opus = AgentSpec(name="b", description="test", model_tier="opus", effort="medium")
        assert estimate_cost(spec_haiku) < estimate_cost(spec_opus)


# ---------------------------------------------------------------------------
# Retry on test failure
# ---------------------------------------------------------------------------


class TestRetryMechanism:
    """Code generation retries when tests fail."""

    def test_retry_produces_fixed_code(self, app):
        """First code fails, second passes -- pipeline should succeed."""
        spec = AgentSpec(name="retry-test", description="Test retry")
        # LLM returns: gherkin, tests, bad_code, good_code
        app._llm = _mock_llm(VALID_GHERKIN, VALID_TESTS, FAILING_CODE, VALID_CODE)
        app._auditor = _approve_audit()

        result = CreatorPipeline(app).run(spec)

        # Should succeed after retry
        assert result.success, f"Failed: {result.error}"
        # LLM called at least 4 times (gherkin + tests + bad_code + good_code)
        assert app._llm.complete.call_count >= 4

    def test_max_retries_exhausted(self, app):
        """All retries fail -- pipeline should report failure."""
        spec = AgentSpec(name="always-fails", description="Test max retry")
        # LLM always returns failing code (3 retries = MAX_CODE_RETRIES)
        app._llm = _mock_llm(
            VALID_GHERKIN, VALID_TESTS,
            FAILING_CODE, FAILING_CODE, FAILING_CODE,
        )
        app._auditor = _approve_audit()

        result = CreatorPipeline(app).run(spec)

        assert not result.success
        assert "failed" in result.error.lower()

    def test_retry_passes_error_output_to_llm(self, app):
        """Verify the retry sends previous error context to the LLM."""
        spec = AgentSpec(name="error-ctx", description="Test error context")
        app._llm = _mock_llm(VALID_GHERKIN, VALID_TESTS, FAILING_CODE, VALID_CODE)
        app._auditor = _approve_audit()

        CreatorPipeline(app).run(spec)

        # The 4th LLM call (retry) should have been called with messages
        # containing error output from the failed run
        calls = app._llm.complete.call_args_list
        assert len(calls) >= 4
        # The retry call messages should reference the error
        retry_call_messages = calls[3][0][0] if calls[3][0] else calls[3][1].get("messages", [])
        retry_prompt = str(retry_call_messages)
        assert "FAILED" in retry_prompt or "Fix" in retry_prompt or "error" in retry_prompt.lower()


# ---------------------------------------------------------------------------
# Audit scenarios
# ---------------------------------------------------------------------------


class TestAuditGate:
    """Audit must pass for registration to happen."""

    def test_audit_rejection_prevents_registration(self, app):
        spec = AgentSpec(name="bad-agent", description="Test audit rejection")
        app._llm = _mock_llm(VALID_GHERKIN, VALID_TESTS, VALID_CODE)
        app._auditor = _reject_audit("Dangerous os.system() call")

        result = CreatorPipeline(app).run(spec)

        assert not result.success
        assert "audit" in result.error.lower() or "bestanden" in result.error.lower()
        # Agent should NOT exist in registry
        assert app.agent_registry.get("bad-agent") is None

    def test_audit_findings_in_result(self, app):
        spec = AgentSpec(name="findings-test", description="Test")
        app._llm = _mock_llm(VALID_GHERKIN, VALID_TESTS, VALID_CODE)
        app._auditor = _reject_audit("Uses subprocess directly")

        result = CreatorPipeline(app).run(spec)

        assert result.audit_result.get("approved") is False
        assert len(result.audit_result.get("findings", [])) > 0

    def test_audit_called_with_correct_args(self, app):
        """Audit receives the generated code, tests, and agent ID."""
        spec = AgentSpec(
            name="audit-args",
            description="Test audit args",
            capabilities_needed=["search.web"],
        )
        app._llm = _mock_llm(VALID_GHERKIN, VALID_TESTS, VALID_CODE)
        app._auditor = _approve_audit()

        CreatorPipeline(app).run(spec)

        app._auditor.review_code_and_tests.assert_called_once()
        call_kwargs = app._auditor.review_code_and_tests.call_args
        # Should pass code, tests, agent_id, and capabilities
        assert "code" in call_kwargs.kwargs or len(call_kwargs.args) >= 1
        assert "tests" in call_kwargs.kwargs or len(call_kwargs.args) >= 2


# ---------------------------------------------------------------------------
# Gherkin generation + parsing
# ---------------------------------------------------------------------------


class TestGherkinIntegration:
    """Gherkin scenarios generated and parseable."""

    def test_gherkin_parseable(self, app):
        spec = AgentSpec(name="test", description="Test")
        app._llm = _mock_llm(VALID_GHERKIN, VALID_TESTS, VALID_CODE)
        app._auditor = _approve_audit()

        result = CreatorPipeline(app).run(spec)

        scenarios = parse_gherkin_scenarios(result.gherkin)
        assert len(scenarios) >= 2

    def test_gherkin_has_given_when_then(self, app):
        spec = AgentSpec(name="test", description="Test")
        app._llm = _mock_llm(VALID_GHERKIN, VALID_TESTS, VALID_CODE)
        app._auditor = _approve_audit()

        result = CreatorPipeline(app).run(spec)
        scenarios = parse_gherkin_scenarios(result.gherkin)

        for scenario in scenarios:
            keywords = {s["keyword"] for s in scenario["steps"]}
            assert "Given" in keywords
            assert "When" in keywords
            assert "Then" in keywords

    def test_gherkin_scenario_titles_extracted(self):
        """Parse extracts meaningful scenario titles."""
        scenarios = parse_gherkin_scenarios(VALID_GHERKIN)
        titles = [s["title"] for s in scenarios]
        assert "Generate a greeting" in titles
        assert "Handle empty name" in titles

    def test_empty_gherkin_returns_empty_list(self):
        """Empty or malformed gherkin produces an empty scenario list."""
        assert parse_gherkin_scenarios("") == []
        assert parse_gherkin_scenarios("just some text") == []


# ---------------------------------------------------------------------------
# ChatService integration
# ---------------------------------------------------------------------------


class TestChatServiceIntegration:
    """Creator Pipeline triggered via ChatService."""

    def test_suggest_agent_name_filters_stops(self, app):
        svc = ChatService(app)
        name = svc._suggest_agent_name("Erstell einen Agent der Nachrichten sucht")
        assert name.endswith("-agent")
        assert "-" in name
        # Stop words like "erstell", "einen", "agent", "der" should be filtered
        assert "einen" not in name
        assert "erstell" not in name

    def test_suggest_agent_name_english(self, app):
        svc = ChatService(app)
        name = svc._suggest_agent_name("I want an agent that summarizes news articles")
        assert name.endswith("-agent")
        # "want", "that" are stop words
        assert "want" not in name

    def test_suggest_agent_name_minimal(self, app):
        svc = ChatService(app)
        name = svc._suggest_agent_name("ein Agent")
        # Both "ein" and "agent" are stop words; falls back to "custom"
        assert name == "custom-agent"

    def test_suggest_agent_name_preserves_meaningful_words(self, app):
        svc = ChatService(app)
        name = svc._suggest_agent_name("Search for recent news articles about technology")
        # "search", "recent", "news" are meaningful (>2 chars, not stops)
        parts = name.replace("-agent", "").split("-")
        assert len(parts) >= 1
        assert all(len(p) > 2 for p in parts)

    def test_handoff_to_creator_switches_session(self, app):
        """Handoff to creator updates the active agent for the session."""
        svc = ChatService(app)
        session_id = svc.create_session()
        svc._execute_handoff(session_id, "creator", "User wants to build an agent")
        assert svc._get_active_agent(session_id) == "creator"

    def test_builder_handler_has_tools(self, app):
        """Builder handler provides tools including handoff and create_agent."""
        handlers = app.get_agent_handlers()
        builder = handlers["builder"]
        tools = builder.get_tools()
        tool_names = [t["function"]["name"] for t in tools]
        assert "handoff" in tool_names
        assert "create_agent" in tool_names
        assert "create_workflow" in tool_names


# ---------------------------------------------------------------------------
# NixOS State integration
# ---------------------------------------------------------------------------


class TestStateIntegration:
    """Created agents should be in the NixOS state snapshot."""

    def test_agent_in_snapshot_after_creation(self, app):
        spec = AgentSpec(name="snapshot-test", description="Test")
        app._llm = _mock_llm(VALID_GHERKIN, VALID_TESTS, VALID_CODE)
        app._auditor = _approve_audit()

        result = CreatorPipeline(app).run(spec)
        assert result.success, f"Pipeline failed: {result.error}"

        snapshot = app.state_manager.snapshot()
        assert "snapshot-test" in snapshot["agents"]
        assert snapshot["agents"]["snapshot-test"]["code_hash"] is not None

    def test_agent_rollbackable_after_creation(self, app):
        # Create initial generation from current state
        gen1 = app.config.apply_from_state(app.state_manager, "before", "test")

        spec = AgentSpec(name="rollback-test", description="Test")
        app._llm = _mock_llm(VALID_GHERKIN, VALID_TESTS, VALID_CODE)
        app._auditor = _approve_audit()

        result = CreatorPipeline(app).run(spec)
        assert result.success, f"Pipeline failed: {result.error}"
        assert app.agent_registry.get("rollback-test") is not None

        # Rollback to generation before agent creation
        app.config.rollback(to_generation=gen1, state_manager=app.state_manager)
        assert app.agent_registry.get("rollback-test") is None

    def test_multiple_agents_in_snapshot(self, app):
        """Creating multiple agents produces a snapshot with all of them."""
        for name in ["agent-alpha", "agent-beta"]:
            spec = AgentSpec(name=name, description="Test")
            app._llm = _mock_llm(VALID_GHERKIN, VALID_TESTS, VALID_CODE)
            app._auditor = _approve_audit()
            result = CreatorPipeline(app).run(spec)
            assert result.success, f"Pipeline failed for {name}: {result.error}"

        snapshot = app.state_manager.snapshot()
        assert "agent-alpha" in snapshot["agents"]
        assert "agent-beta" in snapshot["agents"]


# ---------------------------------------------------------------------------
# Re-creation (update existing agent)
# ---------------------------------------------------------------------------


class TestAgentReCreation:
    """Re-creating an existing agent updates code without duplicate registration."""

    def test_recreate_updates_code_hash(self, app):
        spec = AgentSpec(name="updatable", description="Test")
        app._llm = _mock_llm(VALID_GHERKIN, VALID_TESTS, VALID_CODE)
        app._auditor = _approve_audit()

        # First creation
        result1 = CreatorPipeline(app).run(spec)
        assert result1.success
        agent1 = app.agent_registry.get("updatable")
        hash1 = agent1["code_hash"]

        # Second creation with different code
        different_code = VALID_CODE.replace(
            "Hello, {name}! Welcome to Mycelos.",
            "Hi {name}! Greetings from Mycelos.",
        )
        app._llm = _mock_llm(VALID_GHERKIN, VALID_TESTS, different_code)
        app._auditor = _approve_audit()

        result2 = CreatorPipeline(app).run(spec)
        assert result2.success
        agent2 = app.agent_registry.get("updatable")
        hash2 = agent2["code_hash"]

        # Code hash should have changed
        assert hash1 != hash2


# ---------------------------------------------------------------------------
# Error handling edge cases
# ---------------------------------------------------------------------------


class TestErrorHandling:
    """Pipeline handles various failure modes gracefully."""

    def test_gherkin_generation_failure(self, app):
        spec = AgentSpec(name="fail-gherkin", description="Test")
        mock_llm = MagicMock()
        mock_llm.complete.side_effect = RuntimeError("LLM unavailable")
        app._llm = mock_llm

        result = CreatorPipeline(app).run(spec)
        assert not result.success
        assert "gherkin" in result.error.lower()

    def test_test_generation_failure(self, app):
        spec = AgentSpec(name="fail-tests", description="Test")
        call_count = [0]

        def side_effect(*args, **kwargs):
            call_count[0] += 1
            r = MagicMock()
            r.total_tokens = 100
            r.model = "test"
            r.tool_calls = None
            # Call 1: classify_effort, Call 2: gherkin — both succeed
            if call_count[0] <= 2:
                r.content = "trivial" if call_count[0] == 1 else VALID_GHERKIN
                return r
            # Call 3: test generation — fails
            raise RuntimeError("LLM failed on test gen")

        mock_llm = MagicMock()
        mock_llm.total_tokens = 0
        mock_llm.complete.side_effect = side_effect
        app._llm = mock_llm

        result = CreatorPipeline(app).run(spec)
        assert not result.success
        assert "test generation failed" in result.error.lower()

    def test_code_generation_exception(self, app):
        spec = AgentSpec(name="fail-code", description="Test")
        call_count = [0]

        def side_effect(*args, **kwargs):
            call_count[0] += 1
            r = MagicMock()
            r.total_tokens = 100
            r.model = "test"
            r.tool_calls = None
            # Call 1: classify_effort, Call 2: gherkin, Call 3: tests — all succeed
            if call_count[0] == 1:
                r.content = "trivial"
                return r
            elif call_count[0] == 2:
                r.content = VALID_GHERKIN
                return r
            elif call_count[0] == 3:
                r.content = VALID_TESTS
                return r
            # Call 4+: code generation — fails
            raise RuntimeError("LLM crashed during code gen")

        mock_llm = MagicMock()
        mock_llm.total_tokens = 0
        mock_llm.complete.side_effect = side_effect
        app._llm = mock_llm

        result = CreatorPipeline(app).run(spec)
        assert not result.success
        assert "code generation failed" in result.error.lower()

    def test_creator_result_defaults(self):
        r = CreatorResult(success=False)
        assert r.agent_id is None
        assert r.cost == 0.0
        assert not r.paused
        assert r.error == ""
        assert r.gherkin == ""
        assert r.tests == ""
        assert r.code == ""
