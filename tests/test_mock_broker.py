"""Tests for MockLLMBroker — deterministic LLM testing."""

import pytest

from mycelos.llm.mock_broker import MockLLMBroker


def test_pattern_match_response() -> None:
    broker = MockLLMBroker()
    broker.on_message(r".*email.*", "Here are your emails.")
    result = broker.complete([{"role": "user", "content": "Summarize my emails"}])
    assert result.content == "Here are your emails."


def test_default_response() -> None:
    broker = MockLLMBroker()
    result = broker.complete([{"role": "user", "content": "random question"}])
    assert result.content  # default response is not empty


def test_multiple_patterns_first_match() -> None:
    broker = MockLLMBroker()
    broker.on_message(r".*email.*", "Email response")
    broker.on_message(r".*github.*", "GitHub response")
    result = broker.complete([{"role": "user", "content": "Check my emails on github"}])
    assert result.content == "Email response"


def test_call_log() -> None:
    broker = MockLLMBroker()
    broker.on_message(r".*", "ok")
    broker.complete([{"role": "user", "content": "hello"}])
    broker.complete([{"role": "user", "content": "world"}])
    assert len(broker.call_log) == 2
    assert broker.call_log[0]["messages"][0]["content"] == "hello"


def test_tool_calls_in_response() -> None:
    broker = MockLLMBroker()
    broker.on_message(r".*create.*", "Creating...", tool_calls=[
        {"id": "1", "function": {"name": "create_task", "arguments": "{}"}}
    ])
    result = broker.complete([{"role": "user", "content": "create a workflow"}])
    assert result.tool_calls is not None
    assert result.tool_calls[0]["function"]["name"] == "create_task"


def test_count_tokens() -> None:
    broker = MockLLMBroker()
    count = broker.count_tokens([{"role": "user", "content": "Hello world!"}])
    assert count > 0


def test_chainable_api() -> None:
    broker = (
        MockLLMBroker()
        .on_message(r".*a.*", "response a")
        .on_message(r".*b.*", "response b")
    )
    assert broker.complete([{"role": "user", "content": "a"}]).content == "response a"
    assert broker.complete([{"role": "user", "content": "b"}]).content == "response b"
