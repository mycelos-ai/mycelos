"""Tests for gamification level prompt system."""

from unittest.mock import MagicMock

from mycelos.gamification import get_level_prompt


def _make_mock_app(messages=0, notes=0, connectors=0, workflows=0, agents=0,
                   user_name=None, level=None):
    """Create a mock App with controllable milestone counts."""
    app = MagicMock()

    def fetchone(sql, params=None):
        if "audit_events" in sql:
            return {"c": messages}
        if "knowledge_notes" in sql:
            return {"c": notes}
        if "connectors" in sql:
            return {"c": connectors}
        if "workflows" in sql:
            return {"c": workflows}
        if "agents" in sql:
            return {"c": agents}
        return {"c": 0}

    app.storage.fetchone = fetchone
    app.storage.fetchall = lambda *a, **kw: []
    app.memory.get = lambda uid, scope, key: {
        "user.name": user_name,
        "user_level": str(level) if level else None,
        "greeting_count": "0",
    }.get(key)
    app.memory.set = MagicMock()
    app.credentials.list_services = lambda: []
    return app


def test_check_milestones_newcomer():
    from mycelos.gamification import check_milestones
    app = _make_mock_app(messages=3, notes=0)
    assert check_milestones(app) == 1


def test_check_milestones_explorer():
    from mycelos.gamification import check_milestones
    app = _make_mock_app(messages=15, notes=2)
    assert check_milestones(app) == 2


def test_level_prompt_newcomer_is_longest():
    prompt = get_level_prompt(1)
    assert "new to Mycelos" in prompt
    assert "suggest next steps" in prompt.lower() or "proactively" in prompt.lower()
    assert len(prompt) > 200  # Newcomer gets detailed guidance


def test_level_prompt_builder_is_shorter():
    prompt = get_level_prompt(3)
    assert "experienced" in prompt.lower() or "Builder" in prompt
    assert len(prompt) < len(get_level_prompt(1))


def test_level_prompt_power_user_is_shortest():
    prompt = get_level_prompt(5)
    assert "power user" in prompt.lower() or "Power User" in prompt
    assert len(prompt) < len(get_level_prompt(3))


def test_level_prompt_guru_same_as_power_user():
    assert get_level_prompt(6) == get_level_prompt(5)


def test_level_prompt_includes_level_name():
    prompt = get_level_prompt(1)
    assert "Newcomer" in prompt
    prompt = get_level_prompt(3)
    assert "Builder" in prompt


def test_level_prompt_out_of_range_returns_newcomer():
    assert get_level_prompt(0) == get_level_prompt(1)
    assert get_level_prompt(99) == get_level_prompt(6)


def test_hint_newcomer_no_notes_suggests_first_note():
    from mycelos.gamification import _get_contextual_hint
    app = _make_mock_app(messages=2, notes=0)
    hint = _get_contextual_hint(app, level=1)
    assert hint is not None
    # Should be benefit-oriented, not generic
    assert "?" in hint  # Call-to-action, not statement


def test_hint_builder_no_telegram_suggests_telegram():
    from mycelos.gamification import _get_contextual_hint
    app = _make_mock_app(messages=20, notes=5, connectors=0)
    app.storage.fetchall = lambda *a, **kw: []  # No connectors
    hint = _get_contextual_hint(app, level=3)
    assert hint is not None


def test_hint_power_user_gets_no_hints():
    from mycelos.gamification import _get_contextual_hint
    app = _make_mock_app(messages=100, notes=30, connectors=3)
    hint = _get_contextual_hint(app, level=5)
    assert hint is None


def test_hints_are_deterministic_not_random():
    """Same state should return the same hint (no randomization)."""
    from mycelos.gamification import _get_contextual_hint
    app = _make_mock_app(messages=2, notes=0)
    hint1 = _get_contextual_hint(app, level=1)
    hint2 = _get_contextual_hint(app, level=1)
    assert hint1 == hint2


def test_init_welcome_box_i18n_keys_exist():
    """All welcome box i18n keys must resolve."""
    from mycelos.i18n import t
    keys = [
        "init.welcome_box.title",
        "init.welcome_box.line1",
        "init.welcome_box.line2",
        "init.welcome_box.bullet1",
        "init.welcome_box.bullet2",
        "init.welcome_box.bullet3",
        "init.welcome_box.bullet4",
    ]
    for key in keys:
        value = t(key)
        assert value != key, f"i18n key {key} not found"
        assert len(value) > 3, f"i18n key {key} is too short: {value!r}"


def test_memory_write_agent_display_name_syncs_to_registry():
    """Writing agent.display_name to memory should also update agents table.

    The LLM uses category='fact' (from the enum) with key='agent.display_name'.
    The sync triggers on the key, not the category.
    """
    from mycelos.tools.memory import execute_memory_write

    app = _make_mock_app()
    app.audit.log = MagicMock()
    app.agent_registry.rename = MagicMock()

    context = {"app": app, "user_id": "default", "agent_id": "mycelos"}

    result = execute_memory_write(
        {"category": "fact", "key": "agent.display_name", "value": "Fridolin"},
        context,
    )

    assert result.get("status") == "remembered"
    app.agent_registry.rename.assert_called_once_with("mycelos", "Fridolin")
