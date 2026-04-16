"""Gamification — User Level System.

Users progress through levels based on actual usage milestones.
Level is stored in Memory and shown at session start.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from mycelos.i18n import t


@dataclass
class Level:
    number: int
    name: str
    icon: str
    description: str
    next_hint: str  # What to do to reach next level


def _make_level(number: int, name: str, icon: str, desc_key: str, hint_key: str) -> Level:
    """Create a Level with i18n description and next_hint."""
    return Level(
        number=number,
        name=name,
        icon=icon,
        description=t(desc_key),
        next_hint=t(hint_key),
    )


LEVELS = [
    _make_level(1, "Newcomer", "🌱",
                "gamification.level.newcomer.description",
                "gamification.level.newcomer.next_hint"),
    _make_level(2, "Explorer", "🔍",
                "gamification.level.explorer.description",
                "gamification.level.explorer.next_hint"),
    _make_level(3, "Builder", "🔧",
                "gamification.level.builder.description",
                "gamification.level.builder.next_hint"),
    _make_level(4, "Architect", "🏗️",
                "gamification.level.architect.description",
                "gamification.level.architect.next_hint"),
    _make_level(5, "Power User", "⚡",
                "gamification.level.power_user.description",
                "gamification.level.power_user.next_hint"),
    _make_level(6, "Guru", "🧙",
                "gamification.level.guru.description",
                "gamification.level.guru.next_hint"),
]


def get_level(number: int) -> Level:
    """Get level by number (1-based). Returns Newcomer if out of range."""
    for lvl in LEVELS:
        if lvl.number == number:
            return lvl
    return LEVELS[0]


def get_next_level(current: int) -> Level | None:
    """Get the next level, or None if already max."""
    for lvl in LEVELS:
        if lvl.number == current + 1:
            return lvl
    return None


# --- Level prompts (English only — Constitution Rule 6) ---

_LEVEL_PROMPTS = {
    "newcomer": (
        "The user is new to Mycelos (Level: Newcomer).\n"
        "- Explain what you can do when relevant. Don't list features unprompted, "
        "but when the user asks something, mention related capabilities.\n"
        "- Proactively suggest next steps: \"I can also remind you about this "
        "— want me to set a reminder?\"\n"
        "- When the user creates their first note, acknowledge it warmly.\n"
        "- Mention what makes Mycelos different when it fits naturally: "
        "data stays local, the system grows with them, cloud LLMs are tools not storage.\n"
        "- Suggest one unused feature per conversation, framed as a benefit, not a tutorial."
    ),
    "explorer": (
        "The user is getting familiar with Mycelos (Level: Explorer).\n"
        "- They've created notes and chatted. Explain new features when relevant, "
        "but don't repeat basics they already know.\n"
        "- Proactively suggest next steps when it fits: connectors, reminders, tasks.\n"
        "- Mention what makes Mycelos different when it fits naturally."
    ),
    "builder": (
        "The user is experienced with Mycelos (Level: Builder).\n"
        "- They know the basics. Skip explanations for notes, reminders, search.\n"
        "- Mention advanced features only when directly relevant: workflows, agents, automations.\n"
        "- Keep confirmations brief."
    ),
    "architect": (
        "The user is experienced with Mycelos (Level: Architect).\n"
        "- Skip explanations. Mention workflows and custom agents when relevant.\n"
        "- Keep confirmations brief."
    ),
    "power_user": (
        "The user is a Mycelos power user (Level: Power User).\n"
        "- Terse responses. No feature suggestions unless asked.\n"
        "- Focus on efficiency and optimization."
    ),
}


def get_level_prompt(level: int) -> str:
    """Return a level-appropriate prompt block for the LLM system prompt.

    Higher levels get shorter prompts (fewer tokens, less hand-holding).
    Prompts are English-only (Constitution Rule 6).
    """
    if level <= 1:
        return _LEVEL_PROMPTS["newcomer"]
    elif level == 2:
        return _LEVEL_PROMPTS["explorer"]
    elif level == 3:
        return _LEVEL_PROMPTS["builder"]
    elif level == 4:
        return _LEVEL_PROMPTS["architect"]
    else:
        return _LEVEL_PROMPTS["power_user"]


def check_milestones(app: Any, user_id: str = "default") -> int:
    """Check user's milestones and return their earned level number.

    Milestones are checked against actual system state — not self-reported.
    """
    current_level = 1

    try:
        # Count messages (from audit events)
        msg_count = app.storage.fetchone(
            "SELECT COUNT(*) as c FROM audit_events WHERE event_type = 'chat.message' AND user_id = ?",
            (user_id,),
        )
        messages = (msg_count or {}).get("c", 0)

        # Count KB notes
        note_count = app.storage.fetchone(
            "SELECT COUNT(*) as c FROM knowledge_notes",
        )
        notes = (note_count or {}).get("c", 0)

        # Count connectors
        connector_count = app.storage.fetchone(
            "SELECT COUNT(*) as c FROM connectors WHERE status = 'active'",
        )
        connectors = (connector_count or {}).get("c", 0)

        # Count workflows (excluding builtins)
        workflow_count = app.storage.fetchone(
            "SELECT COUNT(*) as c FROM workflows WHERE created_by != 'system'",
        )
        custom_workflows = (workflow_count or {}).get("c", 0)

        # Count agents (excluding system agents)
        agent_count = app.storage.fetchone(
            "SELECT COUNT(*) as c FROM agents WHERE created_by != 'system' AND status = 'active'",
        )
        custom_agents = (agent_count or {}).get("c", 0)

        # Level 2: Explorer — 10+ messages AND 1+ KB note
        if messages >= 10 and notes >= 1:
            current_level = 2

        # Level 3: Builder — has a connector OR used a workflow
        if current_level >= 2 and connectors >= 1:
            current_level = 3

        # Level 4: Architect — created an agent OR has 3+ connectors
        if current_level >= 3 and (custom_agents >= 1 or connectors >= 3):
            current_level = 4

        # Level 5: Power User — 20+ notes AND custom workflows
        if current_level >= 4 and notes >= 20 and custom_workflows >= 1:
            current_level = 5

        # Level 6: Guru — everything maxed out
        if current_level >= 5 and custom_agents >= 2 and connectors >= 4 and notes >= 50:
            current_level = 6

    except Exception:
        pass  # DB errors → stay at current level

    return current_level


def get_session_greeting(app: Any, user_id: str = "default") -> str | None:
    """Generate a level-aware greeting for session start.

    Shows:
    - Level-up celebration (always, when earned)
    - Progress hint (frequency based on level — more often for beginners)
    - Specific next-step suggestions based on what's NOT yet configured
    """
    try:
        earned_level = check_milestones(app, user_id)

        stored = app.memory.get("default", "system", "user_level")
        stored_level = int(stored) if stored else 1

        level = get_level(earned_level)
        next_level = get_next_level(earned_level)

        # Level up! Always show this.
        if earned_level > stored_level:
            app.memory.set("default", "system", "user_level", str(earned_level))
            parts = [
                f"{level.icon} **{t('gamification.level_up', name=level.name)}**",
                f"_{level.description}_",
            ]
            if next_level:
                parts.append(f"\n{t('gamification.next_goal')}: {next_level.next_hint}")
            return "\n".join(parts)

        # Update stored level
        app.memory.set("default", "system", "user_level", str(earned_level))

        # Decide whether to show a hint based on frequency
        # Beginners: every session. Advanced: less often.
        greeting_count_str = app.memory.get("default", "system", "greeting_count") or "0"
        greeting_count = int(greeting_count_str)
        app.memory.set("default", "system", "greeting_count", str(greeting_count + 1))

        # Show frequency: Newcomer=every time, Explorer=every 2, Builder=every 3, etc.
        show_every = max(1, earned_level)
        if greeting_count % show_every != 0:
            return None

        # Build contextual hint based on what's missing
        hint = _get_contextual_hint(app, earned_level)
        if hint:
            return f"{level.icon} *{level.name}* — {hint}"
        elif next_level:
            return f"{level.icon} *{level.name}* — {next_level.next_hint}"

    except Exception:
        pass

    return None


def _get_contextual_hint(app: Any, level: int) -> str | None:
    """Return the most relevant benefit-oriented hint for the user's current state.

    Hints are ordered by priority within each level range. The first hint
    whose condition is met wins (deterministic, not random).
    Returns None for level 5+ (power users don't need hints).
    """
    if level >= 5:
        return None

    try:
        connectors = app.storage.fetchall(
            "SELECT id FROM connectors WHERE status = 'active'"
        )
        connector_ids = {c["id"] for c in connectors}

        notes = app.storage.fetchone("SELECT COUNT(*) as c FROM knowledge_notes")
        note_count = (notes or {}).get("c", 0)

        # Ordered hint definitions: (min_level, max_level, condition, i18n_key, kwargs)
        hint_defs: list[tuple[int, int, bool, str, dict]] = [
            # Level 1-2: First steps
            (1, 2, note_count == 0, "gamification.hint.first_note", {}),
            (1, 2, note_count > 0, "gamification.hint.first_reminder", {}),
            # Level 2-3: Connectors + KB growth
            (2, 3, "telegram" not in connector_ids, "gamification.hint.telegram_benefit", {}),
            (2, 3, 1 <= note_count <= 5, "gamification.hint.kb_growing", {"count": note_count}),
            # Level 3-4: Advanced connectors + KB linking
            (3, 4, "github" not in connector_ids, "gamification.hint.github_benefit", {}),
            (3, 4, 5 < note_count < 20, "gamification.hint.kb_linking", {}),
            # Level 4: Workflows
            (4, 4, True, "gamification.hint.workflow_benefit", {}),
        ]

        for min_lvl, max_lvl, condition, key, kwargs in hint_defs:
            if min_lvl <= level <= max_lvl and condition:
                return t(key, **kwargs)

    except Exception:
        pass

    return None
