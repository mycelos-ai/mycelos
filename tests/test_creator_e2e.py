"""End-to-end tests for Creator Pipeline via ChatService."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from mycelos.agents.agent_spec import AgentSpec
from mycelos.agents.creator_pipeline import CreatorPipeline, CreatorResult
from mycelos.app import App


@pytest.fixture
def app() -> App:
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["MYCELOS_MASTER_KEY"] = "test-key-e2e"
        a = App(Path(tmp))
        a.initialize()
        yield a


MOCK_GHERKIN = """\
Feature: News Search
  Scenario: Search for news
    Given a search query
    When the agent searches
    Then results are returned
"""

MOCK_TESTS = """\
from agent_code import NewsSearchAgent

def test_search():
    agent = NewsSearchAgent()
    result = agent.execute(type('I', (), {'task': 'AI', 'context': {}})())
    assert result.success
"""

MOCK_CODE = """\
class NewsSearchAgent:
    agent_id = "news-search-agent"
    def execute(self, input):
        return type('R', (), {'success': True, 'result': ['news'], 'artifacts': [], 'metadata': {}, 'error': ''})()
"""


def _mock_llm(responses):
    """Create a mock LLM that returns the given responses in order.

    Prepends a 'trivial' response for classify_effort's LLM call.
    """
    mock = MagicMock()
    mock.total_tokens = 0
    all_responses = ["trivial"] + list(responses)
    idx = [0]

    def side_effect(*a, **kw):
        i = min(idx[0], len(all_responses) - 1)
        idx[0] += 1
        mock.total_tokens += 50
        r = MagicMock()
        r.content = all_responses[i]
        r.total_tokens = 50
        r.model = "test"
        r.tool_calls = None
        return r

    mock.complete.side_effect = side_effect
    return mock


# --- Full E2E ---


def test_e2e_simple_agent_creation(app):
    """Full pipeline: spec -> gherkin -> tests -> code -> audit -> register."""
    spec = AgentSpec(
        name="news-search-agent",
        description="Search for news articles",
        capabilities_needed=["search.web"],
    )

    app._llm = _mock_llm([MOCK_GHERKIN, MOCK_TESTS, MOCK_CODE])
    app._auditor = MagicMock()
    app._auditor.review_code_and_tests.return_value = {"approved": True, "findings": []}

    pipeline = CreatorPipeline(app)
    result = pipeline.run(spec)

    assert result.success, f"Failed: {result.error}"
    assert result.agent_id == "news-search-agent"

    # Verify agent registered
    agent = app.agent_registry.get("news-search-agent")
    assert agent is not None
    assert agent["status"] == "active"

    # Verify code in Object Store
    assert agent["code_hash"] is not None

    # Verify config generation created
    gen = app.config.get_active_generation_id()
    assert gen is not None


def test_e2e_effort_classification(app):
    """Effort is classified (without LLM, heuristic uses capability count)."""
    spec = AgentSpec(name="simple", description="Simple agent", capabilities_needed=[])
    pipeline = CreatorPipeline(app)
    # Just check effort gets set — LLM-based classification falls back to heuristic
    from mycelos.agents.agent_spec import classify_effort
    effort = classify_effort(spec)
    assert effort in ("trivial", "small", "medium", "large", "unrealistic")


def test_e2e_audit_failure(app):
    """Audit rejection prevents registration."""
    spec = AgentSpec(name="bad", description="Simple test", capabilities_needed=[])

    app._llm = _mock_llm([MOCK_GHERKIN, MOCK_TESTS, MOCK_CODE])
    app._auditor = MagicMock()
    app._auditor.review_code_and_tests.return_value = {
        "approved": False,
        "findings": [{"severity": "HIGH", "message": "Unsafe import"}],
    }

    pipeline = CreatorPipeline(app)
    result = pipeline.run(spec)

    assert not result.success
    assert "audit" in result.error.lower() or "nicht bestanden" in result.error.lower()

    # Agent should NOT be registered
    assert app.agent_registry.get("bad") is None


def test_e2e_budget_pause(app):
    """Budget exceeded pauses the pipeline."""
    spec = AgentSpec(
        name="pricey",
        description="Do stuff",
        capabilities_needed=["a", "b", "c"],
        model_tier="opus",
        effort="medium",
    )

    pipeline = CreatorPipeline(app)
    result = pipeline.run(spec, budget_limit=0.001)

    assert not result.success
    assert result.paused
    assert "budget" in result.pause_reason.lower()


def test_e2e_gherkin_scenarios_generated(app):
    """Pipeline should produce Gherkin scenarios."""
    spec = AgentSpec(name="test-gherkin", description="Test", capabilities_needed=[])

    app._llm = _mock_llm([MOCK_GHERKIN, MOCK_TESTS, MOCK_CODE])
    app._auditor = MagicMock()
    app._auditor.review_code_and_tests.return_value = {"approved": True, "findings": []}

    pipeline = CreatorPipeline(app)
    result = pipeline.run(spec)

    assert "Feature" in result.gherkin or "Scenario" in result.gherkin


def test_e2e_artifacts_preserved_on_failure(app):
    """Even on failure, generated artifacts should be in the result."""
    spec = AgentSpec(name="partial", description="Test", capabilities_needed=[])

    app._llm = _mock_llm([MOCK_GHERKIN, MOCK_TESTS, "invalid python {{{{"])
    app._auditor = MagicMock()

    pipeline = CreatorPipeline(app)
    result = pipeline.run(spec)

    # Gherkin and tests were generated before code failed
    assert result.gherkin != ""
    assert result.tests != ""


def test_chat_service_suggest_agent_name():
    """Agent name suggestion from description."""
    from mycelos.chat.service import ChatService

    with tempfile.TemporaryDirectory() as tmp:
        os.environ["MYCELOS_MASTER_KEY"] = "test"
        a = App(Path(tmp))
        a.initialize()
        svc = ChatService(a)
        name = svc._suggest_agent_name("Search for news articles about AI")
        assert "agent" in name
        assert "-" in name
        # Should not contain stop words
        assert "for" not in name.split("-")
