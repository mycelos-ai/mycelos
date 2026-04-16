"""Tests: chat service records session events to JSONL (session audit hooks)."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from mycelos.app import App
from mycelos.chat.service import ChatService


@pytest.fixture
def app() -> App:
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["MYCELOS_MASTER_KEY"] = "test-key-session-audit"
        a = App(Path(tmp))
        a.initialize()
        yield a


@pytest.fixture
def service(app: App) -> ChatService:
    return ChatService(app)


def _mock_text_response(content: str = "Done!", model: str = "test-model") -> MagicMock:
    """LLM response with no tool calls (final answer)."""
    mock = MagicMock()
    mock.content = content
    mock.total_tokens = 20
    mock.model = model
    mock.tool_calls = None
    mock.cost = 0.0
    mock.prompt_tokens = 15
    mock.completion_tokens = 5
    mock.stop_reason = "end_turn"
    return mock


def _mock_tool_response(tool_name: str, args: dict, tool_call_id: str = "tc-test-1") -> MagicMock:
    """LLM response that requests a single tool call."""
    mock = MagicMock()
    mock.content = None
    mock.total_tokens = 25
    mock.model = "test-model"
    mock.cost = 0.0
    mock.prompt_tokens = 20
    mock.completion_tokens = 5
    mock.stop_reason = "tool_use"
    mock.tool_calls = [
        {
            "id": tool_call_id,
            "function": {
                "name": tool_name,
                "arguments": json.dumps(args),
            },
        }
    ]
    return mock


def test_llm_round_is_recorded_for_simple_message(service: ChatService, app: App):
    """A simple text reply should write one llm_round event to the session JSONL."""
    session_id = service.create_session()

    with patch.object(app.llm, "complete", return_value=_mock_text_response("Hello there!")):
        service.handle_message("Hi", session_id)

    all_events = app.session_store.load_all_events(session_id)
    event_types = [e["type"] for e in all_events]

    assert "message" in event_types
    assert "llm_round" in event_types

    llm_rounds = [e for e in all_events if e["type"] == "llm_round"]
    assert len(llm_rounds) == 1
    assert llm_rounds[0]["round"] == 0
    assert llm_rounds[0]["model"] == "test-model"
    assert llm_rounds[0]["stop_reason"] == "end_turn"


def test_tool_call_and_result_are_recorded(service: ChatService, app: App):
    """A tool call should produce tool_call + tool_result events in the JSONL."""
    session_id = service.create_session()

    # First LLM call returns a note_write tool call
    tool_response = _mock_tool_response(
        "note_write",
        {"title": "Audit Test", "content": "test audit event"},
        tool_call_id="tc-audit-1",
    )
    # Second LLM call returns final text (after tool result fed back)
    final_response = _mock_text_response("Note written!")

    call_count = 0

    def side_effect(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        return tool_response if call_count == 1 else final_response

    with patch.object(app.llm, "complete", side_effect=side_effect):
        service.handle_message(
            "Please write a note with content 'test audit event'",
            session_id,
        )

    all_events = app.session_store.load_all_events(session_id)
    event_types = [e["type"] for e in all_events]

    # Must have llm_round events (at least 2: before and after tool call)
    assert "llm_round" in event_types

    # Must have tool_call and tool_result (note_write is a real registered tool)
    tool_calls = [e for e in all_events if e["type"] == "tool_call"]
    tool_results = [e for e in all_events if e["type"] in ("tool_result", "tool_error")]

    assert len(tool_calls) >= 1, f"Expected tool_call events, got: {event_types}"
    assert len(tool_results) >= 1, f"Expected tool_result/error events, got: {event_types}"

    # tool_call_id must match between call and result
    call = tool_calls[0]
    result = tool_results[0]
    assert call["tool_call_id"] == result["tool_call_id"]
    assert call["name"] == "note_write"


def test_llm_round_contains_correct_metadata(service: ChatService, app: App):
    """llm_round events should carry model, token counts, and stop_reason."""
    session_id = service.create_session()
    response = _mock_text_response("answer", model="anthropic/claude-haiku-4-5")
    response.prompt_tokens = 100
    response.completion_tokens = 42
    response.stop_reason = "end_turn"

    with patch.object(app.llm, "complete", return_value=response):
        service.handle_message("What is 2+2?", session_id)

    rounds = [
        e for e in app.session_store.load_all_events(session_id)
        if e["type"] == "llm_round"
    ]
    assert len(rounds) == 1
    r = rounds[0]
    assert r["model"] == "anthropic/claude-haiku-4-5"
    assert r["tokens_in"] == 100
    assert r["tokens_out"] == 42
    assert r["stop_reason"] == "end_turn"
