"""Integration test: Real multi-turn chat conversations with Haiku.

Tests coherent multi-turn dialogue and slash command behavior.

Cost estimate: ~$0.003 per run (Haiku model, short prompts)
"""

from __future__ import annotations

import pytest


def _get_llm_text(events) -> str:
    """Extract LLM response text from events, skipping gamification greetings."""
    parts = []
    for e in events:
        if e.type == "text":
            parts.append(e.data.get("content", ""))
        elif e.type == "system-response":
            content = e.data.get("content", "")
            # Skip gamification greetings (🌱, 🔍, 🔧, etc.)
            if content and not any(content.startswith(icon) for icon in ("🌱", "🔍", "🔧", "⚡", "🧠")):
                parts.append(content)
    return " ".join(parts)


def _skip_if_no_llm_response(events):
    """Skip test if LLM call failed (stale cassette or missing key)."""
    error_events = [e for e in events if e.type == "error"]
    text = _get_llm_text(events)
    if error_events and not text.strip():
        error_msg = error_events[0].data.get("content", "")
        pytest.skip(f"LLM call failed (re-record cassettes): {error_msg[:200]}")


@pytest.mark.integration
def test_multi_turn_conversation(integration_app, require_anthropic_key):
    """Real multi-turn conversation with Haiku — verify coherent responses."""
    from mycelos.chat.service import ChatService

    app = integration_app
    app.memory.set("default", "system", "user.name", "TestUser", created_by="test")
    app.memory.set("default", "system", "onboarding_completed", "true", created_by="test")
    service = ChatService(app)
    session = service.create_session()

    # Turn 1: greeting with name
    events1 = service.handle_message("Hello, my name is TestUser", session, "default")
    _skip_if_no_llm_response(events1)
    texts1 = _get_llm_text(events1)
    assert texts1, "Should get a text response to greeting"

    # Turn 2: ask about name (test in-session memory)
    events2 = service.handle_message("What's my name?", session, "default")
    _skip_if_no_llm_response(events2)
    texts2 = _get_llm_text(events2)
    assert "testuser" in texts2.lower(), \
        f"Should remember name from same session: {texts2}"


@pytest.mark.integration
def test_simple_question_gets_answer(integration_app, require_anthropic_key):
    """A simple factual question should get a meaningful answer."""
    from mycelos.chat.service import ChatService

    app = integration_app
    app.memory.set("default", "system", "user.name", "TestUser", created_by="test")
    app.memory.set("default", "system", "onboarding_completed", "true", created_by="test")
    service = ChatService(app)
    session = service.create_session()

    events = service.handle_message(
        "What is 2 + 2? Reply with just the number.",
        session, "default")

    _skip_if_no_llm_response(events)
    texts = _get_llm_text(events)
    assert "4" in texts, f"Simple math should return 4: {texts}"


@pytest.mark.integration
def test_slash_command_cost(integration_app):
    """The /cost slash command should return usage information."""
    from mycelos.chat.slash_commands import handle_slash_command

    app = integration_app
    result = handle_slash_command(app, "/cost")

    assert isinstance(result, str), "Cost command should return a string"
    # Should either say no usage or show LLM Usage
    assert "llm usage" in result.lower() or "no llm usage" in result.lower() or \
           "today" in result.lower() or "$" in result, \
        f"Cost command should show usage info: {result}"


@pytest.mark.integration
def test_slash_command_help(integration_app):
    """The /help slash command should list available commands."""
    from mycelos.chat.slash_commands import handle_slash_command

    app = integration_app
    result = handle_slash_command(app, "/help")

    assert isinstance(result, (str, list)), "Help should return string or event list"
    result_text = result if isinstance(result, str) else str(result)
    # Should mention some commands
    assert "/memory" in result_text or "/mount" in result_text or "help" in result_text.lower(), \
        f"Help should list commands: {result_text[:200]}"


@pytest.mark.integration
def test_conversation_events_structure(integration_app, require_anthropic_key):
    """ChatService events should have correct structure."""
    from mycelos.chat.service import ChatService

    app = integration_app
    app.memory.set("default", "system", "user.name", "TestUser", created_by="test")
    app.memory.set("default", "system", "onboarding_completed", "true", created_by="test")
    service = ChatService(app)
    session = service.create_session()

    events = service.handle_message(
        "Say hello in one word.",
        session, "default")

    assert len(events) >= 1, "Should get at least one event"

    # Every event should have type and data
    for event in events:
        assert hasattr(event, "type"), f"Event missing type: {event}"
        assert hasattr(event, "data"), f"Event missing data: {event}"

    # At least one text or system-response event should exist
    text_events = [e for e in events if e.type in ("text", "system-response")]
    assert len(text_events) >= 1, \
        f"Should have at least one text event: {[e.type for e in events]}"
