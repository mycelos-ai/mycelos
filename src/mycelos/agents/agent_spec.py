"""AgentSpec — structured specification for a new agent.

Created during the interview phase. Drives all subsequent phases:
Gherkin generation, test generation, code generation, and registration.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


# Effort levels with descriptions
EFFORT_LEVELS = {
    "trivial": "Simple wrapper around one tool (e.g., 'search news')",
    "small": "One agent, 1-2 tools, clear logic",
    "medium": "One agent, multiple tools, some logic",
    "large": "Complex logic or multiple agents needed — should be split",
    "unrealistic": "Outside Mycelos agent capabilities — should be declined",
}

_CLASSIFY_EFFORT_PROMPT = """\
Classify the effort to build this Mycelos agent into exactly one category.

Agent: {name}
Description: {description}
Use Case: {use_case}
Capabilities: {capabilities}
Trigger: {trigger}
Input: {input_format}
Output: {output_format}

Categories:
- trivial: Simple wrapper around one tool or deterministic logic. No complex branching.
- small: 1-2 tools with clear, straightforward logic. No error-prone integrations.
- medium: Multiple tools, some branching logic, or needs structured LLM output.
- large: Complex orchestration, multiple external services, error recovery, or state management.
- unrealistic: Requires capabilities outside Mycelos (GUI apps, real-time systems, full applications).

Respond with ONLY the category name, nothing else."""


@dataclass
class AgentSpec:
    """Structured specification for a new agent."""

    name: str
    description: str
    use_case: str = ""
    capabilities_needed: list[str] = field(default_factory=list)
    input_format: str = ""  # What the agent receives
    output_format: str = ""  # What the agent produces
    trigger: str = "on_demand"  # on_demand, scheduled, event
    model_tier: str = "sonnet"  # haiku, sonnet, opus
    gherkin_scenarios: str = ""  # Confirmed Gherkin text
    user_language: str = "de"  # Language for Gherkin scenarios
    dependencies: list[str] = field(default_factory=list)  # Python packages required
    effort: str = ""  # Set by classify_effort()
    estimated_cost: float = 0.0  # Set by estimate_cost()

    def to_prompt_context(self) -> str:
        """Format spec as context string for LLM prompts."""
        caps = ", ".join(self.capabilities_needed) if self.capabilities_needed else "none"
        parts = [
            f"Agent: {self.name}",
            f"Description: {self.description}",
            f"Use Case: {self.use_case}",
            f"Capabilities: {caps}",
            f"Trigger: {self.trigger}",
            f"Model Tier: {self.model_tier}",
            f"Language: {self.user_language}",
        ]
        if self.input_format:
            parts.append(f"Input Format: {self.input_format}")
        if self.output_format:
            parts.append(f"Output Format: {self.output_format}")
        if self.dependencies:
            parts.append(f"Dependencies: {', '.join(self.dependencies)}")
        return "\n".join(parts)


def classify_effort(spec: AgentSpec, llm: Any = None) -> str:
    """Classify the effort level for building an agent using LLM judgment.

    Args:
        spec: The agent specification.
        llm: LLM broker. If None, falls back to a simple heuristic.

    Returns one of: trivial, small, medium, large, unrealistic.
    """
    if llm is not None:
        try:
            prompt = _CLASSIFY_EFFORT_PROMPT.format(
                name=spec.name,
                description=spec.description,
                use_case=spec.use_case,
                capabilities=", ".join(spec.capabilities_needed) or "none",
                trigger=spec.trigger,
                input_format=spec.input_format or "not specified",
                output_format=spec.output_format or "not specified",
            )
            response = llm.complete(
                [{"role": "user", "content": prompt}],
                model=None,  # cheapest available
            )
            result = response.content.strip().lower()
            if result in ("trivial", "small", "medium", "large", "unrealistic"):
                return result
        except Exception:
            pass  # Fall through to heuristic

    # Fallback heuristic (no LLM available, e.g. in tests)
    num_caps = len(spec.capabilities_needed)
    if num_caps <= 1:
        return "trivial"
    if num_caps <= 2:
        return "small"
    if num_caps <= 5:
        return "medium"
    return "large"


def estimate_cost(spec: AgentSpec) -> float:
    """Estimate the token cost for generating this agent.

    Based on effort level and model tier. Returns estimated $ cost.
    """
    # Base costs per effort level (approximate LLM token costs)
    effort_base = {
        "trivial": 0.01,
        "small": 0.03,
        "medium": 0.08,
        "large": 0.20,
        "unrealistic": 0.0,
    }

    # Model tier multiplier
    tier_multiplier = {
        "haiku": 0.3,
        "sonnet": 1.0,
        "opus": 3.0,
    }

    effort = spec.effort or classify_effort(spec)
    base = effort_base.get(effort, 0.08)
    multiplier = tier_multiplier.get(spec.model_tier, 1.0)

    return round(base * multiplier, 4)
