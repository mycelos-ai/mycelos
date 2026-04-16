"""System tools — status and tool listing."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from mycelos.tools.registry import ToolPermission

# --- Tool Schemas ---

SYSTEM_STATUS_SCHEMA = {
    "type": "function",
    "function": {
        "name": "system_status",
        "description": (
            "Get current system status: active connectors, mounts, agents, "
            "capabilities, and scheduled tasks. Use this to check what's configured "
            "before suggesting setup commands."
        ),
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },
}

LIST_TOOLS_SCHEMA = {
    "type": "function",
    "function": {
        "name": "list_tools",
        "description": "List all available tools, agents, connectors, and workflows.",
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },
}


# --- Tool Execution ---

def execute_system_status(args: dict, context: dict) -> Any:
    """Get current system status -- connectors, mounts, agents, capabilities."""
    app = context["app"]
    status: dict[str, Any] = {}

    # Active connectors with status (builtin vs MCP)
    try:
        from mycelos.connectors.mcp_recipes import get_recipe

        connectors = app.connector_registry.list_connectors(status="active")
        mcp_mgr = getattr(app, "_mcp_manager", None)
        mcp_connected = set(mcp_mgr.list_connected()) if mcp_mgr else set()

        connector_list = []
        for c in connectors:
            cid = c["id"]
            recipe = get_recipe(cid)
            is_builtin = recipe and recipe.transport == "builtin"
            is_channel = recipe and recipe.transport == "channel"

            info: dict[str, Any] = {
                "id": cid,
                "name": c["name"],
                "capabilities": c["capabilities"],
            }

            if is_builtin or is_channel:
                # Builtin connectors (email, etc.) — check credentials instead of MCP
                has_creds = False
                try:
                    cred = app.credentials.get_credential(cid)
                    has_creds = bool(cred and cred.get("api_key"))
                except Exception:
                    pass
                info["type"] = "channel" if is_channel else "builtin"
                info["ready"] = has_creds
                info["status"] = "ready" if has_creds else "credentials missing"
            else:
                # MCP connectors — check if server process is running
                is_running = cid in mcp_connected
                tool_count = sum(
                    1
                    for t in (mcp_mgr.list_tools() if mcp_mgr else [])
                    if t["name"].startswith(f"{cid}.")
                ) if is_running else 0
                info["type"] = "mcp"
                info["ready"] = is_running
                info["status"] = f"running ({tool_count} tools)" if is_running else "starts on demand"
                info["mcp_tools"] = tool_count

            connector_list.append(info)
        status["connectors"] = connector_list
    except Exception:
        status["connectors"] = []

    # Active mounts
    try:
        from mycelos.security.mounts import MountRegistry

        mounts = MountRegistry(app.storage)
        status["mounts"] = [
            {
                "path": m["path"],
                "access": m["access"],
                "agent_id": m.get("agent_id"),
                "workflow_id": m.get("workflow_id"),
            }
            for m in mounts.list_mounts()
        ]
    except Exception:
        status["mounts"] = []

    # Active agents
    try:
        agents = app.agent_registry.list_agents(status="active")
        status["agents"] = [
            {"id": a["id"], "name": a["name"], "capabilities": a["capabilities"]}
            for a in agents
        ]
    except Exception:
        status["agents"] = []

    # Scheduled tasks
    try:
        tasks = app.schedule_manager.list_tasks(status="active")
        status["scheduled_tasks"] = [
            {"id": t["id"][:8], "workflow": t["workflow_id"], "schedule": t["schedule"]}
            for t in tasks
        ]
    except Exception:
        status["scheduled_tasks"] = []

    # Workflows
    try:
        workflows = app.workflow_registry.list_workflows(status="active")
        status["workflows"] = [w["id"] for w in workflows]
    except Exception:
        status["workflows"] = []

    return status


def execute_list_tools(args: dict, context: dict) -> Any:
    """List all available tools, agents, connectors, and workflows."""
    from mycelos.tools.registry import ToolRegistry

    app = context["app"]
    tools_info: dict[str, Any] = {}

    # Available chat tools
    tools_info["chat_tools"] = ToolRegistry.get_all_tool_names()

    # Registered agents
    try:
        agents = app.agent_registry.list_agents()
        tools_info["agents"] = [
            {"id": a["id"], "name": a["name"], "capabilities": a.get("capabilities", [])}
            for a in agents
        ]
    except Exception:
        tools_info["agents"] = []

    # Registered workflows
    try:
        workflows = app.workflow_registry.list_workflows()
        tools_info["workflows"] = [
            {"id": w["id"], "name": w["name"], "description": w.get("description", "")}
            for w in workflows
        ]
    except Exception:
        tools_info["workflows"] = []

    # Connectors
    try:
        connectors = app.connector_registry.list_connectors()
        tools_info["connectors"] = [
            {"id": c["id"], "name": c["name"], "capabilities": c.get("capabilities", [])}
            for c in connectors
        ]
    except Exception:
        tools_info["connectors"] = []

    # Workflow templates (from artifacts/workflows/*.yaml)
    try:
        import yaml
        templates_dir = Path(__file__).parent.parent.parent.parent / "artifacts" / "workflows"
        if not templates_dir.exists():
            templates_dir = app.data_dir / "workflows"
        templates = []
        if templates_dir.exists():
            for yf in sorted(templates_dir.glob("*.yaml")):
                try:
                    data = yaml.safe_load(yf.read_text())
                    templates.append({
                        "id": data.get("name", yf.stem),
                        "description": data.get("description", ""),
                        "requires": data.get("requires", {}),
                        "tags": data.get("tags", []),
                    })
                except Exception:
                    pass
        tools_info["workflow_templates"] = templates
    except Exception:
        tools_info["workflow_templates"] = []

    return tools_info


# --- Registration ---

def register(registry: type) -> None:
    """Register all system tools."""
    registry.register("system_status", SYSTEM_STATUS_SCHEMA, execute_system_status, ToolPermission.OPEN, concurrent_safe=True, category="system")
    registry.register("list_tools", LIST_TOOLS_SCHEMA, execute_list_tools, ToolPermission.OPEN, concurrent_safe=True, category="system")
