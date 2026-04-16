"""AgentHandler Protocol — unified interface for user-facing agents."""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from mycelos.chat.events import ChatEvent


def build_user_context(app: Any) -> str:
    """Build user context block for system prompts (shared by all handlers).

    Includes user name, language preference, and persistent memory entries.
    """
    parts: list[str] = []

    user_name = app.memory.get("default", "system", "user.name")
    user_lang = app.memory.get("default", "system", "user.preference.language") or "en"

    if user_name:
        parts.append(f"## User\nName: {user_name}\nLanguage: {user_lang}")
        parts.append("The user's name is known. Do NOT ask for their name again.")
    else:
        parts.append(
            "## User\nThis is a NEW user (name unknown).\n"
            "Ask their name ONCE at the start, then remember it."
        )

    # Inject persistent memory
    try:
        from mycelos.chat.memory_injection import inject_memory_context
        memory_context = inject_memory_context(app)
        if memory_context:
            parts.append(memory_context)
    except Exception:
        pass

    return "\n\n".join(parts)


@runtime_checkable
class AgentHandler(Protocol):
    """Every user-facing agent implements this interface.

    The active agent for a session is stored in the session_agents table.
    Messages are routed to the active handler — no if-else chains.
    Handoff is a tool that agents call to transfer the conversation.
    """

    @property
    def agent_id(self) -> str:
        """Unique identifier (e.g., 'mycelos', 'creator', 'planner')."""
        ...

    @property
    def display_name(self) -> str:
        """Human-readable name shown to user (e.g., 'Creator-Agent')."""
        ...

    def handle(self, message: str, session_id: str, user_id: str,
               conversation: list[dict]) -> list[ChatEvent]:
        """Process a user message and return response events."""
        ...

    def get_system_prompt(self, context: dict | None = None) -> str:
        """Return the system prompt for this agent."""
        ...

    def get_tools(self) -> list[dict]:
        """Return the tool definitions for this agent (including handoff)."""
        ...


class DynamicAgentHandler:
    """Lightweight handler for custom/persona agents from the registry.

    Created on-the-fly when Mycelos hands off to a custom agent
    that has a system_prompt but no registered AgentHandler class.
    """

    def __init__(self, app: Any, agent_info: dict) -> None:
        self._app = app
        self._info = agent_info

    @property
    def agent_id(self) -> str:
        return self._info["id"]

    @property
    def display_name(self) -> str:
        return self._info.get("display_name") or self._info.get("name", self._info["id"])

    def handle(self, message: str, session_id: str, user_id: str,
               conversation: list[dict]) -> list[ChatEvent]:
        raise NotImplementedError("DynamicAgentHandler uses ChatService tool loop")

    def get_system_prompt(self, context: dict | None = None) -> str:
        prompt = self._info.get("system_prompt", "")
        user_ctx = build_user_context(self._app)
        return prompt + "\n\n" + user_ctx if user_ctx else prompt

    def get_tools(self) -> list[dict]:
        """Return tools based on allowed_tools from registry."""
        import json as _json
        from mycelos.chat.service import CHAT_AGENT_TOOLS

        allowed = self._info.get("allowed_tools")
        if allowed and isinstance(allowed, str):
            try:
                allowed = _json.loads(allowed)
            except (ValueError, TypeError):
                allowed = None

        if not allowed:
            # Persona agents get the standard chat tools
            return list(CHAT_AGENT_TOOLS)

        # Filter tools by allowed list
        from mycelos.tools.registry import ToolRegistry
        ToolRegistry._ensure_initialized()
        tools = []
        for name, entry in ToolRegistry._tools.items():
            if name in allowed:
                tools.append(entry["schema"])
        return tools if tools else list(CHAT_AGENT_TOOLS)
