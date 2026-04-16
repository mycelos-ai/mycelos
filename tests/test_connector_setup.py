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


def test_google_connector_uses_gog() -> None:
    """Google connector uses gog setup type (no API key needed)."""
    assert "google" in CONNECTORS
    google = CONNECTORS["google"]
    assert google["setup_type"] == "gog"
    assert google["requires_key"] is False
    assert "google.gmail.read" in google["capabilities"]
    assert "google.calendar.read" in google["capabilities"]
    assert "google.drive.read" in google["capabilities"]


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
    """Coming-soon connectors appear in the list."""
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
        assert "Gmail" in result.output
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

        # Credential should be stored encrypted
        cred = app.credentials.get_credential("connector:web-search-brave")
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
