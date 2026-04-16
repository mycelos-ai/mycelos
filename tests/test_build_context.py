"""Tests for _build_context -- dynamic Creator-Agent context from DB."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

from mycelos.app import App
from mycelos.cli.chat_cmd import _build_context


@pytest.fixture
def app() -> App:
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["MYCELOS_MASTER_KEY"] = "test-key-context"
        a = App(Path(tmp))
        a.initialize()
        yield a


def test_empty_context(app: App) -> None:
    """Empty DB should show no connectors, no agents."""
    ctx = _build_context(app)
    assert "No connectors" in ctx or "Available Connectors" not in ctx
    assert "No agents" in ctx or "Active Agents" not in ctx


def test_context_shows_connectors(app: App) -> None:
    """Registered connectors should appear in context."""
    app.connector_registry.register(
        "ddg", "DuckDuckGo", "search", ["search.web", "search.news"]
    )
    ctx = _build_context(app)
    assert "DuckDuckGo" in ctx
    assert "search.web" in ctx


def test_context_shows_agents(app: App) -> None:
    """Active agents should appear in context."""
    app.agent_registry.register(
        "a1", "news-agent", "deterministic", ["search.web"], "system"
    )
    app.agent_registry.set_status("a1", "active")
    ctx = _build_context(app)
    assert "news-agent" in ctx
    assert "search.web" in ctx


def test_context_shows_system_config(app: App) -> None:
    """System config should appear."""
    ctx = _build_context(app)
    assert "System Config" in ctx or "Provider" in ctx


def test_context_only_active_connectors(app: App) -> None:
    """Inactive connectors should not appear."""
    app.connector_registry.register("ddg", "DDG", "search", ["search.web"])
    app.connector_registry.register("old", "Old", "search", ["search.old"])
    app.connector_registry.set_status("old", "inactive")
    ctx = _build_context(app)
    assert "DDG" in ctx
    assert "Old" not in ctx


def test_context_only_active_agents(app: App) -> None:
    """Non-active agents should not appear in the active agents section."""
    app.agent_registry.register(
        "a1", "active-agent", "deterministic", ["cap.one"], "system"
    )
    app.agent_registry.set_status("a1", "active")
    app.agent_registry.register(
        "a2", "proposed-agent", "llm", ["cap.two"], "system"
    )
    # a2 stays in default status (proposed), not active
    ctx = _build_context(app)
    assert "active-agent" in ctx
    assert "proposed-agent" not in ctx


def test_context_agent_no_capabilities(app: App) -> None:
    """Agent with no capabilities should show 'keine'."""
    app.agent_registry.register("a1", "empty-agent", "deterministic", [], "system")
    app.agent_registry.set_status("a1", "active")
    ctx = _build_context(app)
    assert "empty-agent" in ctx
    assert "none" in ctx


def test_context_connector_capabilities_listed(app: App) -> None:
    """All connector capabilities should be comma-separated in context."""
    app.connector_registry.register(
        "gh", "GitHub", "api", ["repo.read", "repo.write", "issues.list"]
    )
    ctx = _build_context(app)
    assert "repo.read" in ctx
    assert "repo.write" in ctx
    assert "issues.list" in ctx
    assert "GitHub" in ctx
    assert "api" in ctx
