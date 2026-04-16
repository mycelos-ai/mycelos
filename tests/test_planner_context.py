"""Tests for Planner Context Builder."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

from mycelos.agents.planner_context import (
    build_planner_context,
    format_context_for_prompt,
)
from mycelos.app import App


@pytest.fixture
def app() -> App:
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["MYCELOS_MASTER_KEY"] = "test-key-planner-ctx"
        a = App(Path(tmp))
        a.initialize()
        yield a


def test_empty_system_context(app):
    """Fresh system should have no custom agents, but has built-in workflows."""
    ctx = build_planner_context(app)
    assert ctx["available_agents"] == []
    # Built-in workflows are seeded on init
    assert len(ctx["available_workflows"]) >= 3
    assert ctx["available_capabilities"] == []
    assert ctx["available_connectors"] == []


def test_context_includes_agents(app):
    app.agent_registry.register(
        "test-agent", "Test", "deterministic", ["search.web"], "system"
    )
    app.agent_registry.set_status("test-agent", "active")

    ctx = build_planner_context(app)
    assert len(ctx["available_agents"]) == 1
    assert ctx["available_agents"][0]["id"] == "test-agent"
    assert "search.web" in ctx["available_agents"][0]["capabilities"]


def test_context_includes_workflows(app):
    app.workflow_registry.register(
        "news-wf",
        "News",
        [{"id": "s1"}],
        description="Search news",
        scope=["search.web"],
    )

    ctx = build_planner_context(app)
    # 3 built-in + 1 custom
    wf_ids = [w["id"] for w in ctx["available_workflows"]]
    assert "news-wf" in wf_ids
    news_wf = next(w for w in ctx["available_workflows"] if w["id"] == "news-wf")
    assert news_wf["steps"] == 1


def test_context_includes_capabilities(app):
    app.connector_registry.register(
        "ddg", "DuckDuckGo", "search", ["search.web", "search.news"]
    )

    ctx = build_planner_context(app)
    assert "search.web" in ctx["available_capabilities"]
    assert "search.news" in ctx["available_capabilities"]


def test_context_includes_connectors(app):
    app.connector_registry.register("ddg", "DuckDuckGo", "search", ["search.web"])
    app.connector_registry.register("http", "HTTP", "http", ["http.get"])

    ctx = build_planner_context(app)
    connector_names = [c["name"] for c in ctx["available_connectors"]]
    assert "DuckDuckGo" in connector_names
    assert "HTTP" in connector_names
    # Should include capabilities
    ddg = [c for c in ctx["available_connectors"] if c["id"] == "ddg"][0]
    assert "search.web" in ddg["capabilities"]


def test_full_system_context(app):
    """System with agents, workflows, and connectors."""
    app.agent_registry.register(
        "a1", "Agent1", "deterministic", ["search.web"], "system"
    )
    app.agent_registry.set_status("a1", "active")
    app.workflow_registry.register("wf1", "WF1", [{"id": "s1"}], scope=["search.web"])
    app.connector_registry.register(
        "ddg", "DDG", "search", ["search.web", "search.news"]
    )

    ctx = build_planner_context(app)
    assert len(ctx["available_agents"]) >= 1
    assert len(ctx["available_workflows"]) >= 1
    assert len(ctx["available_capabilities"]) >= 2
    assert len(ctx["available_connectors"]) >= 1


def test_format_context_for_prompt_empty():
    ctx = {
        "available_agents": [],
        "available_workflows": [],
        "available_capabilities": [],
        "available_connectors": [],
    }
    formatted = format_context_for_prompt(ctx)
    assert "No custom agents" in formatted
    assert "No workflows" in formatted


def test_format_context_for_prompt_with_data():
    ctx = {
        "available_agents": [
            {
                "id": "a1",
                "name": "Test",
                "type": "deterministic",
                "capabilities": ["search.web"],
            }
        ],
        "available_workflows": [
            {
                "id": "wf1",
                "name": "WF",
                "description": "Test WF",
                "steps": 3,
                "scope": ["search.web"],
                "tags": [],
            }
        ],
        "available_capabilities": ["search.web", "http.get"],
        "available_connectors": ["DuckDuckGo"],
    }
    formatted = format_context_for_prompt(ctx)
    assert "a1" in formatted
    assert "search.web" in formatted
    assert "wf1" in formatted
    assert "DuckDuckGo" in formatted


def test_format_context_readable():
    """Formatted context should be multi-line and human-readable."""
    ctx = {
        "available_agents": [
            {
                "id": "news",
                "name": "News",
                "type": "light_model",
                "capabilities": ["search.web"],
            }
        ],
        "available_workflows": [],
        "available_capabilities": ["search.web"],
        "available_connectors": ["DDG"],
    }
    formatted = format_context_for_prompt(ctx)
    assert "\n" in formatted  # Multi-line
    assert "###" in formatted  # Has headers


def test_only_active_agents_included(app):
    """Proposed/deprecated agents should not appear."""
    app.agent_registry.register(
        "active-one", "Active", "deterministic", [], "system"
    )
    app.agent_registry.set_status("active-one", "active")
    app.agent_registry.register(
        "proposed-one", "Proposed", "deterministic", [], "system"
    )
    # proposed-one stays in default "proposed" status

    ctx = build_planner_context(app)
    agent_ids = [a["id"] for a in ctx["available_agents"]]
    assert "active-one" in agent_ids
    assert "proposed-one" not in agent_ids
