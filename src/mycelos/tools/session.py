"""Session tools — let the agent name and list sessions."""

from __future__ import annotations

from typing import Any

from mycelos.tools.registry import ToolPermission


SESSION_SET_SCHEMA = {
    "type": "function",
    "function": {
        "name": "session_set",
        "description": (
            "Set the title and/or topic for the current chat session. "
            "Call this early in a conversation to give it a meaningful name "
            "that shows in the session list (instead of a cryptic ID). "
            "Example: session_set(title='Email setup', topic='connectors')"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "description": "Short, descriptive session title (e.g., 'Email setup', 'Weekly news workflow').",
                },
                "topic": {
                    "type": "string",
                    "description": "Topic category (e.g., 'connectors', 'workflows', 'email', 'general').",
                },
            },
        },
    },
}


SESSION_LIST_SCHEMA = {
    "type": "function",
    "function": {
        "name": "session_list",
        "description": (
            "List recent chat sessions with their titles and message counts. "
            "Use this to check what conversations already exist before creating "
            "a new one, or to help the user find a previous conversation."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of sessions to return (default: 10).",
                },
            },
        },
    },
}


def execute_session_set(args: dict, context: dict) -> Any:
    """Set title/topic on the current session."""
    app = context["app"]
    session_id = context.get("session_id")
    if not session_id:
        return {"error": "No active session."}

    title = args.get("title")
    topic = args.get("topic")
    if not title and not topic:
        return {"error": "Provide at least a title or topic."}

    ok = app.session_store.update_session(session_id, title=title, topic=topic)
    if not ok:
        return {"error": f"Session {session_id} not found."}

    result: dict[str, Any] = {"session_id": session_id}
    if title:
        result["title"] = title
    if topic:
        result["topic"] = topic
    return result


def execute_session_list(args: dict, context: dict) -> Any:
    """List recent sessions."""
    app = context["app"]
    limit = args.get("limit", 10)
    sessions = app.session_store.list_sessions()[:limit]
    return {
        "sessions": [
            {
                "session_id": s.get("session_id", ""),
                "title": s.get("title", ""),
                "topic": s.get("topic", ""),
                "message_count": s.get("message_count", 0),
                "timestamp": s.get("timestamp", ""),
            }
            for s in sessions
        ]
    }


def register(registry: Any) -> None:
    """Register session tools."""
    registry.register("session_set", SESSION_SET_SCHEMA, execute_session_set, ToolPermission.STANDARD, category="core")
    registry.register("session_list", SESSION_LIST_SCHEMA, execute_session_list, ToolPermission.OPEN, concurrent_safe=True, category="core")
