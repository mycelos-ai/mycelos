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
        parts.append(
            "The user's name is known — do NOT ask for it again. "
            f"You are talking WITH {user_name}, not ABOUT them. Always address "
            "them directly in the second person ('you' in English, 'du' in "
            f"German). Never refer to the user in the third person ('{user_name} "
            f"asked…', 'they want…'). Use their name sparingly and only when "
            "greeting or when it adds warmth — every sentence does not need it."
        )
    else:
        parts.append(
            "## User\nThis is a NEW user (name unknown).\n"
            "Ask their name ONCE at the start, then remember it. "
            "Always address the user directly in the second person — never in "
            "the third person."
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
