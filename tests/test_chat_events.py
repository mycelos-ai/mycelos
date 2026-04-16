"""Tests for ChatEvent model and SSE serialization."""

from __future__ import annotations

import json

import pytest

from mycelos.chat.events import (
    ChatEvent,
    agent_event,
    done_event,
    error_event,
    plan_event,
    session_event,
    step_progress_event,
    system_response_event,
    text_delta_event,
    text_event,
)


def test_chat_event_to_sse():
    event = ChatEvent(type="text-delta", data={"delta": "Hello"})
    sse = event.to_sse()
    assert sse == 'event: text-delta\ndata: {"delta": "Hello"}\n\n'


def test_chat_event_to_dict():
    event = ChatEvent(type="agent", data={"agent": "Creator"})
    d = event.to_dict()
    assert d == {"type": "agent", "data": {"agent": "Creator"}}


def test_chat_event_frozen():
    event = ChatEvent(type="test", data={"key": "val"})
    with pytest.raises(AttributeError):
        event.type = "other"


def test_agent_event():
    e = agent_event("Planner-Agent")
    assert e.type == "agent"
    assert e.data["agent"] == "Planner-Agent"


def test_text_delta_event():
    e = text_delta_event("chunk")
    assert e.type == "text-delta"
    assert e.data["delta"] == "chunk"


def test_text_event():
    e = text_event("Full response")
    assert e.type == "text"
    assert e.data["content"] == "Full response"


def test_plan_event():
    plan = {"action": "execute", "steps": []}
    e = plan_event("task-123", plan)
    assert e.type == "plan"
    assert e.data["task_id"] == "task-123"
    assert e.data["plan"] == plan


def test_step_progress_event():
    e = step_progress_event("search", "done")
    assert e.type == "step-progress"
    assert e.data["step_id"] == "search"
    assert e.data["status"] == "done"


def test_system_response_event():
    e = system_response_event("Config info here")
    assert e.type == "system-response"
    assert e.data["content"] == "Config info here"


def test_error_event():
    e = error_event("Something broke")
    assert e.type == "error"
    assert e.data["message"] == "Something broke"


def test_done_event():
    e = done_event(tokens=150, model="claude-sonnet-4-6", cost=0.0045)
    assert e.type == "done"
    assert e.data["tokens"] == 150
    assert e.data["model"] == "claude-sonnet-4-6"
    assert e.data["cost"] == 0.0045


def test_done_event_defaults():
    e = done_event()
    assert e.data["tokens"] == 0
    assert e.data["model"] == ""
    assert e.data["cost"] == 0.0


def test_session_event():
    e = session_event("sess-abc", resumed=True)
    assert e.type == "session"
    assert e.data["session_id"] == "sess-abc"
    assert e.data["resumed"] is True


def test_sse_unicode():
    """SSE should handle unicode correctly."""
    e = text_event("Grüße aus München")
    sse = e.to_sse()
    assert "Grüße aus München" in sse


def test_sse_parseable():
    """SSE data line should be valid JSON."""
    e = plan_event("t1", {"steps": [{"id": "s1"}]})
    sse = e.to_sse()
    lines = sse.strip().split("\n")
    data_line = [l for l in lines if l.startswith("data: ")][0]
    parsed = json.loads(data_line[6:])
    assert parsed["task_id"] == "t1"


from mycelos.chat.events import widget_event
from mycelos.widgets import TextBlock, Table, Compose, widget_to_dict


class TestWidgetEvent:
    def test_single_widget(self):
        w = TextBlock(text="Hello", weight="bold")
        event = widget_event(w)
        assert event.type == "widget"
        assert event.data["widget"]["type"] == "text_block"
        assert event.data["widget"]["text"] == "Hello"

    def test_compose_widget(self):
        w = Compose(children=[
            TextBlock(text="Title"),
            Table(headers=["A"], rows=[["1"]]),
        ])
        event = widget_event(w)
        assert event.data["widget"]["type"] == "compose"
        assert len(event.data["widget"]["children"]) == 2

    def test_sse_serialization(self):
        w = TextBlock(text="Hi")
        event = widget_event(w)
        sse = event.to_sse()
        assert sse.startswith("event: widget\n")
        assert '"type": "text_block"' in sse
