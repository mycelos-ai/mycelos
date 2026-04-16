"""Chat context builder — shared between ChatService and CLI.

Extracted from chat_cmd.py to break the circular dependency
between the service layer and the CLI layer.
"""

from __future__ import annotations

from typing import Any


def build_context(app: Any) -> str:
    """Build dynamic context for the system prompt.

    Reads live data from ConnectorRegistry, AgentRegistry, and config
    so the LLM knows what tools and agents are available.
    """
    parts: list[str] = []

    # Connectors + their capabilities
    try:
        connectors = app.connector_registry.list_connectors(status="active")
        if connectors:
            lines: list[str] = []
            for c in connectors:
                caps = ", ".join(c["capabilities"])
                lines.append(f"- {c['name']} ({c['connector_type']}): {caps}")
            parts.append("## Available Connectors\n" + "\n".join(lines))
        else:
            parts.append(
                "## Available Connectors\n"
                "No connectors configured. Set one up with: `mycelos connector setup`"
            )
    except Exception:
        pass

    # Active agents + their capabilities
    try:
        agents = app.agent_registry.list_agents(status="active")
        if agents:
            lines = []
            for a in agents:
                caps = ", ".join(a["capabilities"]) if a["capabilities"] else "none"
                lines.append(f"- {a['name']} ({a['agent_type']}): {caps}")
            parts.append("## Active Agents\n" + "\n".join(lines))
        else:
            parts.append(
                "## Active Agents\n"
                "No agents registered. Tell me what you want to automate!"
            )
    except Exception:
        parts.append("## Active Agents\nNo agents configured.")

    # System config
    try:
        config = app.config.get_active_config() or {}
        provider = config.get("provider", "unknown")
        model = config.get("default_model", "unknown")
        parts.append(f"## System Config\nProvider: {provider}, Model: {model}")
    except Exception:
        pass

    return "\n\n".join(parts)


def handle_system_command(app: Any, user_input: str) -> str:
    """Handle system commands directly without LLM call."""
    lower = user_input.lower()

    if "config" in lower or "generation" in lower:
        gens = app.config.list_generations(limit=5)
        lines = ["**Config Generations:**\n"]
        for g in gens:
            active = " <- active" if g.is_active else ""
            lines.append(f"- Gen {g.id}: {g.description or 'no description'}{active}")
        return "\n".join(lines)

    elif "agent" in lower and ("list" in lower or "show" in lower or "which" in lower):
        agents = app.agent_registry.list_agents()
        if not agents:
            return "No agents registered. Tell me what you want to automate!"
        lines = ["**Registered Agents:**\n"]
        for a in agents:
            caps = ", ".join(a["capabilities"]) if a["capabilities"] else "none"
            lines.append(f"- {a['name']} ({a['agent_type']}) -- {a['status']} [{caps}]")
        return "\n".join(lines)

    elif "connector" in lower:
        try:
            connectors = app.connector_registry.list_connectors()
            if not connectors:
                return "No connectors configured. Set one up with: `mycelos connector setup`"
            lines = ["**Configured Connectors:**\n"]
            for c in connectors:
                caps = ", ".join(c["capabilities"])
                lines.append(f"- {c['name']} ({c['connector_type']}): {caps}")
            return "\n".join(lines)
        except Exception:
            return "No connectors configured."

    elif "task" in lower:
        tasks = app.task_manager.list_tasks(limit=10)
        if not tasks:
            return "No tasks yet. Ask me something!"
        lines = ["**Recent Tasks:**\n"]
        for t in tasks:
            lines.append(f"- {t['goal'][:50]} -- {t['status']}")
        return "\n".join(lines)

    elif "session" in lower:
        sessions = app.session_store.list_sessions()
        if not sessions:
            return "No sessions."
        lines = [f"**Sessions ({len(sessions)}):**\n"]
        for s in sessions[:5]:
            lines.append(f"- {s.get('session_id', '?')[:8]}... ({s.get('message_count', 0)} messages)")
        return "\n".join(lines)

    elif "workflow" in lower:
        try:
            workflows = app.workflow_registry.list_workflows(status="active")
            if not workflows:
                return "No workflows defined."
            lines = ["**Workflows:**\n"]
            for w in workflows:
                lines.append(f"- {w['name']} (v{w['version']}) — {w.get('description', '')}")
            return "\n".join(lines)
        except Exception:
            return "No workflows defined."

    elif "model" in lower or "llm" in lower:
        try:
            models = app.model_registry.list_models()
            if not models:
                return "No LLM models configured."
            lines = ["**LLM Models:**\n"]
            for m in models:
                cost = f"${m.get('input_cost_per_1k', 0):.4f}" if m.get("input_cost_per_1k") else "?"
                lines.append(f"- {m['id']} ({m['tier']}) — {cost}/1K")
            return "\n".join(lines)
        except Exception:
            return "No LLM models configured."

    else:
        return "Available info: config, agents, connectors, tasks, sessions, workflows, models"
