"""Memory Injection — loads relevant memory entries into the system prompt.

Formats stored preferences, decisions, context, and facts so the LLM
knows what it has learned about the user across sessions.
"""

from __future__ import annotations

from typing import Any


def inject_memory_context(app: Any, user_id: str = "default") -> str:
    """Build memory context for system prompt injection.

    Loads all system-scope memory entries and formats them
    by category for the LLM to understand the user's context.

    Args:
        app: Mycelos App instance.
        user_id: User identifier.

    Returns:
        Formatted memory context string, or empty string if no entries.
    """
    try:
        entries = app.memory.search(user_id, "system", "")
    except Exception:
        return ""

    if not entries:
        return ""

    # Group by category based on key prefix
    preferences: list[str] = []
    decisions: list[str] = []
    context: list[str] = []
    facts: list[str] = []

    for e in entries[:30]:  # Max 30 entries to keep prompt manageable
        key = e.get("key", "") if isinstance(e, dict) else ""
        value = e.get("value", "") if isinstance(e, dict) else ""

        if not key or not value:
            continue

        # Extract readable name from key
        display_key = key.split(".")[-1].replace("_", " ")

        if ".preference." in key:
            preferences.append(f"- {display_key}: {value}")
        elif ".decision." in key:
            decisions.append(f"- {display_key}: {value}")
        elif ".context." in key:
            context.append(f"- {display_key}: {value}")
        elif ".fact." in key:
            facts.append(f"- {display_key}: {value}")
        elif key == "user.name":
            continue  # Already handled separately
        else:
            facts.append(f"- {display_key}: {value}")

    if not any([preferences, decisions, context, facts]):
        return ""

    parts = ["<memory>"]
    if preferences:
        parts.append("User Preferences:\n" + "\n".join(preferences))
    if decisions:
        parts.append("Decisions:\n" + "\n".join(decisions))
    if context:
        parts.append("Current Context:\n" + "\n".join(context))
    if facts:
        parts.append("Known Facts:\n" + "\n".join(facts))
    parts.append("</memory>")

    return "\n\n".join(parts)
