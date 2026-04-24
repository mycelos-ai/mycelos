"""Tests for connector setup CLI."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from mycelos.cli.connector_cmd import CONNECTORS, _setup_connector, connector_cmd


def test_connectors_dict_has_required_fields() -> None:
    """Every connector has name, description, requires_key, capabilities."""
    for key, info in CONNECTORS.items():
        assert "name" in info, f"{key} missing name"
        assert "description" in info, f"{key} missing description"
        assert "requires_key" in info, f"{key} missing requires_key"
        assert "capabilities" in info, f"{key} missing capabilities"
        assert isinstance(info["capabilities"], list), f"{key} capabilities not a list"


def test_duckduckgo_no_key_required() -> None:
    """DuckDuckGo connector doesn't need an API key."""
    assert CONNECTORS["web-search-duckduckgo"]["requires_key"] is False


def test_brave_requires_key() -> None:
    """Brave connector requires an API key."""
    assert CONNECTORS["web-search-brave"]["requires_key"] is True


def test_brave_has_env_var() -> None:
    """Brave connector declares its environment variable."""
    assert CONNECTORS["web-search-brave"]["env_var"] == "BRAVE_API_KEY"


def test_http_no_key_required() -> None:
    """HTTP connector doesn't need an API key."""
    assert CONNECTORS["http"]["requires_key"] is False


def test_github_connector_available() -> None:
    """GitHub is a regular connector (not coming soon — uses MCP)."""
    assert "github" in CONNECTORS
    assert CONNECTORS["github"].get("coming_soon") is not True
    assert CONNECTORS["github"]["requires_key"] is True
    assert CONNECTORS["github"]["env_var"] == "GITHUB_PERSONAL_ACCESS_TOKEN"


def test_connector_list_command(tmp_data_dir: Path) -> None:
    """mycelos connector list shows available connectors."""
    from mycelos.app import App

    os.environ["MYCELOS_MASTER_KEY"] = "test-key-for-list"
    try:
        app = App(tmp_data_dir)
        app.initialize()

        runner = CliRunner()
        result = runner.invoke(
            connector_cmd, ["list", "--data-dir", str(tmp_data_dir)]
        )
        assert result.exit_code == 0
        assert "DuckDuckGo" in result.output
        assert "Brave" in result.output
        assert "HTTP" in result.output
    finally:
        os.environ.pop("MYCELOS_MASTER_KEY", None)


def test_connector_list_shows_coming_soon(tmp_data_dir: Path) -> None:
    """MCP-based connectors such as GitHub appear in the list."""
    from mycelos.app import App

    os.environ["MYCELOS_MASTER_KEY"] = "test-key-for-list"
    try:
        app = App(tmp_data_dir)
        app.initialize()

        runner = CliRunner()
        result = runner.invoke(
            connector_cmd, ["list", "--data-dir", str(tmp_data_dir)]
        )
        assert result.exit_code == 0
        assert "GitHub" in result.output
    finally:
        os.environ.pop("MYCELOS_MASTER_KEY", None)


def test_connector_list_not_initialized(tmp_data_dir: Path) -> None:
    """mycelos connector list fails when not initialized."""
    runner = CliRunner()
    result = runner.invoke(
        connector_cmd, ["list", "--data-dir", str(tmp_data_dir)]
    )
    assert result.exit_code == 1


def test_keyless_connector_setup_sets_policy(tmp_data_dir: Path) -> None:
    """Setting up a keyless connector enables its capabilities."""
    from mycelos.app import App

    os.environ["MYCELOS_MASTER_KEY"] = "test-key-for-policy"
    try:
        app = App(tmp_data_dir)
        app.initialize()
        _setup_connector(app, "http", CONNECTORS["http"])

        # Policy should be set to "always" for http.get and http.post
        decision_get = app.policy_engine.evaluate("default", "any-agent", "http.get")
        decision_post = app.policy_engine.evaluate("default", "any-agent", "http.post")
        assert decision_get == "always"
        assert decision_post == "always"
    finally:
        os.environ.pop("MYCELOS_MASTER_KEY", None)


def test_keyless_connector_logs_audit(tmp_data_dir: Path) -> None:
    """Setting up a keyless connector creates an audit log entry."""
    from mycelos.app import App

    os.environ["MYCELOS_MASTER_KEY"] = "test-key-for-audit"
    try:
        app = App(tmp_data_dir)
        app.initialize()
        _setup_connector(app, "web-search-duckduckgo", CONNECTORS["web-search-duckduckgo"])

        # Check audit log contains the connector setup event
        logs = app.audit.query(event_type="connector.setup", limit=10)
        assert len(logs) >= 1
        assert logs[0]["event_type"] == "connector.setup"
    finally:
        os.environ.pop("MYCELOS_MASTER_KEY", None)


def test_keyed_connector_stores_credential(tmp_data_dir: Path) -> None:
    """Setting up a keyed connector encrypts the API key."""
    from mycelos.app import App

    os.environ["MYCELOS_MASTER_KEY"] = "test-key-for-cred"
    try:
        app = App(tmp_data_dir)
        app.initialize()

        # Simulate key entry
        with patch("click.prompt", return_value="test-brave-key-123"):
            with patch("click.confirm", return_value=False):  # Skip test
                _setup_connector(
                    app, "web-search-brave", CONNECTORS["web-search-brave"]
                )

        # Credential should be stored encrypted (bare connector id since
        # b365963 — see CHANGELOG).
        cred = app.credentials.get_credential("web-search-brave")
        assert cred is not None
        assert cred["api_key"] == "test-brave-key-123"
        assert cred["connector"] == "web-search-brave"
    finally:
        os.environ.pop("MYCELOS_MASTER_KEY", None)


