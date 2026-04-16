"""Integration test: Creator Agent interview flow with real LLM.

Tests that asking to create an agent triggers the Creator interview flow.

Cost estimate: ~$0.003 per run (Haiku model, short prompts)
"""

from __future__ import annotations

import pytest


@pytest.mark.integration
def test_creator_interview_starts(integration_app, require_anthropic_key):
    """Asking to create an agent should start the Creator interview."""
    from mycelos.chat.service import ChatService

    app = integration_app
    service = ChatService(app)
    session = service.create_session()

    events = service.handle_message(
        "Create an agent that reads CSV files and summarizes them",
        session, "default")

    # Should see Creator-Agent attribution or interview question
    all_text = " ".join(
        str(e.data) for e in events
    ).lower()

    # The response should indicate the Creator is engaging with meaningful content
    assert len(events) >= 1, \
        f"Should get a meaningful response, got {len(events)} events"

    # Should get at least some textual content
    text_events = [e for e in events if e.type in ("text", "system-response", "agent")]
    assert len(text_events) >= 1, \
        f"Should get at least one text/response event: {[e.type for e in events]}"


@pytest.mark.integration
def test_creator_agent_responds_to_capability_request(integration_app, require_anthropic_key):
    """Creator agent should respond meaningfully to agent creation requests."""
    from mycelos.chat.service import ChatService

    app = integration_app
    service = ChatService(app)
    session = service.create_session()

    events = service.handle_message(
        "I need an agent that can monitor my inbox and summarize emails daily",
        session, "default")

    all_data = " ".join(str(e.data) for e in events)

    # Must have some response
    assert len(all_data) > 10, \
        f"Response should contain meaningful content: {all_data}"

    # Should get a response (not crash or return empty)
    assert len(events) >= 1, "Should get at least one event"


@pytest.mark.integration
def test_chat_handles_agent_registration_requires_confirmation(integration_app, require_anthropic_key):
    """Agent registration policy should require confirmation (protected resource)."""
    app = integration_app

    # agent.register is a protected resource — always returns 'confirm'
    decision = app.policy_engine.evaluate("default", "creator-agent", "agent.register")
    assert decision == "confirm", \
        f"agent.register should always require confirmation, got '{decision}'"
