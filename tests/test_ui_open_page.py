"""Tests for the ui_open_page tool."""

from __future__ import annotations

from mycelos.tools.ui import execute_open_page


def test_target_connectors_no_anchor() -> None:
    result = execute_open_page({"target": "connectors"}, context={})
    actions = result["__suggested_actions__"]
    assert len(actions) == 1
    assert actions[0]["url"] == "/pages/connectors.html"
    assert actions[0]["kind"] == "link"
    assert actions[0]["label"] == "Open Connectors page"


def test_target_connectors_with_anchor() -> None:
    result = execute_open_page(
        {"target": "connectors", "anchor": "gmail"}, context={}
    )
    actions = result["__suggested_actions__"]
    assert actions[0]["url"] == "/pages/connectors.html#gmail"


def test_target_settings_models_default_anchor() -> None:
    result = execute_open_page({"target": "settings_models"}, context={})
    actions = result["__suggested_actions__"]
    assert actions[0]["url"] == "/pages/settings.html#models"


def test_target_settings_models_anchor_overrides_default() -> None:
    result = execute_open_page(
        {"target": "settings_models", "anchor": "provider-anthropic"},
        context={},
    )
    actions = result["__suggested_actions__"]
    assert actions[0]["url"] == "/pages/settings.html#provider-anthropic"


def test_unknown_target_returns_error_dict() -> None:
    result = execute_open_page({"target": "memory"}, context={})
    assert "__suggested_actions__" not in result
    assert "error" in result
    msg = result["error"].lower()
    assert "memory" in msg or "unknown" in msg
    assert "connectors" in result["error"]  # the allowed-targets list


def test_custom_label_respected() -> None:
    result = execute_open_page(
        {"target": "connectors", "anchor": "gmail", "label": "Gmail einrichten"},
        context={},
    )
    actions = result["__suggested_actions__"]
    assert actions[0]["label"] == "Gmail einrichten"


def test_anchor_strips_leading_hash() -> None:
    """`anchor='#gmail'` and `anchor='gmail'` produce the same URL."""
    a = execute_open_page({"target": "connectors", "anchor": "gmail"}, context={})
    b = execute_open_page({"target": "connectors", "anchor": "#gmail"}, context={})
    assert (
        a["__suggested_actions__"][0]["url"]
        == b["__suggested_actions__"][0]["url"]
    )


def test_all_four_targets_resolve() -> None:
    """Every documented target maps to a URL — no silent omissions."""
    for target in ("connectors", "settings_models", "settings_generations", "doctor"):
        result = execute_open_page({"target": target}, context={})
        actions = result["__suggested_actions__"]
        assert actions[0]["url"].startswith("/pages/"), (
            f"target {target!r} produced URL {actions[0]['url']!r}"
        )
        assert actions[0]["kind"] == "link"


def test_result_is_json_serializable() -> None:
    """Tool results MUST be JSON-serializable so they can be passed to the LLM."""
    import json
    result = execute_open_page({"target": "connectors", "anchor": "telegram"}, context={})
    json.dumps(result)  # raises if not serializable
