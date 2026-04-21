"""Connector tools — MCP connector access and GitHub API."""

from __future__ import annotations

from typing import Any

from mycelos.tools.registry import ToolPermission

# --- Tool Schemas ---

CONNECTOR_TOOLS_SCHEMA = {
    "type": "function",
    "function": {
        "name": "connector_tools",
        "description": (
            "List available tools for a specific connector. "
            "Use system_status first to see which connectors are configured, "
            "then use this to discover what a connector can do."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "connector_id": {
                    "type": "string",
                    "description": "Connector ID, e.g. 'github', 'filesystem'",
                },
            },
            "required": ["connector_id"],
        },
    },
}

CONNECTOR_CALL_SCHEMA = {
    "type": "function",
    "function": {
        "name": "connector_call",
        "description": (
            "Call a tool on a connector. First use connector_tools to see "
            "available tools and their parameters, then call the specific tool."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "connector_id": {
                    "type": "string",
                    "description": "Connector ID, e.g. 'github', 'filesystem'",
                },
                "tool": {
                    "type": "string",
                    "description": "Tool name within the connector",
                },
                "args": {
                    "type": "object",
                    "description": "Arguments for the tool",
                },
            },
            "required": ["connector_id", "tool"],
        },
    },
}

SEARCH_MCP_SERVERS_SCHEMA = {
    "type": "function",
    "function": {
        "name": "search_mcp_servers",
        "description": (
            "Search the official MCP Registry for available connectors/tools. "
            "Use this when the user needs a service that isn't installed yet "
            "(e.g., 'I need Notion integration', 'Is there an email MCP server?')."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "What to search for (e.g., 'notion', 'email', 'database')",
                },
            },
            "required": ["query"],
        },
    },
}

GITHUB_API_SCHEMA = {
    "type": "function",
    "function": {
        "name": "github_api",
        "description": (
            "Call GitHub REST API directly for endpoints not covered by "
            "connector_call, especially /user/repos (private repos). "
            "Prefer connector_call('github', ...) for most operations."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "endpoint": {
                    "type": "string",
                    "description": "API path, e.g. '/user/repos', '/user'",
                },
                "method": {
                    "type": "string",
                    "enum": ["GET", "POST", "PATCH"],
                    "default": "GET",
                },
            },
            "required": ["endpoint"],
        },
    },
}


# --- Tool Execution ---

def execute_connector_tools(args: dict, context: dict) -> Any:
    """List available tools for a specific connector."""
    app = context["app"]
    connector_id = args.get("connector_id", "")

    mcp_mgr = getattr(app, "_mcp_manager", None)
    if not mcp_mgr:
        return {"error": "No MCP connectors running. Start with: mycelos serve"}

    prefix = f"{connector_id}."
    tools = []
    for tool in mcp_mgr.list_tools():
        if tool["name"].startswith(prefix):
            short_name = tool["name"][len(prefix):]
            schema = tool.get("input_schema", {})
            if isinstance(schema, dict):
                schema = {k: v for k, v in schema.items() if k != "$schema"}
            params = schema.get("properties", {})
            required = schema.get("required", [])
            tools.append({
                "name": short_name,
                "description": tool.get("description", ""),
                "parameters": {
                    k: v.get("description", v.get("type", "?"))
                    for k, v in params.items()
                },
                "required": required,
            })

    if not tools:
        connected = mcp_mgr.list_connected()
        if connector_id not in connected:
            return {
                "error": f"Connector '{connector_id}' is not running.",
                "available_connectors": connected,
            }
        return {"connector": connector_id, "tools": [], "message": "No tools discovered."}

    return {"connector": connector_id, "tool_count": len(tools), "tools": tools}


def execute_connector_call(args: dict, context: dict) -> Any:
    """Call a tool on a specific connector via MCP."""
    app = context["app"]
    connector_id = args.get("connector_id", "")
    tool_name = args.get("tool", "")
    tool_args = args.get("args", {})

    mcp_mgr = getattr(app, "_mcp_manager", None)
    if not mcp_mgr:
        return {"error": "No MCP connectors running."}

    mcp_tool_name = f"{connector_id}.{tool_name}"

    # Security: per-tool policy check (F8)
    user_id = context.get("user_id", "default")
    decision = app.policy_engine.evaluate(user_id, None, mcp_tool_name)
    if decision == "never":
        app.audit.log("tool.blocked", details={"tool": mcp_tool_name, "reason": "policy:never"})
        return {"error": f"Tool '{mcp_tool_name}' is blocked by policy."}

    # Verify the tool exists
    available = {t["name"] for t in mcp_mgr.list_tools()}
    if mcp_tool_name not in available:
        prefix = f"{connector_id}."
        similar = [
            t["name"][len(prefix):]
            for t in mcp_mgr.list_tools()
            if t["name"].startswith(prefix)
        ]
        return {
            "error": f"Tool '{tool_name}' not found on connector '{connector_id}'.",
            "available_tools": similar[:10],
        }

    try:
        result = mcp_mgr.call_tool(mcp_tool_name, tool_args or {})
    except Exception as e:
        # Record failure for Doctor / Connectors UI so "when did this
        # last fail?" is answerable without grepping audit_events.
        try:
            app.connector_registry.record_failure(connector_id, str(e))
        except Exception:
            pass
        raise

    # Treat a structured {"error": ...} payload as a failure too — many
    # MCP tools return errors without raising.
    if isinstance(result, dict) and result.get("error"):
        try:
            app.connector_registry.record_failure(connector_id, str(result["error"]))
        except Exception:
            pass
    else:
        try:
            app.connector_registry.record_success(connector_id)
        except Exception:
            pass

    app.audit.log("tool.executed", details={
        "tool": mcp_tool_name,
        "user_id": user_id,
        "source": "mcp",
    })

    return result


def execute_search_mcp_servers(args: dict, context: dict) -> Any:
    """Search the MCP Registry for connectors."""
    from mycelos.connectors.mcp_search import search_mcp_servers

    results = search_mcp_servers(args.get("query", ""), limit=5)
    if not results:
        return {"results": [], "message": "No MCP servers found. Try a different search term."}
    return {
        "results": [
            {
                "name": r["name"],
                "description": r["description"],
                "repository": r.get("repository", ""),
                "install": (
                    f"npx -y {r['packages'][0]['name']}"
                    if r.get("packages") and r["packages"][0].get("registry") == "npm"
                    else r["packages"][0]["name"]
                    if r.get("packages")
                    else ""
                ),
            }
            for r in results
        ]
    }


def execute_github_api(args: dict, context: dict) -> Any:
    """Call GitHub REST API directly."""
    from mycelos.connectors.github_tools import github_api

    app = context["app"]
    return github_api(
        endpoint=args.get("endpoint", "/user"),
        credential_proxy=app.credentials,
        method=args.get("method", "GET"),
        body=args.get("body"),
    )


# --- Registration ---

def register(registry: type) -> None:
    """Register all connector tools."""
    registry.register("connector_tools", CONNECTOR_TOOLS_SCHEMA, execute_connector_tools, ToolPermission.PRIVILEGED, concurrent_safe=True, category="connectors")
    registry.register("connector_call", CONNECTOR_CALL_SCHEMA, execute_connector_call, ToolPermission.PRIVILEGED, category="connectors")
    registry.register("search_mcp_servers", SEARCH_MCP_SERVERS_SCHEMA, execute_search_mcp_servers, ToolPermission.STANDARD, concurrent_safe=True, category="connectors")
    registry.register("github_api", GITHUB_API_SCHEMA, execute_github_api, ToolPermission.PRIVILEGED, category="connectors")
