"""Tests for ChatService — channel-agnostic message handler."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from mycelos.app import App
from mycelos.chat.events import ChatEvent
from mycelos.chat.service import ChatService
from mycelos.llm.broker import LLMResponse


@pytest.fixture
def app() -> App:
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["MYCELOS_MASTER_KEY"] = "test-key-chat-service"
        a = App(Path(tmp))
        a.initialize()
        yield a


@pytest.fixture
def service(app: App) -> ChatService:
    return ChatService(app)


def _mock_llm_response(content: str = "Hello!", tokens: int = 10) -> MagicMock:
    mock = MagicMock()
    mock.content = content
    mock.total_tokens = tokens
    mock.model = "test-model"
    mock.tool_calls = None  # No tool calls by default
    mock.cost = 0.0
    return mock


# --- Session management ---


def test_create_session(service: ChatService):
    session_id = service.create_session()
    assert session_id is not None
    assert len(session_id) > 0


def test_create_session_with_user(service: ChatService):
    session_id = service.create_session(user_id="stefan")
    assert session_id is not None


# --- Message handling ---


def test_handle_message_returns_events(service: ChatService, app: App):
    """Basic message should return text + done events (may include proactive events)."""
    session_id = service.create_session()

    with patch.object(app.llm, "complete", return_value=_mock_llm_response("Hi there!")):
        events = service.handle_message("Hello", session_id)

    assert len(events) >= 2
    types = [e.type for e in events]
    # Must have text + done. May also have agent, system-response
    # (from gamification hints, KB context, task reminders, onboarding)
    assert "text" in types
    assert "done" in types


def test_handle_message_persists_to_session(service: ChatService, app: App):
    """Messages should be saved to the session store."""
    session_id = service.create_session()

    with patch.object(app.llm, "complete", return_value=_mock_llm_response("Response")):
        service.handle_message("Test message", session_id)

    messages = app.session_store.load_messages(session_id)
    assert len(messages) >= 2  # user + assistant
    assert messages[0]["role"] == "user"
    assert messages[0]["content"] == "Test message"


def test_first_user_message_sets_session_title(service: ChatService, app: App):
    """Deterministic auto-title: the first user message in an untitled
    session becomes the title (up to 60 chars, ellipsised). This runs
    independent of the LLM so it can't be 'forgotten'."""
    session_id = service.create_session()
    assert app.session_store.get_session_meta(session_id).get("title", "") == ""

    with patch.object(app.llm, "complete", return_value=_mock_llm_response("ok")):
        service.handle_message("Remind me to buy milk tomorrow", session_id)

    meta = app.session_store.get_session_meta(session_id)
    assert meta["title"] == "Remind me to buy milk tomorrow"


def test_long_first_message_is_truncated_with_ellipsis(service: ChatService, app: App):
    """Titles longer than 60 chars get truncated to 60 chars + ellipsis."""
    session_id = service.create_session()
    long_msg = (
        "This is a very long first user message that certainly exceeds "
        "the sixty character cap we set for session titles"
    )
    with patch.object(app.llm, "complete", return_value=_mock_llm_response("ok")):
        service.handle_message(long_msg, session_id)

    title = app.session_store.get_session_meta(session_id)["title"]
    assert len(title) <= 61  # 60 chars + single ellipsis char
    assert title.endswith("…")
    assert title.startswith("This is a very long first user message")


def test_second_message_does_not_overwrite_existing_title(service: ChatService, app: App):
    """Once a title exists (even if manually set), subsequent user
    messages must not overwrite it — the LLM's session_set() tool or
    a manual PATCH remains the only way to change it after."""
    session_id = service.create_session()
    app.session_store.update_session(session_id, title="Manual Title")

    with patch.object(app.llm, "complete", return_value=_mock_llm_response("ok")):
        service.handle_message("Something totally different", session_id)

    assert app.session_store.get_session_meta(session_id)["title"] == "Manual Title"


def test_user_message_to_llm_carries_current_time_prefix(service: ChatService, app: App):
    """The LLM must receive the current time on every user message, not
    just at session start — otherwise relative phrases like "in 5 minutes"
    drift over the lifetime of a long session. The prefix lives only in
    the conversation history passed to the LLM, not in the persisted
    session store (that stays pure user text)."""
    session_id = service.create_session()

    seen_messages: list[list[dict]] = []

    def _capture(messages, **kwargs):
        seen_messages.append([dict(m) for m in messages])
        return _mock_llm_response("ok")

    with patch.object(app.llm, "complete", side_effect=_capture):
        service.handle_message("Remind me in 5 minutes", session_id)

    assert seen_messages, "LLM must have been called"
    msgs = seen_messages[0]
    user_turns = [m for m in msgs if m.get("role") == "user"]
    assert user_turns, "at least one user turn must reach the LLM"
    first = user_turns[0]["content"]
    # Prefix must carry an ISO-ish datetime and the original message verbatim
    assert "current time" in first.lower()
    assert "Remind me in 5 minutes" in first


