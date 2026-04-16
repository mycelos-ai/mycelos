"""PlannerAgent — analyzes user intent and creates execution plans.

Responsibilities:
- Parse user intent
- Search for matching workflows (context-based)
- Create new plans when no match found
- Delegate to appropriate agents
"""

from __future__ import annotations

import json
from typing import Any

from mycelos.prompts import PromptLoader


class PlannerAgent:
    """Analyzes user intent and creates execution plans."""

    def __init__(self, llm: Any) -> None:
        self._llm = llm

    def plan(self, user_request: str, context: dict[str, Any]) -> dict[str, Any]:
        """Create a plan for a user request.

        Args:
            user_request: The user's natural language request.
            context: Additional context such as available_agents,
                available_workflows, user_name, etc.

        Returns:
            A dict describing the plan with at minimum an "action" key.
        """
        messages = self._build_messages(user_request, context)
        response = self._llm.complete(messages)

        try:
            result = json.loads(response.content)
        except (json.JSONDecodeError, ValueError):
            result = {
                "action": "execute_workflow",
                "workflow_id": None,
                "steps": [],
                "missing_agents": [],
                "estimated_cost": "unknown",
                "explanation": "",
                "error": "Planner returned invalid JSON",
                "raw_response": response.content,
            }

        # Ensure missing_agents is always present
        if "missing_agents" not in result:
            result["missing_agents"] = []
        if "explanation" not in result:
            result["explanation"] = ""

        return result

    def _build_messages(
        self, user_request: str, context: dict[str, Any]
    ) -> list[dict[str, str]]:
        """Build the message list for the LLM call.

        Args:
            user_request: The user's request text.
            context: Context dict with optional keys like
                available_agents, available_workflows,
                available_capabilities, available_connectors.

        Returns:
            List of message dicts with role and content keys.
        """
        from mycelos.agents.planner_context import format_context_for_prompt

        system_context = (
            format_context_for_prompt(context)
            if context
            else "No system resources available yet."
        )
        system = PromptLoader().load("planner", system_context=system_context)

        return [
            {"role": "system", "content": system},
            {"role": "user", "content": user_request},
        ]
