"""Tests for the ui.open_page tool."""

from __future__ import annotations

from mycelos.tools.ui import execute_open_page


def test_target_connectors_no_anchor() -> None:
    events = execute_open_page({"target": "connectors"}, context={})
    assert len(events) == 1
    actions = events[0].data["actions"]
    assert len(actions) == 1
    assert actions[0]["url"] == "/pages/connectors.html"
    assert actions[0]["kind"] == "link"
    assert actions[0]["label"] == "Open Connectors page"


def test_target_connectors_with_anchor() -> None:
    events = execute_open_page(
        {"target": "connectors", "anchor": "gmail"}, context={}
    )
    actions = events[0].data["actions"]
    assert actions[0]["url"] == "/pages/connectors.html#gmail"


def test_target_settings_models_default_anchor() -> None:
    events = execute_open_page({"target": "settings_models"}, context={})
    actions = events[0].data["actions"]
    assert actions[0]["url"] == "/pages/settings.html#models"


def test_target_settings_models_anchor_overrides_default() -> None:
    events = execute_open_page(
        {"target": "settings_models", "anchor": "provider-anthropic"},
        context={},
    )
    actions = events[0].data["actions"]
    assert actions[0]["url"] == "/pages/settings.html#provider-anthropic"


def test_unknown_target_returns_error_event() -> None:
    events = execute_open_page({"target": "memory"}, context={})
    assert len(events) == 1
    # Error path returns a system_response_event with the allowed list
    content = events[0].data.get("content", "")
    assert "memory" in content.lower() or "unknown" in content.lower()
    assert "connectors" in content  # the allowed-targets list


def test_custom_label_respected() -> None:
    events = execute_open_page(
        {"target": "connectors", "anchor": "gmail", "label": "Gmail einrichten"},
        context={},
    )
    actions = events[0].data["actions"]
    assert actions[0]["label"] == "Gmail einrichten"


def test_anchor_strips_leading_hash() -> None:
    """`anchor='#gmail'` and `anchor='gmail'` produce the same URL."""
    a = execute_open_page({"target": "connectors", "anchor": "gmail"}, context={})
    b = execute_open_page({"target": "connectors", "anchor": "#gmail"}, context={})
    assert a[0].data["actions"][0]["url"] == b[0].data["actions"][0]["url"]


def test_all_four_targets_resolve() -> None:
    """Every documented target maps to a URL — no silent omissions."""
    for target in ("connectors", "settings_models", "settings_generations", "doctor"):
        events = execute_open_page({"target": target}, context={})
        actions = events[0].data["actions"]
        assert actions[0]["url"].startswith("/pages/"), (
            f"target {target!r} produced URL {actions[0]['url']!r}"
        )
        assert actions[0]["kind"] == "link"
