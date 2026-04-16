"""Tests for Chat Orchestrator."""

import json

import pytest

from mycelos.llm.mock_broker import MockLLMBroker
from mycelos.orchestrator import ChatOrchestrator, Intent


@pytest.fixture
def orchestrator() -> ChatOrchestrator:
    """Orchestrator with a default conversation response."""
    broker = MockLLMBroker().on_message(
        r".*", json.dumps({"intent": "conversation", "confidence": 0.9})
    )
    return ChatOrchestrator(llm=broker)


def test_classify_conversation(orchestrator: ChatOrchestrator) -> None:
    """Greetings and questions classify as CONVERSATION."""
    assert orchestrator.classify("Hello!") == Intent.CONVERSATION


def test_classify_task_request() -> None:
    """Action requests classify as TASK_REQUEST."""
    broker = MockLLMBroker().on_message(
        r".*", json.dumps({"intent": "task_request"})
    )
    assert ChatOrchestrator(llm=broker).classify("Summarize my emails") == Intent.TASK_REQUEST


def test_classify_create_agent() -> None:
    """Agent creation requests classify as CREATE_AGENT."""
    broker = MockLLMBroker().on_message(
        r".*", json.dumps({"intent": "create_agent"})
    )
    assert ChatOrchestrator(llm=broker).classify("Create a PR reviewer") == Intent.CREATE_AGENT


def test_classify_system_command() -> None:
    """System queries classify as SYSTEM_COMMAND."""
    broker = MockLLMBroker().on_message(
        r".*", json.dumps({"intent": "system_command"})
    )
    assert ChatOrchestrator(llm=broker).classify("Show config") == Intent.SYSTEM_COMMAND


def test_classify_defaults_on_invalid_json() -> None:
    """Invalid JSON from LLM falls back to CONVERSATION."""
    broker = MockLLMBroker().on_message(r".*", "not json")
    assert ChatOrchestrator(llm=broker).classify("x") == Intent.CONVERSATION


def test_classify_defaults_on_unknown_intent() -> None:
    """Unknown intent value falls back to CONVERSATION."""
    broker = MockLLMBroker().on_message(
        r".*", json.dumps({"intent": "unknown_category"})
    )
    assert ChatOrchestrator(llm=broker).classify("x") == Intent.CONVERSATION


def test_classify_defaults_on_missing_intent_key() -> None:
    """Missing intent key in JSON falls back to CONVERSATION."""
    broker = MockLLMBroker().on_message(r".*", json.dumps({"other": "value"}))
    assert ChatOrchestrator(llm=broker).classify("x") == Intent.CONVERSATION


def test_classify_uses_cheap_model() -> None:
    """When classifier_model is set, it is forwarded to the LLM broker."""
    broker = MockLLMBroker().on_message(
        r".*", json.dumps({"intent": "conversation"})
    )
    orch = ChatOrchestrator(llm=broker, classifier_model="anthropic/claude-haiku-4-5")
    orch.classify("test")
    assert broker.call_log[0]["model"] == "anthropic/claude-haiku-4-5"


def test_classify_without_model_override() -> None:
    """Without classifier_model, model=None is passed to the broker."""
    broker = MockLLMBroker().on_message(
        r".*", json.dumps({"intent": "conversation"})
    )
    orch = ChatOrchestrator(llm=broker)
    orch.classify("test")
    assert broker.call_log[0]["model"] is None


def test_classify_sends_system_prompt() -> None:
    """The classifier prompt is sent as the system message."""
    broker = MockLLMBroker().on_message(
        r".*", json.dumps({"intent": "conversation"})
    )
    ChatOrchestrator(llm=broker).classify("Hello")
    messages = broker.call_log[0]["messages"]
    assert messages[0]["role"] == "system"
    assert "Classify" in messages[0]["content"]
    assert messages[1]["role"] == "user"
    assert messages[1]["content"] == "Hello"
