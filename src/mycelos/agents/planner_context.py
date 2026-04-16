"""Planner Context Builder — provides system state for informed planning.

Reads agents, workflows, connectors, and capabilities from the live DB
so the Planner can match requests to existing resources or identify gaps.
"""

from __future__ import annotations

from typing import Any


def build_planner_context(app: Any) -> dict[str, Any]:
    """Build rich context for the PlannerAgent from live DB state.

    Includes:
    - Active agents with their capabilities
    - Active workflows with their scope
    - Available capabilities from connectors
    - Connector names

    Args:
        app: The Mycelos App instance.

    Returns:
        Dict with available_agents, available_workflows,
        available_capabilities, available_connectors.
    """
    return {
        "available_agents": _get_agents(app),
        "available_workflows": _get_workflows(app),
        "available_capabilities": _get_capabilities(app),
        "available_connectors": _get_connectors(app),
    }


def format_context_for_prompt(context: dict[str, Any]) -> str:
    """Format the planner context as a readable string for the LLM prompt.

    Args:
        context: The context dict from build_planner_context.

    Returns:
        Formatted multi-line string for inclusion in the system prompt.
    """
    parts: list[str] = []

    # Agents
    agents = context.get("available_agents", [])
    if agents:
        lines = []
        for a in agents:
            if isinstance(a, dict):
                caps = ", ".join(a.get("capabilities", [])) or "none"
                lines.append(f"  - {a['id']} ({a['type']}): {caps}")
            else:
                # Backward compat: plain string agent names
                lines.append(f"  - {a}")
        parts.append("### Registered Agents\n" + "\n".join(lines))
    else:
        parts.append("### Registered Agents\n  No custom agents registered yet.")

    # Workflows
    workflows = context.get("available_workflows", [])
    if workflows:
        lines = []
        for w in workflows:
            scope = ", ".join(w.get("scope") or []) or "none"
            lines.append(
                f"  - {w['id']}: {w.get('description', '')} "
                f"({w['steps']} steps, scope: {scope})"
            )
        parts.append("### Available Workflows\n" + "\n".join(lines))
    else:
        parts.append("### Available Workflows\n  No workflows defined yet.")

    # Capabilities
    capabilities = context.get("available_capabilities", [])
    if capabilities:
        parts.append(
            "### Available Capabilities (tools)\n  " + ", ".join(capabilities)
        )
    else:
        parts.append("### Available Capabilities\n  No capabilities configured.")

    # Connectors
    connectors = context.get("available_connectors", [])
    if connectors:
        lines = []
        for c in connectors:
            if isinstance(c, dict):
                caps = ", ".join(c.get("capabilities", [])) or "none"
                desc = c.get("description", "")
                lines.append(f"  - {c['id']} ({c['name']}): {desc} [capabilities: {caps}]")
            else:
                lines.append(f"  - {c}")
        parts.append("### Connectors\n" + "\n".join(lines))
    else:
        parts.append(
            "### Connectors\n  No connectors configured yet.\n"
            "  Use /connector search <query> to find MCP servers for external services.\n"
            "  Use /connector add <name> to install a known connector."
        )

    return "\n\n".join(parts)


def _get_agents(app: Any) -> list[dict[str, Any]]:
    """Get active agents with capabilities."""
    try:
        agents = app.agent_registry.list_agents(status="active")
        return [
            {
                "id": a["id"],
                "name": a["name"],
                "type": a["agent_type"],
                "capabilities": a.get("capabilities", []),
            }
            for a in agents
        ]
    except Exception:
        return []


def _get_workflows(app: Any) -> list[dict[str, Any]]:
    """Get active workflows with scope info."""
    try:
        workflows = app.workflow_registry.list_workflows(status="active")
        return [
            {
                "id": w["id"],
                "name": w["name"],
                "description": w.get("description", ""),
                "steps": len(w.get("steps", [])),
                "scope": w.get("scope", []),
                "tags": w.get("tags", []),
            }
            for w in workflows
        ]
    except Exception:
        return []


def _get_capabilities(app: Any) -> list[str]:
    """Get all available capabilities from connectors."""
    try:
        connectors = app.connector_registry.list_connectors(status="active")
        capabilities: set[str] = set()
        for c in connectors:
            for cap in c.get("capabilities", []):
                capabilities.add(cap)
        return sorted(capabilities)
    except Exception:
        return []


def _get_connectors(app: Any) -> list[dict[str, Any]]:
    """Get connectors with descriptions and capabilities."""
    try:
        connectors = app.connector_registry.list_connectors(status="active")
        return [
            {
                "id": c["id"],
                "name": c["name"],
                "type": c.get("connector_type", ""),
                "description": c.get("description", ""),
                "capabilities": c.get("capabilities", []),
            }
            for c in connectors
        ]
    except Exception:
        return []
