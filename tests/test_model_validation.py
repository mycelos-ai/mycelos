"""Tests for model configuration plausibility checks and connectivity tests."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from mycelos.llm.providers import ModelInfo
from mycelos.llm.validation import (
    ConnectivityResult,
    ValidationReport,
    check_model_connectivity,
    validate_model_config,
)


# ---------------------------------------------------------------------------
# Fixtures: reusable model configurations
# ---------------------------------------------------------------------------

def _model(
    id: str, tier: str, provider: str,
    input_cost: float = 0.003, output_cost: float = 0.015,
) -> ModelInfo:
    return ModelInfo(
        id=id, name=id, tier=tier, provider=provider,
        input_cost_per_1k=input_cost, output_cost_per_1k=output_cost,
    )


SONNET_ANTHROPIC = _model("claude-sonnet-4-6", "sonnet", "anthropic")
HAIKU_ANTHROPIC = _model("claude-haiku-4-5", "haiku", "anthropic", 0.0008, 0.004)
OPUS_ANTHROPIC = _model("claude-opus-4-6", "opus", "anthropic", 0.015, 0.075)
GPT4O = _model("gpt-4o", "sonnet", "openai")
GPT4O_MINI = _model("gpt-4o-mini", "haiku", "openai", 0.0005, 0.002)
GEMINI_FLASH = _model("gemini-2.5-flash", "haiku", "gemini", 0.0, 0.0)


# ---------------------------------------------------------------------------
# Plausibility checks
# ---------------------------------------------------------------------------

class TestValidateModelConfig:
    """Tests for validate_model_config plausibility checks."""

    def test_both_tiers_present_no_warnings(self) -> None:
        """With both capable and cheap models, no warnings should appear."""
        report = validate_model_config([SONNET_ANTHROPIC, HAIKU_ANTHROPIC])

        assert report.is_healthy
        assert not report.has_warnings
        assert report.has_capable_tier
        assert report.has_cheap_tier
        assert report.model_count == 2

    def test_only_expensive_models_warns_missing_cheap(self) -> None:
        """Only sonnet/opus models should warn about missing haiku tier."""
        report = validate_model_config([SONNET_ANTHROPIC, OPUS_ANTHROPIC])

        assert report.has_warnings
        assert report.has_capable_tier
        assert not report.has_cheap_tier

        codes = [i.code for i in report.issues]
        assert "missing_cheap_tier" in codes

        # Should suggest a cheap model
        cheap_issue = next(i for i in report.issues if i.code == "missing_cheap_tier")
        assert "classification" in cheap_issue.message.lower()
        assert cheap_issue.suggestion

    def test_only_cheap_models_warns_missing_capable(self) -> None:
        """Only haiku models should warn about missing sonnet/opus tier."""
        report = validate_model_config([HAIKU_ANTHROPIC])

        assert report.has_warnings
        assert not report.has_capable_tier
        assert report.has_cheap_tier

        codes = [i.code for i in report.issues]
        assert "missing_capable_tier" in codes

        capable_issue = next(i for i in report.issues if i.code == "missing_capable_tier")
        assert "complex tasks" in capable_issue.message.lower()
        assert capable_issue.suggestion

    def test_no_models_warns(self) -> None:
        """Empty model list should produce a warning."""
        report = validate_model_config([])

        assert report.has_warnings
        codes = [i.code for i in report.issues]
        assert "no_models" in codes

    def test_single_provider_info(self) -> None:
        """All models from one provider should produce an info about resilience."""
        report = validate_model_config([SONNET_ANTHROPIC, HAIKU_ANTHROPIC])

        assert report.provider_count == 1
        codes = [i.code for i in report.issues]
        assert "single_provider" in codes

        info_issue = next(i for i in report.issues if i.code == "single_provider")
        assert info_issue.level == "info"
        assert "failover" in info_issue.suggestion.lower()

    def test_multi_provider_no_resilience_warning(self) -> None:
        """Models from multiple providers should not show single_provider info."""
        report = validate_model_config([SONNET_ANTHROPIC, GPT4O_MINI])

        assert report.provider_count == 2
        codes = [i.code for i in report.issues]
        assert "single_provider" not in codes

    def test_three_providers_no_warning(self) -> None:
        """Three providers = good resilience, no single_provider info."""
        report = validate_model_config([SONNET_ANTHROPIC, GPT4O_MINI, GEMINI_FLASH])

        assert report.provider_count == 3
        assert "single_provider" not in [i.code for i in report.issues]

    def test_opus_counts_as_capable(self) -> None:
        """Opus tier should satisfy the capable-tier requirement."""
        report = validate_model_config([OPUS_ANTHROPIC, HAIKU_ANTHROPIC])

        assert report.has_capable_tier
        assert "missing_capable_tier" not in [i.code for i in report.issues]

    def test_suggestion_matches_provider(self) -> None:
        """Cheap model suggestions should match the configured provider."""
        report = validate_model_config([GPT4O])  # Only OpenAI sonnet

        cheap_issue = next(i for i in report.issues if i.code == "missing_cheap_tier")
        assert "gpt-4o-mini" in cheap_issue.suggestion

    def test_capable_suggestion_matches_provider(self) -> None:
        """Capable model suggestions should match the configured provider."""
        report = validate_model_config([GPT4O_MINI])  # Only OpenAI haiku

        capable_issue = next(i for i in report.issues if i.code == "missing_capable_tier")
        assert "gpt-4o" in capable_issue.suggestion

    def test_single_provider_is_info_not_warning(self) -> None:
        """Single provider should be info level, not warning — it's not blocking."""
        report = validate_model_config([SONNET_ANTHROPIC, HAIKU_ANTHROPIC])

        info_issues = [i for i in report.issues if i.code == "single_provider"]
        assert len(info_issues) == 1
        assert info_issues[0].level == "info"
        # Info-level issues should NOT make has_warnings True
        assert not report.has_warnings


