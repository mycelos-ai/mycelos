"""MycelosHandler — the default chat agent handler.

Wraps the existing Mycelos system prompt and CHAT_AGENT_TOOLS with the
handoff tool so the agent can transfer conversations to specialist agents.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from mycelos.chat.events import ChatEvent
from mycelos.chat.service import CHAT_AGENT_TOOLS  # noqa: F401 — CHAT_AGENT_TOOLS is a lazy proxy
from mycelos.prompts import PromptLoader
from mycelos.tools.registry import ToolRegistry

if TYPE_CHECKING:
    from mycelos.app import App

# Fallback list of user-facing agent IDs if the registry cannot be queried
_FALLBACK_USER_FACING_AGENTS = ["builder"]

_HANDOFF_RULES_BASE = """
## Handoff Rules (Agent Routing)

You are the primary interface. You can route to specialist agents:

### Build Requests
- User wants to **build something** (agent, workflow, automation, scraper,
  recurring task, integration) → handoff to "builder"

### Custom Agent / Persona Routing
{custom_agent_routing}

### What to handle yourself
- **Simple tasks** (notes, quick web search, file read/write, short questions,
  managing preferences) → handle yourself, do NOT hand off

### How to hand off
1. Briefly tell the user you're connecting them with the right agent.
2. Call the `handoff` tool with the target agent ID and a clear reason.
3. Do NOT attempt to build agents, workflows, or complex automations yourself.
"""


def _build_handoff_tool(target_agents: list[str]) -> dict:
    """Build the handoff tool definition with a dynamic target_agent enum."""
    return {
        "type": "function",
        "function": {
            "name": "handoff",
            "description": (
                "Transfer the conversation to another agent. "
                "Use for: build requests (→ builder), persona conversations "
                "(→ persona agent by name), or specialist tasks "
                "(→ specialist agent matching the user's need)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "target_agent": {
                        "type": "string",
                        "enum": target_agents,
                        "description": "The agent ID to hand off to.",
                    },
                    "reason": {
                        "type": "string",
                        "description": (
                            "Why the handoff is needed. Shown to the receiving agent "
                            "as context for the request."
                        ),
                    },
                    "summary": {
                        "type": "string",
                        "description": (
                            "Short summary of the conversation so far (1-3 sentences). "
                            "Helps the receiving agent quickly understand the context."
                        ),
                    },
                },
                "required": ["target_agent", "reason"],
            },
        },
    }


def _get_user_facing_agents(app: Any) -> list[str]:
    """Return IDs of user-facing agents from the registry, falling back to defaults."""
    try:
        rows = app.storage.fetchall(
            "SELECT id FROM agents WHERE user_facing = 1 AND status = 'active' AND id != 'mycelos'"
        )
        ids = [r["id"] for r in rows if r["id"]]
        return ids if ids else _FALLBACK_USER_FACING_AGENTS
    except Exception:
        return _FALLBACK_USER_FACING_AGENTS


def _build_custom_agents_context(app: Any) -> tuple[str, list[dict]]:
    """Build routing context for custom agents and personas.

    Returns:
        Tuple of (routing_rules_text, list_of_agent_info_dicts)
    """
    try:
        all_agents = app.agent_registry.list_agents(status="active")
    except Exception:
        return ("No custom agents available.", [])

    # System agents are not routable custom agents
    system_ids = {"mycelos", "builder", "workflow-agent", "evaluator-agent", "auditor-agent"}
    custom = [a for a in all_agents if a["id"] not in system_ids]

    if not custom:
        return ("No custom agents available yet. If the user wants one, hand off to builder.", [])

    lines = []
    agent_infos = []
    for agent in custom:
        agent_id = agent["id"]
        name = agent.get("name", agent_id)
        prompt = agent.get("system_prompt", "")
        caps = agent.get("capabilities", [])
        created_by = agent.get("created_by", "")
        is_persona = bool(prompt) and created_by != "creator-agent"

        # Build a short description from the prompt or capabilities
        if prompt:
            # First sentence of system prompt as description
            desc = prompt.split(".")[0].strip()[:100]
        elif caps:
            desc = f"Capabilities: {', '.join(caps[:5])}"
        else:
            desc = name

        agent_type = "persona" if is_persona else "specialist"
        lines.append(f"- **{name}** (`{agent_id}`) — {agent_type}: {desc}")

        agent_infos.append({
            "id": agent_id, "name": name, "type": agent_type,
            "description": desc, "is_persona": is_persona,
        })

    routing = "\n".join(lines)
    routing += "\n\n**IMPORTANT: Always prefer custom agents over your built-in tools.**"
    routing += "\nIf a specialist agent exists for a task (e.g., PDF extraction), hand off to it"
    routing += "\ninstead of using your own tools. The specialist was built specifically for this."
    routing += "\nFor persona agents: the user may ask by name (e.g., 'talk to Stella')."

    return (routing, agent_infos)


def _build_mcp_connectors_context(app: Any) -> str:
    """Build context listing ALL active connectors (MCP + channels + builtins).

    The MCP list alone misses channel-type connectors like Telegram — which
    means Mycelos wrongly claims Telegram is not set up even when it is.
    Always enumerate the connector_registry and annotate what each connector
    is capable of so the chat agent can mention it in its replies.
    """
    lines: list[str] = []

    # 1) Channel / builtin / MCP connectors from the registry
    try:
        active = app.connector_registry.list_connectors(status="active") or []
    except Exception:
        active = []

    # 2) MCP tool discovery (if the MCP manager is running)
    mcp_mgr = getattr(app, "_mcp_manager", None)
    mcp_connected = set()
    if mcp_mgr:
        try:
            mcp_connected = set(mcp_mgr.list_connected())
        except Exception:
            mcp_connected = set()

    if not active and not mcp_connected:
        return ""

    lines.append("## Active Connectors")
    lines.append(
        "These connectors are CONFIGURED AND READY. Do NOT tell the user they need to set "
        "them up — they already did. Use them directly."
    )

    seen: set[str] = set()
    for c in active:
        cid = c.get("id", "")
        if not cid:
            continue
        seen.add(cid)
        ctype = c.get("type", "")
        desc = c.get("description") or ""
        if cid in mcp_connected and mcp_mgr:
            tools = [t for t in mcp_mgr.list_tools() if t["name"].startswith(f"{cid}.")]
            tool_names = [t["name"].split(".", 1)[1] for t in tools[:8]]
            more = f" (+{len(tools) - 8} more)" if len(tools) > 8 else ""
            lines.append(
                f"- **{cid}** ({ctype}) — {len(tools)} tools via connector_tools/connector_call: "
                f"{', '.join(tool_names)}{more}"
            )
        elif ctype == "channel":
            # Channel connectors (Telegram, Slack, ...) — handled by the channel layer,
            # no MCP tools, but the user can still *receive* messages here and Mycelos
            # can push notifications through pending_reminder / memory.
            lines.append(
                f"- **{cid}** (channel) — the user reaches you via this channel. "
                f"You can deliver reminders/replies through it (set pending_reminder or use "
                f"the channel tools if available). {desc}"
            )
        else:
            lines.append(f"- **{cid}** ({ctype}) — {desc}")

    # MCP connectors not in the registry (edge case)
    for cid in mcp_connected - seen:
        tools = [t for t in mcp_mgr.list_tools() if t["name"].startswith(f"{cid}.")]
        tool_names = [t["name"].split(".", 1)[1] for t in tools[:8]]
        lines.append(f"- **{cid}** (mcp) — {len(tools)} tools: {', '.join(tool_names)}")

    lines.append("")
    lines.append(
        "Usage for MCP: `connector_tools(connector_id)` → discover tools, then "
        "`connector_call(connector_id, tool, args)` → execute."
    )
    return "\n".join(lines)


def _build_paused_agents_context(app: Any) -> str:
    """Build a context block describing paused/developing agents, if any."""
    try:
        rows = app.storage.fetchall(
            "SELECT id, name FROM agents WHERE user_facing = 1 AND status = 'proposed'"
        )
        if not rows:
            return ""
        lines = [f"  - {r['id']} ({r['name']}): still in development" for r in rows]
        return "\n\n## Agents in Development\n" + "\n".join(lines)
    except Exception:
        return ""


class MycelosHandler:
    """Default chat agent — the primary interface for all user interaction.

    Provides the full Mycelos system prompt enriched with handoff routing rules
    and the standard CHAT_AGENT_TOOLS plus the `handoff` tool.

    `handle()` is intentionally not implemented here; it will be wired in Task 5
    via the HandlerDispatcher which delegates to the existing ChatService.
    """

    def __init__(self, app: "App") -> None:
        self._app = app

    @property
    def agent_id(self) -> str:
        return "mycelos"

    @property
    def display_name(self) -> str:
        """Return the user-chosen display_name from the agents row, or 'Mycelos'."""
        try:
            row = self._app.agent_registry.get("mycelos")
            if row and row.get("display_name"):
                return row["display_name"]
        except Exception:
            pass
        return "Mycelos"

    def handle(
        self,
        message: str,
        session_id: str,
        user_id: str,
        conversation: list[dict],
    ) -> list[ChatEvent]:
        raise NotImplementedError(
            "MycelosHandler.handle() will be wired in Task 5 via HandlerDispatcher."
        )

    def get_system_prompt(self, context: dict | None = None) -> str:
        """Return the full dynamic Mycelos system prompt.

        All dynamic context is injected via {placeholders} in mycelos.md.
        build_prompt_variables() gathers everything; the template decides
        what to include.
        """
        from mycelos.prompts import build_prompt_variables

        variables = build_prompt_variables(self._app)
        return PromptLoader().load("mycelos", **variables)

    def get_tools(self, channel: str = "api") -> list[dict]:
        """Return chat tools + handoff + custom agent tools.

        Tool list is channel- and connector-aware:
        - Web-UI widgets (show_*) only on channel="api"
        - Email tools only when email connector is active
        - Builder-only tools (create_workflow) excluded — Builder has its own

        Code agents are exposed as direct tools (run_agent_<id>).
        Persona agents are added to the handoff enum.
        """
        from mycelos.chat.service import _get_chat_agent_tools

        target_agents = _get_user_facing_agents(self._app)

        _, custom_infos = _build_custom_agents_context(self._app)
        tools: list[dict] = []

        for info in custom_infos:
            if info["is_persona"]:
                # Persona → handoff target
                if info["id"] not in target_agents:
                    target_agents.append(info["id"])
            else:
                # Code agent → direct tool
                tools.append(_build_agent_tool(info))

        handoff_tool = _build_handoff_tool(target_agents)
        return _get_chat_agent_tools(self._app, channel=channel) + tools + [handoff_tool]


def _build_agent_tool(agent_info: dict) -> dict:
    """Build a tool definition that invokes a custom code agent."""
    agent_id = agent_info["id"]
    name = agent_info["name"]
    desc = agent_info.get("description", name)
    return {
        "type": "function",
        "function": {
            "name": f"run_agent_{agent_id.replace('-', '_')}",
            "description": f"Run the '{name}' specialist agent. {desc}",
            "parameters": {
                "type": "object",
                "properties": {
                    "task": {
                        "type": "string",
                        "description": "What to do (e.g., file path, query, or instructions)",
                    },
                    "inputs": {
                        "type": "object",
                        "description": "Additional structured inputs for the agent",
                    },
                },
                "required": ["task"],
            },
        },
    }