def test_keyed_connector_sets_policy(tmp_data_dir: Path) -> None:
    """Setting up a keyed connector also enables its capabilities."""
    from mycelos.app import App

    os.environ["MYCELOS_MASTER_KEY"] = "test-key-for-policy2"
    try:
        app = App(tmp_data_dir)
        app.initialize()

        with patch("click.prompt", return_value="test-key"):
            with patch("click.confirm", return_value=False):
                _setup_connector(
                    app, "web-search-brave", CONNECTORS["web-search-brave"]
                )

        decision = app.policy_engine.evaluate("default", "any-agent", "search.web")
        assert decision == "always"
    finally:
        os.environ.pop("MYCELOS_MASTER_KEY", None)


def test_setup_unknown_connector(tmp_data_dir: Path) -> None:
    """mycelos connector setup with unknown name fails."""
    from mycelos.app import App

    os.environ["MYCELOS_MASTER_KEY"] = "test-key-for-unknown"
    try:
        app = App(tmp_data_dir)
        app.initialize()

        runner = CliRunner()
        result = runner.invoke(
            connector_cmd,
            ["setup", "nonexistent", "--data-dir", str(tmp_data_dir)],
        )
        assert result.exit_code == 1
        assert "Unknown connector" in result.output
    finally:
        os.environ.pop("MYCELOS_MASTER_KEY", None)


def test_setup_not_initialized(tmp_data_dir: Path) -> None:
    """mycelos connector setup fails when not initialized."""
    runner = CliRunner()
    result = runner.invoke(
        connector_cmd, ["setup", "--data-dir", str(tmp_data_dir)]
    )
    assert result.exit_code == 1


# ── Task 2: recipe-driven _setup_mcp helper ─────────────────────

def test_recipe_driven_github_recipe_exists() -> None:
    """GitHub is an MCP recipe."""
    from mycelos.connectors.mcp_recipes import get_recipe
    r = get_recipe("github")
    assert r is not None
    assert r.kind == "mcp"
    assert r.credentials and r.credentials[0]["env_var"] == "GITHUB_PERSONAL_ACCESS_TOKEN"


def test_recipe_driven_brave_recipe_exists() -> None:
    """Brave Search is an MCP recipe."""
    from mycelos.connectors.mcp_recipes import get_recipe
    r = get_recipe("brave-search")
    assert r is not None
    assert r.kind == "mcp"


def test_recipe_driven_telegram_recipe_is_channel() -> None:
    """Telegram recipe has kind=channel (consumed by _setup_channel in Task 3)."""
    from mycelos.connectors.mcp_recipes import get_recipe
    r = get_recipe("telegram")
    assert r is not None
    assert r.kind == "channel"


def test_setup_mcp_no_key_grants_policy(tmp_data_dir: Path) -> None:
    """Setting up an MCP recipe without credentials grants its capabilities."""
    from mycelos.app import App
    from mycelos.cli.connector_cmd import _setup_mcp
    from mycelos.connectors.mcp_recipes import get_recipe

    os.environ["MYCELOS_MASTER_KEY"] = "test-key-setup-mcp-nokey"
    try:
        app = App(tmp_data_dir)
        app.initialize()
        recipe = get_recipe("fetch")  # no credentials
        assert recipe is not None
        _setup_mcp(app, recipe)

        for cap in recipe.capabilities_preview:
            decision = app.policy_engine.evaluate("default", "any-agent", cap)
            assert decision == "always", f"cap {cap} not granted"
    finally:
        os.environ.pop("MYCELOS_MASTER_KEY", None)


def test_setup_mcp_with_key_stores_credential(tmp_data_dir: Path) -> None:
    """Setting up a keyed MCP recipe stores the credential under recipe.id."""
    from mycelos.app import App
    from mycelos.cli.connector_cmd import _setup_mcp
    from mycelos.connectors.mcp_recipes import get_recipe

    os.environ["MYCELOS_MASTER_KEY"] = "test-key-setup-mcp-keyed"
    try:
        app = App(tmp_data_dir)
        app.initialize()
        recipe = get_recipe("brave-search")
        assert recipe is not None

        with patch("click.prompt", return_value="BSA-test-token"), \
             patch("click.confirm", return_value=False):
            _setup_mcp(app, recipe)

        stored = app.credentials.get_credential("brave-search")
        assert stored is not None
        assert stored["api_key"] == "BSA-test-token"
        assert stored["env_var"] == "BRAVE_API_KEY"
    finally:
        os.environ.pop("MYCELOS_MASTER_KEY", None)


def test_setup_mcp_registers_in_connector_registry(tmp_data_dir: Path) -> None:
    """After setup, connector_registry.get returns the connector."""
    from mycelos.app import App
    from mycelos.cli.connector_cmd import _setup_mcp
    from mycelos.connectors.mcp_recipes import get_recipe

    os.environ["MYCELOS_MASTER_KEY"] = "test-key-setup-mcp-registry"
    try:
        app = App(tmp_data_dir)
        app.initialize()
        recipe = get_recipe("fetch")
        assert recipe is not None
        _setup_mcp(app, recipe)

        row = app.connector_registry.get("fetch")
        assert row is not None
        assert row["name"] == recipe.name
    finally:
        os.environ.pop("MYCELOS_MASTER_KEY", None)
