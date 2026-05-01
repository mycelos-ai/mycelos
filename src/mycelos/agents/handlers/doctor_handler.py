"""DoctorHandler — diagnostic specialist agent.

A system agent (like Builder) that runs a multi-turn diagnostic dialogue.
The user describes a symptom; Doctor investigates with read-only tools,
asks focused questions, and proposes one hypothesis at a time.

Replaces the single-shot DoctorAgent in mycelos.doctor.agent — that one
runs once and stops, which dead-ends as soon as the first guess is wrong.
This one stays in conversation until the user confirms the fix worked.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from mycelos.chat.events import ChatEvent
from mycelos.prompts import PromptLoader, build_prompt_variables
from mycelos.tools.doctor import (
    DOCTOR_CHECK_CREDENTIALS_SCHEMA,
    DOCTOR_CHECK_REMINDERS_SCHEMA,
    DOCTOR_CHECK_SCHEDULES_SCHEMA,
    DOCTOR_CHECK_TELEGRAM_SCHEMA,
    DOCTOR_CONFIG_HISTORY_SCHEMA,
    DOCTOR_QUERY_AUDIT_SCHEMA,
)

if TYPE_CHECKING:
    from mycelos.app import App


_HANDOFF_TOOL = {
    "type": "function",
    "function": {
        "name": "handoff",
        "description": (
            "Transfer control out of the diagnostic conversation. "
            "Use 'mycelos' when the user is done diagnosing or the topic shifted; "
            "use 'builder' when the diagnosis revealed a missing automation that "
            "needs to be built."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "target_agent": {
                    "type": "string",
                    "enum": ["mycelos", "builder"],
                },
                "reason": {"type": "string"},
                "summary": {
                    "type": "string",
                    "description": "1-3 sentence summary of what was diagnosed.",
                },
            },
            "required": ["target_agent", "reason"],
        },
    },
}

_NOTE_READ_TOOL_NAMES = ("note_read", "note_search")


class DoctorHandler:
    """Diagnostic specialist — stays in dialogue until the symptom is resolved."""

    def __init__(self, app: "App") -> None:
        self._app = app

    @property
    def agent_id(self) -> str:
        return "doctor"

    @property
    def display_name(self) -> str:
        return "Doctor-Agent"

    def handle(
        self,
        message: str,
        session_id: str,
        user_id: str,
        conversation: list[dict],
    ) -> list[ChatEvent]:
        raise NotImplementedError("DoctorHandler.handle() — dispatch via ChatService")

    def get_system_prompt(self, context: dict | None = None) -> str:
        variables = build_prompt_variables(self._app)
        return PromptLoader().load("doctor", **variables)

    def get_tools(self) -> list[dict]:
        from mycelos.tools.registry import ToolRegistry

        ToolRegistry._ensure_initialized()
        tools: list[dict] = [
            DOCTOR_CHECK_TELEGRAM_SCHEMA,
            DOCTOR_CHECK_REMINDERS_SCHEMA,
            DOCTOR_CHECK_SCHEDULES_SCHEMA,
            DOCTOR_CHECK_CREDENTIALS_SCHEMA,
            DOCTOR_QUERY_AUDIT_SCHEMA,
            DOCTOR_CONFIG_HISTORY_SCHEMA,
        ]
        for name in _NOTE_READ_TOOL_NAMES:
            schema = ToolRegistry.get_schema(name)
            if schema:
                tools.append(schema)
        tools.append(_HANDOFF_TOOL)
        return tools
