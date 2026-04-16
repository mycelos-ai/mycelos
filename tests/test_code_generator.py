"""Tests for Code Generator — creates agent code to pass tests."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from mycelos.agents.agent_spec import AgentSpec
from mycelos.agents.code_generator import (
    generate_code,
    CODE_GEN_PROMPT,
    CODE_RETRY_PROMPT,
)


SAMPLE_TESTS = """\
def test_search():
    agent = NewsAgent()
    result = agent.execute(MagicMock(task="AI"))
    assert result.success
"""

SAMPLE_CODE = """\
class NewsAgent:
    def execute(self, input):
        return AgentOutput(success=True, result=[], artifacts=[], metadata={})
"""


def test_generate_code_calls_llm():
    spec = AgentSpec(name="news-agent", description="Search news")
    mock_llm = MagicMock()
    mock_llm.complete.return_value = MagicMock(content=SAMPLE_CODE)

    result = generate_code(spec, SAMPLE_TESTS, mock_llm)

    mock_llm.complete.assert_called_once()
    assert "class" in result or "def" in result


def test_generate_code_includes_tests():
    spec = AgentSpec(name="test", description="test")
    mock_llm = MagicMock()
    mock_llm.complete.return_value = MagicMock(content="# code")

    generate_code(spec, SAMPLE_TESTS, mock_llm)

    call_args = mock_llm.complete.call_args
    messages = call_args.kwargs.get("messages") or call_args[0][0]
    system_msg = messages[0]["content"]
    assert "test_search" in system_msg


def test_generate_code_with_model():
    spec = AgentSpec(name="test", description="test")
    mock_llm = MagicMock()
    mock_llm.complete.return_value = MagicMock(content="# code")

    generate_code(spec, "# tests", mock_llm, model="opus")

    assert mock_llm.complete.call_args.kwargs.get("model") == "opus"


def test_generate_code_retry_uses_error():
    spec = AgentSpec(name="test", description="test")
    mock_llm = MagicMock()
    mock_llm.complete.return_value = MagicMock(content="# fixed code")

    generate_code(
        spec,
        SAMPLE_TESTS,
        mock_llm,
        previous_code="# broken code",
        test_error="AssertionError: expected True got False",
    )

    call_args = mock_llm.complete.call_args
    messages = call_args.kwargs.get("messages") or call_args[0][0]
    system_msg = messages[0]["content"]
    assert "FAILED" in system_msg or "error" in system_msg.lower()
    assert "broken code" in system_msg


def test_generate_code_strips_fences():
    spec = AgentSpec(name="test", description="test")
    mock_llm = MagicMock()
    mock_llm.complete.return_value = MagicMock(content="```python\ncode_here\n```")

    result = generate_code(spec, "# tests", mock_llm)
    assert result == "code_here"


def test_generate_code_includes_capabilities():
    spec = AgentSpec(
        name="mailer",
        description="Send emails",
        capabilities_needed=["google.gmail.send"],
    )
    mock_llm = MagicMock()
    mock_llm.complete.return_value = MagicMock(content="# code")

    generate_code(spec, "# tests", mock_llm)

    call_args = mock_llm.complete.call_args
    messages = call_args.kwargs.get("messages") or call_args[0][0]
    system_msg = messages[0]["content"]
    assert "google.gmail.send" in system_msg


def test_prompt_templates_have_placeholders():
    assert "{test_code}" in CODE_GEN_PROMPT
    assert "{spec_context}" in CODE_GEN_PROMPT
    assert "{reference_agent}" in CODE_GEN_PROMPT
    assert "{test_error}" in CODE_RETRY_PROMPT
    assert "{previous_code}" in CODE_RETRY_PROMPT
