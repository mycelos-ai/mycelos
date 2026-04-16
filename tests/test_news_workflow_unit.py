"""Unit tests for the news summary workflow template (no real API calls)."""

from pathlib import Path

import yaml
import pytest


TEMPLATE_PATH = Path(__file__).parent.parent / "artifacts" / "workflows" / "news-summary.yaml"


def test_news_workflow_yaml_exists() -> None:
    """The news-summary.yaml workflow file exists."""
    assert TEMPLATE_PATH.exists(), f"Missing: {TEMPLATE_PATH}"


def test_news_workflow_parses() -> None:
    """The workflow YAML parses without errors."""
    data = yaml.safe_load(TEMPLATE_PATH.read_text())
    assert data["name"] == "news-summary"


def test_news_workflow_has_correct_scope() -> None:
    """Workflow declares the correct capabilities."""
    data = yaml.safe_load(TEMPLATE_PATH.read_text())
    assert "search.news" in data["scope"]
    assert "http.get" in data["scope"]


def test_news_workflow_has_plan() -> None:
    """Workflow has a plan for WorkflowAgent."""
    data = yaml.safe_load(TEMPLATE_PATH.read_text())
    assert "plan" in data
    assert "search_news" in data["plan"]


def test_news_workflow_has_allowed_tools() -> None:
    """Workflow lists allowed tools."""
    data = yaml.safe_load(TEMPLATE_PATH.read_text())
    assert "search_news" in data["allowed_tools"]
    assert "http_get" in data["allowed_tools"]


def test_news_workflow_has_model() -> None:
    """Workflow specifies a model tier."""
    data = yaml.safe_load(TEMPLATE_PATH.read_text())
    assert data["model"] == "haiku"


def test_news_workflow_has_inputs() -> None:
    """Workflow declares expected inputs."""
    data = yaml.safe_load(TEMPLATE_PATH.read_text())
    assert "inputs" in data
    input_names = [i["name"] for i in data["inputs"]]
    assert "topic" in input_names


def test_news_workflow_topic_input_required() -> None:
    """The topic input is required."""
    data = yaml.safe_load(TEMPLATE_PATH.read_text())
    topic = next(i for i in data["inputs"] if i["name"] == "topic")
    assert topic["required"] is True


def test_news_workflow_tags() -> None:
    """Workflow has expected tags."""
    data = yaml.safe_load(TEMPLATE_PATH.read_text())
    assert set(data["tags"]) == {"news", "research", "summary"}


def test_news_workflow_requires() -> None:
    """Workflow has requires section."""
    data = yaml.safe_load(TEMPLATE_PATH.read_text())
    assert "requires" in data
    assert "search_news" in data["requires"]["tools"]
