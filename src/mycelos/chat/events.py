"""Chat events — channel-agnostic response events with SSE serialization.

Events flow from ChatService to channels:
  ChatService → ChatEvent → Terminal (Rich), Gateway (SSE), Slack (chat_stream)
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ChatEvent:
    """A single event in a chat response stream."""

    type: str
    data: dict[str, Any] = field(default_factory=dict)

    def to_sse(self) -> str:
        """Serialize to Server-Sent Events format."""
        return f"event: {self.type}\ndata: {json.dumps(self.data, ensure_ascii=False)}\n\n"

    def to_dict(self) -> dict[str, Any]:
        """Serialize to plain dict."""
        return {"type": self.type, "data": self.data}


# ---------------------------------------------------------------------------
# Factory functions — one per event type
# ---------------------------------------------------------------------------


def agent_event(agent_name: str) -> ChatEvent:
    """Which agent is responding."""
    return ChatEvent(type="agent", data={"agent": agent_name})


def text_delta_event(delta: str) -> ChatEvent:
    """Incremental text chunk (for streaming)."""
    return ChatEvent(type="text-delta", data={"delta": delta})


def text_event(content: str) -> ChatEvent:
    """Complete text response (non-streaming)."""
    return ChatEvent(type="text", data={"content": content})


def plan_event(task_id: str, plan: dict[str, Any]) -> ChatEvent:
    """A plan was generated, awaiting user confirmation."""
    return ChatEvent(type="plan", data={"task_id": task_id, "plan": plan})


def step_progress_event(step_id: str, status: str) -> ChatEvent:
    """A workflow step completed."""
    return ChatEvent(type="step-progress", data={"step_id": step_id, "status": status})


def system_response_event(content: str) -> ChatEvent:
    """Direct system response (no LLM involved)."""
    return ChatEvent(type="system-response", data={"content": content})


def error_event(message: str) -> ChatEvent:
    """An error occurred."""
    return ChatEvent(type="error", data={"message": message})


def done_event(
    tokens: int = 0, model: str = "", cost: float = 0.0
) -> ChatEvent:
    """Response stream is complete."""
    return ChatEvent(
        type="done",
        data={"tokens": tokens, "model": model, "cost": cost},
    )


def session_event(session_id: str, resumed: bool = False) -> ChatEvent:
    """Session created or resumed."""
    return ChatEvent(
        type="session",
        data={"session_id": session_id, "resumed": resumed},
    )


def widget_event(widget: Any) -> ChatEvent:
    """A structured widget for rich channel rendering."""
    from mycelos.widgets import widget_to_dict
    return ChatEvent(type="widget", data={"widget": widget_to_dict(widget)})


def suggested_actions_event(actions: list[dict[str, str]]) -> ChatEvent:
    """Clickable command suggestions for the user.

    Each action has:
        label: Display text for the button
        command: The command/message to send when clicked
    """
    return ChatEvent(type="suggested-actions", data={"actions": actions})
