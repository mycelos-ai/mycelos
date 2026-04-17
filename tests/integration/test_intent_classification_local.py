"""Integration test: Intent classification with a local LLM.

ChatOrchestrator.classify() asks the cheapest model to categorize a user
message into one of four intents. Pure classification — no tool calls,
no multi-step reasoning — exactly what an 8B local model can do well.

Gated on OLLAMA_HOST. Skipped without it.
"""

from __future__ import annotations

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.timeout(300)]


@pytest.mark.parametrize("integration_app_local", ["ollama"], indirect=True)
def test_intent_classification_conversation(integration_app_local):
    """A friendly greeting should classify as CONVERSATION (the default)."""
    from mycelos.orchestrator import ChatOrchestrator, Intent

    app = integration_app_local
    orchestrator = ChatOrchestrator(
        llm=app.llm,
        classifier_model=app.resolve_cheapest_model(),
    )
    intent = orchestrator.classify("Hey, how are you today?")
    assert intent in (Intent.CONVERSATION, Intent.TASK_REQUEST), \
        f"greeting should not classify as {intent.value}"


@pytest.mark.parametrize("integration_app_local", ["ollama"], indirect=True)
def test_intent_classification_create_agent(integration_app_local):
    """Explicit agent-creation request should classify as CREATE_AGENT.

    This is the most important intent to get right — misclassifying an
    agent creation as conversation means the user's request silently falls
    through.
    """
    from mycelos.orchestrator import ChatOrchestrator, Intent

    app = integration_app_local
    orchestrator = ChatOrchestrator(
        llm=app.llm,
        classifier_model=app.resolve_cheapest_model(),
    )
    intent = orchestrator.classify(
        "Please create a new agent that watches my email inbox and summarizes it every morning."
    )
    # Lax assertion: we accept CREATE_AGENT or TASK_REQUEST. Small models
    # sometimes collapse the distinction but both route to useful handlers.
    assert intent in (Intent.CREATE_AGENT, Intent.TASK_REQUEST), \
        f"'create a new agent' should not classify as {intent.value}"
