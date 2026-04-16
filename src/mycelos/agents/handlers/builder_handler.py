"""BuilderHandler — unified specialist that creates workflows AND agents.

Replaces the separate Creator and Planner agents with one that:
1. First checks if existing tools/workflows can solve the request (→ workflow)
2. Searches MCP Registry if external services are needed
3. Only writes custom agent code if no simpler solution exists (→ create_agent)

The user doesn't think in "workflow vs agent" — they just want something built.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from mycelos.agents.planner_context import build_planner_context, format_context_for_prompt
from mycelos.chat.events import ChatEvent
from mycelos.prompts import PromptLoader
from mycelos.tools.workflow import (
    CREATE_SCHEDULE_SCHEMA,
    DELETE_SCHEDULE_SCHEMA,
    LIST_SCHEDULES_SCHEMA,
    UPDATE_WORKFLOW_SCHEMA,
    WORKFLOW_INFO_SCHEMA,
)

if TYPE_CHECKING:
    from mycelos.app import App


# Tool definitions — merged from Creator + Planner

_CREATE_WORKFLOW_TOOL = {
    "type": "function",
    "function": {
        "name": "create_workflow",
        "description": (
            "Create a new workflow definition. A workflow is an LLM-powered agent "
            "that executes a plan using scoped tools. You MUST provide plan, inputs, "
            "model, and allowed_tools."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "workflow_id": {
                    "type": "string",
                    "description": "Unique ID in kebab-case (e.g., 'daily-news-summary').",
                },
                "name": {
                    "type": "string",
                    "description": "Human-readable name.",
                },
                "description": {
                    "type": "string",
                    "description": "What the workflow does (1-2 sentences).",
                },
                "goal": {
                    "type": "string",
                    "description": "Desired outcome when workflow completes.",
                },
                "plan": {
                    "type": "string",
                    "description": (
                        "Detailed instructions for the LLM agent (system prompt). "
                        "Include: what tools to call, in what order, how to handle errors, "
                        "output format. Be specific."
                    ),
                },
                "inputs": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "type": {"type": "string"},
                            "required": {"type": "boolean"},
                            "description": {"type": "string"},
                        },
                        "required": ["name", "type", "description"],
                    },
                    "description": "Input parameters the workflow expects.",
                },
                "model": {
                    "type": "string",
                    "enum": ["haiku", "sonnet", "opus"],
                    "description": "LLM tier. haiku for simple, sonnet for complex.",
                },
                "allowed_tools": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Tools the agent can use (e.g., 'search_web', 'http_get', 'playwright.*').",
                },
                "tags": {"type": "array", "items": {"type": "string"}},
                "scope": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["workflow_id", "name", "description", "plan", "allowed_tools"],
        },
    },
}

_CREATE_AGENT_TOOL = {
    "type": "function",
    "function": {
        "name": "create_agent",
        "description": (
            "Build, test, audit, and register a new custom agent. ONLY use this "
            "when existing tools and workflows cannot handle the task. The pipeline "
            "runs automatically: gherkin → tests → code → audit → register."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Agent name in kebab-case.",
                },
                "description": {
                    "type": "string",
                    "description": "What the agent does.",
                },
                "capabilities": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Required capabilities (e.g., 'filesystem.read').",
                },
                "input_format": {"type": "string"},
                "output_format": {"type": "string"},
                "trigger": {
                    "type": "string",
                    "enum": ["on_demand", "scheduled"],
                },
                "dependencies": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "External Python packages the agent needs "
                        "(e.g., ['pdfplumber', 'pandas']). "
                        "The user will be asked to approve installation."
                    ),
                },
            },
            "required": ["name", "description"],
        },
    },
}

_LIST_TOOLS_TOOL = {
    "type": "function",
    "function": {
        "name": "list_tools",
        "description": "List all available tools, agents, connectors, and workflows.",
        "parameters": {"type": "object", "properties": {}},
    },
}

_SEARCH_MCP_TOOL = {
    "type": "function",
    "function": {
        "name": "search_mcp_servers",
        "description": (
            "Search the MCP Registry for connectors (browser, email, database, etc.). "
            "Use when the solution needs an external service not currently available."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search term."},
            },
            "required": ["query"],
        },
    },
}

_HANDOFF_TOOL = {
    "type": "function",
    "function": {
        "name": "handoff",
        "description": "Transfer control back to Mycelos when done or user cancels.",
        "parameters": {
            "type": "object",
            "properties": {
                "target_agent": {
                    "type": "string",
                    "enum": ["mycelos"],
                    "description": "Always 'mycelos' from Builder.",
                },
                "reason": {"type": "string"},
                "summary": {"type": "string"},
            },
            "required": ["target_agent", "reason"],
        },
    },
}

_NOTE_WRITE_TOOL = {
    "type": "function",
    "function": {
        "name": "note_write",
        "description": "Save a plan, decision, or progress note to the knowledge base.",
        "parameters": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "content": {"type": "string"},
            },
            "required": ["title", "content"],
        },
    },
}

_SEARCH_WEB_TOOL = {
    "type": "function",
    "function": {
        "name": "search_web",
        "description": "Search the web for information needed to build the solution.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "max_results": {"type": "integer", "default": 5},
            },
            "required": ["query"],
        },
    },
}


class BuilderHandler:
    """Unified specialist — builds workflows and agents.

    Prefers workflows over custom code. Only creates agents when
    existing tools genuinely can't handle the task.
    """

    def __init__(self, app: "App") -> None:
        self._app = app

    @property
    def agent_id(self) -> str:
        return "builder"

    @property
    def display_name(self) -> str:
        return "Builder-Agent"

    def handle(
        self,
        message: str,
        session_id: str,
        user_id: str,
        conversation: list[dict],
    ) -> list[ChatEvent]:
        raise NotImplementedError("BuilderHandler.handle() — dispatch via ChatService")

    def get_system_prompt(self, context: dict | None = None) -> str:
        """Return the Builder prompt with dynamic system state.

        All dynamic context is injected via {placeholders} in builder.md.
        build_prompt_variables() gathers everything; the template decides
        what to include.
        """
        from mycelos.prompts import build_prompt_variables

        variables = build_prompt_variables(self._app)
        return PromptLoader().load("builder", **variables)

    def get_tools(self) -> list[dict]:
        return [
            _CREATE_WORKFLOW_TOOL,
            UPDATE_WORKFLOW_SCHEMA,
            WORKFLOW_INFO_SCHEMA,
            CREATE_SCHEDULE_SCHEMA,
            DELETE_SCHEDULE_SCHEMA,
            LIST_SCHEDULES_SCHEMA,
            _CREATE_AGENT_TOOL,
            _LIST_TOOLS_TOOL,
            _SEARCH_MCP_TOOL,
            _HANDOFF_TOOL,
            _NOTE_WRITE_TOOL,
            _SEARCH_WEB_TOOL,
        ]