def test_time_prefix_does_not_pollute_session_store(service: ChatService, app: App):
    """The session store must keep the user's real text — we don't want
    replays/audit/exports to show the injected timestamp."""
    session_id = service.create_session()

    with patch.object(app.llm, "complete", return_value=_mock_llm_response("ok")):
        service.handle_message("Pure text", session_id)

    messages = app.session_store.load_messages(session_id)
    user_msgs = [m for m in messages if m["role"] == "user"]
    assert user_msgs
    assert user_msgs[0]["content"] == "Pure text"
    assert "current time" not in user_msgs[0]["content"].lower()


def test_each_user_message_gets_fresh_timestamp(service: ChatService, app: App):
    """Second user message must see a *new* timestamp, not the one from
    the first message — this is the whole point of the fix. We patch the
    module-level datetime so each handle_message call returns a different
    'now' regardless of how many times the code reads the clock inside a
    single call."""
    session_id = service.create_session()

    seen_messages: list[list[dict]] = []

    def _capture(messages, **kwargs):
        seen_messages.append([dict(m) for m in messages])
        return _mock_llm_response("ok")

    from datetime import datetime as real_dt
    import mycelos.chat.service as svc_mod

    # A single mutable "current fake now" — tests flip this between calls
    # so every datetime.now() inside one handle_message sees the same
    # value, and the second call sees a later one.
    fake_now_holder = {"value": real_dt(2026, 4, 11, 10, 0, 0)}
    original_datetime = svc_mod.datetime

    class _FakeDateTime(real_dt):
        @classmethod
        def now(cls, tz=None):
            val = fake_now_holder["value"]
            return val.replace(tzinfo=tz) if tz else val

    svc_mod.datetime = _FakeDateTime
    try:
        with patch.object(app.llm, "complete", side_effect=_capture):
            service.handle_message("first", session_id)
            fake_now_holder["value"] = real_dt(2026, 4, 11, 10, 5, 0)
            service.handle_message("second", session_id)
    finally:
        svc_mod.datetime = original_datetime

    assert len(seen_messages) == 2
    first_user = next(m for m in reversed(seen_messages[0]) if m.get("role") == "user")
    second_user = next(m for m in reversed(seen_messages[1]) if m.get("role") == "user")
    assert "10:00" in first_user["content"]
    assert "10:05" in second_user["content"]
    assert first_user["content"] != second_user["content"]


def test_handle_message_llm_error(service: ChatService, app: App):
    """LLM errors should return error event, not crash."""
    session_id = service.create_session()

    with patch.object(app.llm, "complete", side_effect=Exception("API down")):
        events = service.handle_message("Hello", session_id)

    types = [e.type for e in events]
    assert "error" in types
    error = next(e for e in events if e.type == "error")
    assert "API down" in error.data["message"]


def test_handle_system_command(service: ChatService, app: App):
    """System commands should return system-response event without LLM call."""
    session_id = service.create_session()
    # Set user name so orchestrator runs
    app.memory.set("default", "system", "user.name", "Test", created_by="test")

    # Mock orchestrator to return SYSTEM_COMMAND
    from mycelos.orchestrator import Intent, RouteResult
    with patch.object(app.orchestrator, "route",
                      return_value=RouteResult(intent=Intent.SYSTEM_COMMAND)):
        events = service.handle_message("show config", session_id)

    types = [e.type for e in events]
    assert "system-response" in types


def test_done_event_has_token_info(service: ChatService, app: App):
    """Done event should include token count and model."""
    session_id = service.create_session()

    with patch.object(app.llm, "complete",
                      return_value=_mock_llm_response("Hi", tokens=42)):
        events = service.handle_message("Hello", session_id)

    done = next(e for e in events if e.type == "done")
    assert done.data["tokens"] >= 42  # May include tool overhead
    assert done.data["model"] == "test-model"


def test_conversation_state_maintained(service: ChatService, app: App):
    """Multiple messages should maintain conversation context."""
    session_id = service.create_session()

    with patch.object(app.llm, "complete", return_value=_mock_llm_response("First")):
        service.handle_message("Message 1", session_id)

    with patch.object(app.llm, "complete", return_value=_mock_llm_response("Second")):
        service.handle_message("Message 2", session_id)

    # Internal conversation should have system + 2 user + 2 assistant
    conv = service._conversations[session_id]
    roles = [m["role"] for m in conv]
    assert roles.count("user") == 2
    assert roles.count("assistant") >= 2  # May include tool-call assistant messages


def test_events_are_chat_event_instances(service: ChatService, app: App):
    """All returned events should be ChatEvent instances."""
    session_id = service.create_session()

    with patch.object(app.llm, "complete", return_value=_mock_llm_response()):
        events = service.handle_message("Hi", session_id)

    for event in events:
        assert isinstance(event, ChatEvent)


def test_events_serializable_to_sse(service: ChatService, app: App):
    """All events should be serializable to SSE format."""
    session_id = service.create_session()

    with patch.object(app.llm, "complete", return_value=_mock_llm_response()):
        events = service.handle_message("Hi", session_id)

    for event in events:
        sse = event.to_sse()
        assert "event: " in sse
        assert "data: " in sse
