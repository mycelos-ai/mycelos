import pytest
from unittest.mock import patch, MagicMock

from mycelos.llm.broker import LiteLLMBroker
from mycelos.protocols import LLMBroker


def _mock_completion_response(content="Hello!", total_tokens=50):
    """Create a mock litellm completion response."""
    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = content
    mock_response.choices[0].message.tool_calls = None
    mock_response.usage.total_tokens = total_tokens
    return mock_response


def test_implements_protocol():
    assert isinstance(LiteLLMBroker.__new__(LiteLLMBroker), LLMBroker)


def test_complete_calls_litellm():
    broker = LiteLLMBroker(default_model="claude-haiku-4-5-20251001")

    with patch("litellm.completion", return_value=_mock_completion_response()) as mock_completion:
        result = broker.complete(
            messages=[{"role": "user", "content": "Hi"}]
        )

        mock_completion.assert_called_once()
        assert result.content == "Hello!"
        assert result.total_tokens == 50


def test_default_model_used():
    broker = LiteLLMBroker(default_model="claude-haiku-4-5-20251001")

    with patch("litellm.completion", return_value=_mock_completion_response("test", 10)) as mock_completion:
        broker.complete(messages=[{"role": "user", "content": "test"}])

        call_kwargs = mock_completion.call_args
        # Broker auto-prefixes bare model IDs
        assert call_kwargs.kwargs["model"] == "anthropic/claude-haiku-4-5-20251001"


def test_model_override():
    broker = LiteLLMBroker(default_model="claude-haiku-4-5-20251001")

    with patch("litellm.completion", return_value=_mock_completion_response("test", 10)) as mock_completion:
        broker.complete(
            messages=[{"role": "user", "content": "test"}],
            model="claude-opus-4-20250514",
        )

        call_kwargs = mock_completion.call_args
        # Broker auto-prefixes bare model IDs
        assert call_kwargs.kwargs["model"] == "anthropic/claude-opus-4-20250514"


def test_count_tokens():
    broker = LiteLLMBroker(default_model="claude-haiku-4-5-20251001")

    with patch("litellm.token_counter", return_value=42):
        count = broker.count_tokens(
            messages=[{"role": "user", "content": "Hello world"}]
        )
        assert count == 42
