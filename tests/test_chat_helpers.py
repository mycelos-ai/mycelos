# tests/test_chat_helpers.py
"""Tests for chat_cmd helper functions."""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from mycelos.cli.chat_cmd import _resolve_workflow, _extract_inputs, _format_execution_result


def test_extract_inputs_from_description():
    plan = {"description": "AI news", "steps": []}
    inputs = _extract_inputs(plan)
    assert inputs["query"] == "AI news"


def test_extract_inputs_from_search_step():
    plan = {"steps": [{"action": "Search for latest AI developments"}]}
    inputs = _extract_inputs(plan)
    assert "query" in inputs


def test_extract_inputs_empty_plan():
    assert _extract_inputs(None) == {}
    assert _extract_inputs({}) == {}


def test_format_execution_result_list():
    """Format list results (search results) into markdown."""
    mock_output = MagicMock()
    mock_output.result = [
        {"title": "Article 1", "url": "https://ex.com/1", "snippet": "First article"},
        {"title": "Article 2", "url": "https://ex.com/2", "snippet": "Second article"},
    ]
    mock_result = MagicMock()
    mock_result.step_results = {"search": mock_output}

    text = _format_execution_result(mock_result)
    assert "Article 1" in text
    assert "Article 2" in text
    assert "https://ex.com/1" in text


def test_format_execution_result_string():
    """Format string results into markdown."""
    mock_output = MagicMock()
    mock_output.result = "Summary of all articles"
    mock_result = MagicMock()
    mock_result.step_results = {"summarize": mock_output}

    text = _format_execution_result(mock_result)
    assert "Summary of all articles" in text


def test_resolve_workflow_from_plan_description():
    """Build ad-hoc workflow dict from plan description."""
    app = MagicMock()
    app.workflow_registry.get.return_value = None
    app.workflow_registry.list_workflows.return_value = []

    plan = {
        "description": "Search for AI news and summarize",
    }
    wf = _resolve_workflow(app, plan, None)
    assert wf is not None
    assert "plan" in wf
    assert "model" in wf
    assert "allowed_tools" in wf


def test_resolve_workflow_from_registry():
    """Resolve workflow from registry by name."""
    app = MagicMock()
    app.workflow_registry.get.return_value = {
        "id": "news-summary",
        "name": "News Summary",
        "plan": "Search and summarize news.",
        "model": "haiku",
        "allowed_tools": ["search_news", "http_get"],
    }

    wf = _resolve_workflow(app, None, "news-summary")
    assert wf is not None
    assert wf["plan"] == "Search and summarize news."


def test_resolve_workflow_returns_none_for_empty():
    """Return None when plan has no description and no workflow name."""
    app = MagicMock()
    app.workflow_registry.get.return_value = None
    app.workflow_registry.list_workflows.return_value = []
    assert _resolve_workflow(app, None, None) is None
    assert _resolve_workflow(app, {}, None) is None
