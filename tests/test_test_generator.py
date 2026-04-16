"""Tests for Test Generator — creates pytest code from Gherkin scenarios."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from mycelos.agents.agent_spec import AgentSpec
from mycelos.agents.test_generator import (
    generate_tests,
    _spec_to_class_name,
    _clean_code_output,
    TEST_GEN_PROMPT,
)


SAMPLE_GHERKIN = """\
Feature: News Search
  Scenario: Find news articles
    Given a search query "AI news"
    When the agent searches
    Then it should return results

  Scenario: Handle empty results
    Given a search query with no results
    When the agent searches
    Then it should return an empty list
"""

SAMPLE_TEST_OUTPUT = """\
```python
import pytest
from unittest.mock import MagicMock, patch
from agent_code import NewsAgent

def test_find_news_articles():
    agent = NewsAgent()
    input = MagicMock(task="AI news", context={})
    result = agent.execute(input)
    assert result.success
    assert result.result is not None

def test_handle_empty_results():
    agent = NewsAgent()
    input = MagicMock(task="nonexistent topic xyz", context={})
    result = agent.execute(input)
    assert result.success
    assert result.result == []
```
"""


def test_generate_tests_calls_llm():
    spec = AgentSpec(name="news-agent", description="Search news")
    mock_llm = MagicMock()
    mock_llm.complete.return_value = MagicMock(content="def test_something(): pass")

    result = generate_tests(spec, SAMPLE_GHERKIN, mock_llm)

    mock_llm.complete.assert_called_once()
    assert "def test_" in result


def test_generate_tests_includes_gherkin():
    spec = AgentSpec(name="test", description="test")
    mock_llm = MagicMock()
    mock_llm.complete.return_value = MagicMock(content="# tests")

    generate_tests(spec, SAMPLE_GHERKIN, mock_llm)

    call_args = mock_llm.complete.call_args
    messages = call_args.kwargs.get("messages") or call_args[0][0]
    system_msg = messages[0]["content"]
    assert "Find news articles" in system_msg


def test_generate_tests_with_model():
    spec = AgentSpec(name="test", description="test")
    mock_llm = MagicMock()
    mock_llm.complete.return_value = MagicMock(content="# tests")

    generate_tests(spec, "Feature: T", mock_llm, model="haiku")

    assert mock_llm.complete.call_args.kwargs.get("model") == "haiku"


def test_spec_to_class_name():
    assert _spec_to_class_name("news-agent") == "NewsAgent"
    assert _spec_to_class_name("pdf_summarizer") == "PdfSummarizer"
    assert _spec_to_class_name("simple") == "Simple"
    assert _spec_to_class_name("my-cool-agent") == "MyCoolAgent"


def test_clean_code_output_strips_fences():
    assert _clean_code_output("```python\ncode\n```") == "code"
    assert _clean_code_output("```\ncode\n```") == "code"
    assert _clean_code_output("code") == "code"


def test_clean_code_output_strips_whitespace():
    assert _clean_code_output("  \n```python\ncode\n```\n  ") == "code"


def test_prompt_has_placeholders():
    assert "{gherkin_scenarios}" in TEST_GEN_PROMPT
    assert "{spec_context}" in TEST_GEN_PROMPT
    assert "{agent_class}" in TEST_GEN_PROMPT
