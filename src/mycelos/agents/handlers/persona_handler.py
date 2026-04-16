"""PersonaHandler — generic agent handler that reads prompt/tools/model from DB.

Enables user-created "persona agents" without Python code.
The agent's system_prompt, allowed_tools, and model are stored in the
agents table and loaded dynamically.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from mycelos.agents.handlers.base import AgentHandler
from mycelos.tools.registry import ToolRegistry

logger = logging.getLogger("mycelos.agents.persona")


class PersonaHandler(AgentHandler):
    """Agent handler that loads identity from the database."""

    def __init__(self, app: Any, agent_id: str, agent_data: dict):
        self._app = app
        self._agent_id = agent_id
        self._data = agent_data

    @property
    def agent_id(self) -> str:
        return self._agent_id

    @property
    def display_name(self) -> str:
        return self._data.get("display_name") or self._data.get("name", self._agent_id)

    def get_system_prompt(self, context: dict | None = None) -> str:
        """Return the system prompt from the DB, enriched with user context."""
        from mycelos.agents.handlers.base import build_user_context

        base_prompt = self._data.get("system_prompt", "")
        if not base_prompt:
            base_prompt = f"You are {self.display_name}, a specialist agent in Mycelos."

        # User-facing agents: add interaction rules
        if self._data.get("user_facing"):
            base_prompt += "\n\n" + (
                "## Interaction Rules\n"
                "- Speak in the user's language.\n"
                "- If a tool call fails or a capability is missing, ASK the user "
                "if you should request it — don't just say you can't do it.\n"
                "- If you need a connector that isn't installed, suggest the setup command.\n"
                "- Be helpful and proactive, not apologetic."
            )

        # Add user context (name, language, memory)
        user_ctx = build_user_context(self._app)
        return base_prompt + "\n\n" + user_ctx

    def get_tools(self) -> list[dict]:
        """Return only the tools this persona is allowed to use."""
        allowed_str = self._data.get("allowed_tools", "[]")
        try:
            allowed_names = json.loads(allowed_str) if isinstance(allowed_str, str) else allowed_str
        except (json.JSONDecodeError, TypeError):
            allowed_names = []

        if not allowed_names:
            # No restriction — get all tools for mycelos agent type
            return ToolRegistry.get_tools_for("mycelos")

        # Filter to only allowed tools
        all_tools = ToolRegistry.get_tools_for("mycelos")
        return [t for t in all_tools if t["function"]["name"] in allowed_names]

    def handle(self, message: str, context: dict) -> list:
        """Persona agents are handled by the ChatService tool loop, not directly."""
        raise NotImplementedError(
            "PersonaHandler.handle() — routed through ChatService"
        )


def load_persona_handlers(app: Any) -> dict[str, PersonaHandler]:
    """Load all user-facing agents from the DB as PersonaHandlers.

    Returns a dict of agent_id → PersonaHandler for agents that have
    user_facing=1 and a system_prompt set.
    """
    try:
        rows = app.storage.fetchall(
            "SELECT * FROM agents WHERE user_facing = 1 AND status = 'active'"
        )
    except Exception:
        return {}

    handlers = {}
    for row in rows:
        agent_id = row["id"]
        # Skip system agents — they have their own handlers
        if agent_id in ("mycelos", "builder"):
            continue
        if row.get("system_prompt"):
            handlers[agent_id] = PersonaHandler(app, agent_id, dict(row))
            logger.info("Loaded persona agent: %s (%s)", agent_id, row.get("name"))

    return handlers
