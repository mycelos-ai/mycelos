"""Model configuration plausibility checks and connectivity tests.

Validates that the user's model setup covers both capable (sonnet/opus)
and cheap (haiku) tiers, checks provider diversity for resilience,
and optionally tests connectivity to each configured model.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from mycelos.llm.providers import ModelInfo


@dataclass
class ValidationIssue:
    """A single issue found during validation."""

    level: str  # "warning" or "info"
    code: str  # machine-readable code
    message: str  # human-readable message
    suggestion: str = ""  # actionable fix


@dataclass
class ConnectivityResult:
    """Result of testing a single model's connectivity."""

    model_id: str
    reachable: bool
    error: str = ""


@dataclass
class ValidationReport:
    """Full validation report for model configuration."""

    issues: list[ValidationIssue] = field(default_factory=list)
    connectivity: list[ConnectivityResult] = field(default_factory=list)
    has_capable_tier: bool = False
    has_cheap_tier: bool = False
    provider_count: int = 0
    model_count: int = 0

    @property
    def has_warnings(self) -> bool:
        return any(i.level == "warning" for i in self.issues)

    @property
    def is_healthy(self) -> bool:
        return not self.has_warnings


# Tiers considered "capable" (for complex tasks).
_CAPABLE_TIERS: frozenset[str] = frozenset({"sonnet", "opus"})

# Tiers considered "cheap" (for classification/routing).
_CHEAP_TIERS: frozenset[str] = frozenset({"haiku"})

# Suggestions for cheap models per provider.
_CHEAP_SUGGESTIONS: dict[str, str] = {
    "anthropic": "anthropic/claude-haiku-4-5",
    "openai": "openai/gpt-4o-mini",
    "gemini": "gemini/gemini-2.5-flash",
}

# Suggestions for capable models per provider.
_CAPABLE_SUGGESTIONS: dict[str, str] = {
    "anthropic": "anthropic/claude-sonnet-4-6",
    "openai": "openai/gpt-4o",
    "gemini": "gemini/gemini-2.5-pro",
}


def validate_model_config(models: list[ModelInfo]) -> ValidationReport:
    """Run plausibility checks on the user's model configuration.

    Checks:
    1. Has at least one capable model (sonnet/opus) for complex tasks
    2. Has at least one cheap model (haiku) for classification/routing
    3. Has models from multiple providers for resilience
    """
    report = ValidationReport()
    report.model_count = len(models)

    if not models:
        report.issues.append(ValidationIssue(
            level="warning",
            code="no_models",
            message="No models configured.",
            suggestion="Add at least one LLM model to use Mycelos.",
        ))
        return report

    tiers = {m.tier for m in models}
    providers = {m.provider for m in models}
    report.provider_count = len(providers)
    report.has_capable_tier = bool(tiers & _CAPABLE_TIERS)
    report.has_cheap_tier = bool(tiers & _CHEAP_TIERS)

    # Check 1: Missing cheap tier
    if not report.has_cheap_tier:
        suggestions = [
            _CHEAP_SUGGESTIONS[p] for p in providers if p in _CHEAP_SUGGESTIONS
        ]
        suggestion_text = (
            f"Add a cheap model like {' or '.join(repr(s) for s in suggestions)}."
            if suggestions
            else "Add a haiku-tier model for cost-effective classification."
        )
        report.issues.append(ValidationIssue(
            level="warning",
            code="missing_cheap_tier",
            message=(
                "No haiku-tier (cheap) model configured. "
                "Classification and routing tasks will use expensive models."
            ),
            suggestion=suggestion_text,
        ))

    # Check 2: Missing capable tier
    if not report.has_capable_tier:
        suggestions = [
            _CAPABLE_SUGGESTIONS[p] for p in providers if p in _CAPABLE_SUGGESTIONS
        ]
        suggestion_text = (
            f"Add a capable model like {' or '.join(repr(s) for s in suggestions)}."
            if suggestions
            else "Add a sonnet or opus-tier model for complex tasks."
        )
        report.issues.append(ValidationIssue(
            level="warning",
            code="missing_capable_tier",
            message=(
                "No sonnet/opus-tier (capable) model configured. "
                "Complex tasks like code generation may produce lower quality results."
            ),
            suggestion=suggestion_text,
        ))

    # Check 3: Single provider
    if len(providers) == 1:
        provider = next(iter(providers))
        report.issues.append(ValidationIssue(
            level="info",
            code="single_provider",
            message=f"All models are from one provider ({provider}).",
            suggestion="Consider adding a second provider for cross-provider failover resilience.",
        ))

    return report


def check_model_connectivity(
    model: ModelInfo,
    credential_proxy: Any | None = None,
) -> ConnectivityResult:
    """Test connectivity to a single LLM model with a minimal prompt.

    Sends a tiny test message to verify the API key and model are working.
    Uses the broker's scoped credential injection for security.
    """
    try:
        from mycelos.llm.broker import LiteLLMBroker

        broker = LiteLLMBroker(
            default_model=model.id,
            credential_proxy=credential_proxy,
        )
        response = broker.complete(
            messages=[{"role": "user", "content": "Reply with exactly: OK"}],
            model=model.id,
        )
        if response.content:
            return ConnectivityResult(model_id=model.id, reachable=True)
        return ConnectivityResult(
            model_id=model.id, reachable=False, error="Empty response",
        )
    except Exception as e:
        error_msg = str(e)
        # Truncate very long error messages
        if len(error_msg) > 200:
            error_msg = error_msg[:200] + "..."
        return ConnectivityResult(
            model_id=model.id, reachable=False, error=error_msg,
        )
