"""Tests for /connector slash commands — list/search (read-only)."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

from mycelos.app import App
from mycelos.chat.slash_commands import handle_slash_command


def _result_text(result) -> str:
    """Extract text from slash command result (str or ChatEvent list)."""
    if isinstance(result, str):
        return result
    if isinstance(result, list):
        parts = []
        for event in result:
            data = event.data if hasattr(event, "data") else {}
            if "content" in data:
                parts.append(data["content"])
        return "\n".join(parts)
    return str(result)


@pytest.fixture
def app():
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["MYCELOS_MASTER_KEY"] = "test-connector-cmds"
        a = App(Path(tmp))
        a.initialize()
        yield a


class TestConnectorSetupDeprecated:
    """Setup verbs in chat must redirect to CLI / Web UI."""

    @pytest.mark.parametrize("verb", ["add", "setup", "remove", "test"])
    def test_setup_verbs_redirect(self, app, verb):
        result = handle_slash_command(app, f"/connector {verb} playwright")
        text = _result_text(result).lower()
        assert (
            "not supported in chat" in text
            or "use the web ui" in text
            or "mycelos connector setup" in text
        ), f"/connector {verb} should redirect, got: {text!r}"

    def test_setup_does_not_register_connector(self, app):
        """Deprecated verbs must not call into the registry."""
        handle_slash_command(app, "/connector add playwright npx -y @playwright/mcp")
        # The registry must still be empty — setup was not executed.
        assert app.connector_registry.get("playwright") is None


class TestConnectorList:
    def test_list_shows_connectors(self, app):
        result = handle_slash_command(app, "/connector list")
        assert "Connectors" in _result_text(result) or "connector" in _result_text(result).lower()


class TestConnectorSearch:
    def test_search_returns_results(self, app):
        """Note: This makes a real HTTP call to the MCP registry."""
        result = handle_slash_command(app, "/connector search playwright")
        # May or may not find results depending on network
        assert isinstance(result, str)
        assert len(result) > 10
