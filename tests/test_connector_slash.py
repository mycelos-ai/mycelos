"""Tests for /connector slash commands — read-only (list, search)."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

from mycelos.app import App
from mycelos.chat.slash_commands import handle_slash_command


def _text(result) -> str:
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
def app() -> App:
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["MYCELOS_MASTER_KEY"] = "test-key-connector-slash"
        a = App(Path(tmp))
        a.initialize()
        yield a


# --- /connector list ---

def test_connector_list(app):
    result = handle_slash_command(app, "/connector list")
    assert "github" in _text(result).lower()
    assert "brave" in _text(result).lower()
    assert "Available" in _text(result) or "available" in _text(result)


def test_connector_list_shows_active(app):
    app.connector_registry.register("test-conn", "Test", "builtin", ["test.cap"])
    result = handle_slash_command(app, "/connector list")
    assert "test-conn" in _text(result)


# --- Setup verbs are deprecated in chat ---

@pytest.mark.parametrize("verb", ["add", "setup", "remove", "test"])
def test_connector_setup_verbs_are_deprecated(app, verb):
    """Setup verbs must point users to the CLI / Web UI instead of executing."""
    result = handle_slash_command(app, f"/connector {verb} github")
    text = _text(result).lower()
    assert (
        "not supported in chat" in text
        or "use the web ui" in text
        or "mycelos connector setup" in text
    ), f"Expected deprecation notice for /connector {verb}, got: {text!r}"


# --- Usage help ---

def test_connector_no_args_shows_list(app):
    """No args should show the connector list (not usage)."""
    result = handle_slash_command(app, "/connector")
    assert "Available" in _text(result) or "github" in _text(result).lower()
