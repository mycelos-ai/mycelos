"""Memory tools — read and write persistent user memory."""

from __future__ import annotations

from typing import Any

from mycelos.tools.registry import ToolPermission

# --- Tool Schemas ---

MEMORY_READ_SCHEMA = {
    "type": "function",
    "function": {
        "name": "memory_read",
        "description": (
            "Read stored facts about the user or context. "
            "Use this to recall preferences, decisions, or past context."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "What to look up (e.g., 'format preference', 'current project')",
                },
            },
            "required": ["query"],
        },
    },
}

MEMORY_WRITE_SCHEMA = {
    "type": "function",
    "function": {
        "name": "memory_write",
        "description": (
            "Remember an important fact about the user. "
            "Use this when the user states a preference, makes a decision, "
            "or shares context that should be remembered for future sessions."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "category": {
                    "type": "string",
                    "enum": ["preference", "decision", "context", "fact"],
                    "description": "Type of information",
                },
                "key": {
                    "type": "string",
                    "description": "Descriptive key (e.g., 'output_format', 'current_project')",
                },
                "value": {
                    "type": "string",
                    "description": "The fact to remember (1-2 sentences max)",
                },
            },
            "required": ["category", "key", "value"],
        },
    },
}


# --- Tool Execution ---

def execute_memory_read(args: dict, context: dict) -> Any:
    """Execute memory_read tool."""
    app = context["app"]
    results = app.memory.search("default", "system", args.get("query", ""))
    if not results:
        return {"results": [], "message": "Nothing found in memory"}
    return {
        "results": [
            {"key": r["key"], "value": r["value"]} for r in results[:10]
        ]
    }


def execute_memory_write(args: dict, context: dict) -> Any:
    """Execute memory_write tool with validation."""
    from mycelos.chat.service import _validate_memory_write

    app = context["app"]
    category = args.get("category", "fact")
    raw_key = args.get("key", "unknown")
    value = args.get("value", "")

    # H-03 Security: validate memory write
    error = _validate_memory_write(category, raw_key, value)
    if error:
        app.audit.log(
            "memory.write_blocked",
            details={"category": category, "key": raw_key, "reason": error},
        )
        return {"error": error}

    key = f"user.{category}.{raw_key}"
    app.memory.set("default", "system", key, value, created_by="agent")

    # Sync agent display_name to agents table (fixes UI showing old name)
    # The LLM writes key="agent.display_name" with any allowed category (typically "fact")
    if raw_key == "agent.display_name" and value:
        try:
            agent_id = context.get("agent_id", "mycelos")
            app.agent_registry.rename(agent_id, value)
            app.audit.log(
                "agent.renamed",
                details={"agent_id": agent_id, "display_name": value},
            )
        except Exception:
            pass  # Best-effort — memory write still succeeded

    # Track onboarding progress
    if "main_interest" in key or "topic" in key:
        app.audit.log("onboarding.step_3", details={"key": key})
    elif category == "preference":
        app.audit.log("onboarding.preference_saved", details={"key": key})

    return {"status": "remembered", "key": key}


# --- Registration ---

def register(registry: type) -> None:
    """Register all memory tools."""
    registry.register("memory_read", MEMORY_READ_SCHEMA, execute_memory_read, ToolPermission.OPEN, concurrent_safe=True, category="core")
    registry.register("memory_write", MEMORY_WRITE_SCHEMA, execute_memory_write, ToolPermission.STANDARD, category="core")
