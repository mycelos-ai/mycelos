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


# -- EU-mode enforcement (P1-5) --

from mycelos.llm.eu_enforcement import EUResidencyError


def test_eu_mode_denies_non_eu_model():
    """With EU mode on, a US-provider model must be denied fail-closed,
    never sent to litellm."""
    broker = LiteLLMBroker(
        default_model="anthropic/claude-sonnet-4-6",
        eu_mode_check=lambda: True,
    )
    with patch("litellm.completion", return_value=_mock_completion_response()) as mock_completion:
        with pytest.raises(EUResidencyError):
            broker.complete(messages=[{"role": "user", "content": "Hi"}])
        mock_completion.assert_not_called()


def test_eu_mode_allows_eu_model():
    broker = LiteLLMBroker(
        default_model="mistral/mistral-large-latest",
        eu_mode_check=lambda: True,
    )
    with patch("litellm.completion", return_value=_mock_completion_response()) as mock_completion:
        result = broker.complete(messages=[{"role": "user", "content": "Hi"}])
        mock_completion.assert_called_once()
        assert result.content == "Hello!"


def test_eu_mode_filters_us_fallback():
    """The fallback chain must be filtered too — a rate-limited EU primary must
    NOT fail over to a US provider (the silent-exfiltration path)."""
    broker = LiteLLMBroker(
        default_model="mistral/mistral-large-latest",
        fallback_models=["anthropic/claude-sonnet-4-6"],
        eu_mode_check=lambda: True,
    )

    def _raise_rate_limit(*a, **k):
        raise RuntimeError("rate limit exceeded")

    with patch("litellm.completion", side_effect=_raise_rate_limit) as mock_completion:
        # Primary rate-limits; the only fallback is US, so with EU mode on it
        # must be filtered out and the call must raise rather than fail over.
        with pytest.raises(Exception) as exc:
            broker.complete(messages=[{"role": "user", "content": "Hi"}])
        # It must not be a success via the US fallback.
        assert not isinstance(exc.value, type(None))
        # litellm was called only for the EU primary, never the US fallback.
        for call in mock_completion.call_args_list:
            assert "anthropic" not in call.kwargs.get("model", "")


def test_eu_mode_off_allows_us_model():
    """Default (EU mode off) must not change behavior."""
    broker = LiteLLMBroker(
        default_model="anthropic/claude-sonnet-4-6",
        eu_mode_check=lambda: False,
    )
    with patch("litellm.completion", return_value=_mock_completion_response()) as mock_completion:
        result = broker.complete(messages=[{"role": "user", "content": "Hi"}])
        mock_completion.assert_called_once()
        assert result.content == "Hello!"