# ---------------------------------------------------------------------------
# Connectivity tests
# ---------------------------------------------------------------------------

class TestConnectivityTest:
    """Tests for test_model_connectivity."""

    def test_successful_connectivity(self) -> None:
        """Successful LLM call should return reachable=True."""
        mock_broker_cls = MagicMock()
        mock_broker = mock_broker_cls.return_value
        mock_broker.complete.return_value = MagicMock(content="OK")

        with patch("mycelos.llm.broker.LiteLLMBroker", mock_broker_cls):
            result = check_model_connectivity(SONNET_ANTHROPIC)

        assert result.reachable
        assert result.model_id == "claude-sonnet-4-6"
        assert result.error == ""

    def test_failed_connectivity(self) -> None:
        """Failed LLM call should return reachable=False with error."""
        mock_broker_cls = MagicMock()
        mock_broker = mock_broker_cls.return_value
        mock_broker.complete.side_effect = Exception("AuthenticationError: invalid key")

        with patch("mycelos.llm.broker.LiteLLMBroker", mock_broker_cls):
            result = check_model_connectivity(SONNET_ANTHROPIC)

        assert not result.reachable
        assert "AuthenticationError" in result.error

    def test_empty_response_is_unreachable(self) -> None:
        """Empty response content should be treated as unreachable."""
        mock_broker_cls = MagicMock()
        mock_broker = mock_broker_cls.return_value
        mock_broker.complete.return_value = MagicMock(content="")

        with patch("mycelos.llm.broker.LiteLLMBroker", mock_broker_cls):
            result = check_model_connectivity(SONNET_ANTHROPIC)

        assert not result.reachable
        assert "Empty response" in result.error

    def test_long_error_is_truncated(self) -> None:
        """Very long error messages should be truncated to 200 chars."""
        mock_broker_cls = MagicMock()
        mock_broker = mock_broker_cls.return_value
        mock_broker.complete.side_effect = Exception("x" * 500)

        with patch("mycelos.llm.broker.LiteLLMBroker", mock_broker_cls):
            result = check_model_connectivity(SONNET_ANTHROPIC)

        assert not result.reachable
        assert len(result.error) <= 203  # 200 + "..."

    def test_credential_proxy_is_passed(self) -> None:
        """Credential proxy should be forwarded to the broker."""
        mock_proxy = MagicMock()
        mock_broker_cls = MagicMock()
        mock_broker = mock_broker_cls.return_value
        mock_broker.complete.return_value = MagicMock(content="OK")

        with patch("mycelos.llm.broker.LiteLLMBroker", mock_broker_cls):
            check_model_connectivity(SONNET_ANTHROPIC, credential_proxy=mock_proxy)

        mock_broker_cls.assert_called_once_with(
            default_model="claude-sonnet-4-6",
            credential_proxy=mock_proxy,
        )


# ---------------------------------------------------------------------------
# ValidationReport properties
# ---------------------------------------------------------------------------

class TestValidationReport:
    """Tests for ValidationReport dataclass properties."""

    def test_is_healthy_when_no_warnings(self) -> None:
        report = ValidationReport()
        assert report.is_healthy

    def test_not_healthy_when_warnings(self) -> None:
        report = validate_model_config([])  # Produces warning
        assert not report.is_healthy

    def test_info_issues_dont_affect_health(self) -> None:
        """Info-level issues should not make the report unhealthy."""
        report = validate_model_config([SONNET_ANTHROPIC, HAIKU_ANTHROPIC])
        # Has single_provider info but no warnings
        assert report.is_healthy
