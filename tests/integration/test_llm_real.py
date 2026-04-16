"""Integration tests with real LLM API calls.

These tests cost real money! Run with:
    pytest -m integration tests/integration/ -v

Inspired by Gherkin scenarios:
- UC22: Onboarding Interview (Creator-Agent greets, asks name)
- UC21: Ad-hoc Quick Tasks (simple questions get answers)
- UC08: Cost Optimization (cheap model for simple tasks)
"""

import json
import os
import tempfile
from pathlib import Path

import pytest

from mycelos.app import App
from mycelos.llm.broker import LiteLLMBroker, LLMResponse


# ── Basic LLM Connectivity ──


@pytest.mark.integration
def test_real_llm_responds(require_anthropic_key):
    """Smoke test: can we reach the Anthropic API and get a response?"""
    broker = LiteLLMBroker(default_model="anthropic/claude-haiku-4-5")
    response = broker.complete([
        {"role": "user", "content": "Say just the word 'hello'."}
    ])
    assert isinstance(response, LLMResponse)
    assert len(response.content) > 0
    assert response.total_tokens > 0
    assert "hello" in response.content.lower()


@pytest.mark.integration
def test_real_llm_haiku_is_cheapest(require_anthropic_key):
    """UC08: Haiku should be usable for simple tasks (cost optimization)."""
    broker = LiteLLMBroker(default_model="anthropic/claude-haiku-4-5")
    response = broker.complete([
        {"role": "user", "content": "What is 2 + 2? Reply with just the number."}
    ])
    assert "4" in response.content


# ── Creator-Agent Onboarding ──


@pytest.mark.integration
def test_creator_agent_greeting(require_anthropic_key):
    """UC22: Creator-Agent should greet new users warmly and ask their name."""
    broker = LiteLLMBroker(default_model="anthropic/claude-sonnet-4-6")
    system_prompt = (
        "You are the Creator-Agent in Mycelos — the user's personal AI assistant. "
        "This is a NEW user. Start with a friendly welcome and ask their name. "
        "Speak in the user's language. Be warm and concise."
    )
    response = broker.complete([
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": "Hello!"},
    ])
    # Should be a greeting, not a technical dump
    assert len(response.content) > 20
    assert len(response.content) < 2000  # concise, not a wall of text
    # Should ask for name or welcome the user
    content_lower = response.content.lower()
    assert any(word in content_lower for word in ["name", "willkommen", "welcome", "hallo", "hello", "hi"])


@pytest.mark.integration
def test_creator_agent_responds_in_german(require_anthropic_key):
    """UC22: Creator-Agent should respond in German when user writes German."""
    broker = LiteLLMBroker(default_model="anthropic/claude-sonnet-4-6")
    system_prompt = (
        "You are the Creator-Agent in Mycelos. "
        "Speak in the user's language. Be warm and concise."
    )
    response = broker.complete([
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": "Hallo! Ich bin neu hier."},
    ])
    # Should contain German words
    content_lower = response.content.lower()
    assert any(word in content_lower for word in ["willkommen", "hallo", "mycelos", "freut", "hilfe", "name"])


# ── Error Handling ──


@pytest.mark.integration
def test_wrong_model_gives_clear_error(require_anthropic_key):
    """UC06 inspired: wrong model should give a clear error, not crash."""
    broker = LiteLLMBroker(default_model="anthropic/nonexistent-model-xyz")
    with pytest.raises(Exception) as exc_info:
        broker.complete([{"role": "user", "content": "test"}])
    # Should be a clear API error, not a crash
    assert "not_found" in str(exc_info.value).lower() or "error" in str(exc_info.value).lower()


# ── Full App Integration ──


@pytest.mark.integration
def test_full_app_init_and_chat(require_anthropic_key):
    """Full flow: initialize App, store credential, make LLM call."""
    with tempfile.TemporaryDirectory() as tmp:
        data_dir = Path(tmp)
        os.environ["MYCELOS_MASTER_KEY"] = "integration-test-key"

        app = App(data_dir)
        app.initialize_with_config(
            default_model="anthropic/claude-haiku-4-5",
            provider="anthropic",
        )

        # Store credential
        app.credentials.store_credential("anthropic", {
            "api_key": os.environ["ANTHROPIC_API_KEY"],
            "env_var": "ANTHROPIC_API_KEY",
            "provider": "anthropic",
        })

        # Make LLM call — llm broker reads credential from store directly
        response = app.llm.complete([
            {"role": "user", "content": "Say 'integration test passed'."}
        ])
        assert "integration" in response.content.lower() or "passed" in response.content.lower()

        # Verify audit trail
        events = app.audit.query(event_type="system.initialized")
        assert len(events) >= 1


# ── Workflow Pipeline ──


@pytest.mark.integration
def test_workflow_with_real_search_and_mock_llm(require_anthropic_key):
    """Real search + mock LLM summary — full workflow pipeline."""
    pytest.skip("WorkflowExecutor removed — test needs rewrite for WorkflowAgent")
    from mycelos.agents.models import AgentOutput
    from mycelos.connectors.search_tools import search_news
    from mycelos.workflows.models import Workflow, WorkflowStep

    wf = Workflow(
        name="news-summary",
        steps=[
            WorkflowStep(
                id="search",
                action="Search news",
                agent="search-agent",
                policy="always",
            ),
            WorkflowStep(
                id="summarize",
                action="Summarize",
                agent="summary-agent",
                policy="always",
            ),
        ],
    )

    def real_runner(step, ctx):
        if step.id == "search":
            results = search_news("artificial intelligence", max_results=3)
            return AgentOutput(
                success=True,
                result={"articles": results},
                artifacts=[],
                metadata={"cost": 0.0},
            )
        elif step.id == "summarize":
            articles = (
                ctx.get("steps", {})
                .get("search", {})
                .get("result", {})
                .get("articles", [])
            )
            summary = "## AI News Summary\n\n"
            for a in articles:
                summary += (
                    f"- **{a.get('title', '?')}**: "
                    f"{a.get('snippet', '')[:100]}\n"
                )
            return AgentOutput(
                success=True,
                result=summary,
                artifacts=[],
                metadata={"cost": 0.003},
            )
        return AgentOutput(
            success=False,
            result=None,
            artifacts=[],
            metadata={},
            error="Unknown step",
        )

    result = WorkflowExecutor(agent_runner=real_runner).execute(wf)
    assert result.success is True
    assert len(result.step_results) == 2
    # Summary should have content
    summary = result.step_results["summarize"].result
    assert "##" in summary
    print(f"\n{summary}")
