"""Tests for /connector slash commands — MCP connector setup."""

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


# --- /connector add (recipe-based) ---

def test_connector_add_unknown(app):
    result = handle_slash_command(app, "/connector add nonexistent")
    text = _text(result)
    assert "Unknown" in text or "unknown" in text


def test_connector_add_fetch_no_key(app):
    """Fetch needs no key — should activate immediately."""
    result = handle_slash_command(app, "/connector add fetch")
    assert "activated" in _text(result).lower() or "bereit" in _text(result).lower() or "no API key" in _text(result)

    # Should be registered
    conn = app.connector_registry.get("fetch")
    assert conn is not None


def test_connector_add_github_needs_key(app):
    """GitHub needs a token — should show instructions."""
    result = handle_slash_command(app, "/connector add github")
    assert "token" in _text(result).lower() or "Token" in _text(result)
    assert "github.com" in _text(result) or "settings/tokens" in _text(result)


def test_connector_add_brave_shows_help(app):
    result = handle_slash_command(app, "/connector add brave-search")
    assert "brave.com" in _text(result).lower() or "API" in _text(result)


def test_connector_add_already_active(app):
    app.connector_registry.register("fetch", "Fetch", "mcp", ["fetch"])
    result = handle_slash_command(app, "/connector add fetch")
    assert "already" in _text(result).lower()


# --- /connector add-custom ---

def test_connector_add_custom(app):
    # New syntax: /connector add <name> npx ... (auto-detects MCP command)
    result = handle_slash_command(app, "/connector add notion npx @sirodrigo/mcp-notion")
    assert "registered" in _text(result).lower() or "Custom" in _text(result)

    conn = app.connector_registry.get("notion")
    assert conn is not None


# --- /connector remove ---

def test_connector_remove(app):
    app.connector_registry.register("test-rm", "Test", "mcp", ["test.cap"])
    result = handle_slash_command(app, "/connector remove test-rm")
    assert "deactivated" in _text(result).lower() or "Deactivated" in _text(result)


def test_connector_remove_nonexistent(app):
    result = handle_slash_command(app, "/connector remove nope")
    assert "not found" in _text(result).lower()


# --- /connector test ---

def test_connector_test(app):
    app.connector_registry.register("test-t", "Test", "mcp", [])
    result = handle_slash_command(app, "/connector test test-t")
    assert "test-t" in _text(result)


# --- Config generation ---

def test_connector_add_creates_generation(app):
    gen_before = app.config.get_active_generation_id()
    handle_slash_command(app, "/connector add fetch")
    gen_after = app.config.get_active_generation_id()
    assert gen_after != gen_before


# --- Usage help ---

def test_connector_no_args_shows_list(app):
    """No args should show the connector list (not usage)."""
    result = handle_slash_command(app, "/connector")
    assert "Available" in _text(result) or "github" in _text(result).lower()
