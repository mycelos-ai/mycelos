"""EvaluatorAgent -- evaluates agent output quality.

Two-phase evaluation:
1. Deterministic checks (format, length, forbidden content) -- free, fast
2. LLM evaluation (quality assessment) -- only if deterministic checks pass
"""

from __future__ import annotations

import json
import re
from typing import Any

from mycelos.agents.models import AgentOutput


class EvaluatorAgent:
    """Evaluates agent output against criteria.

    Uses a two-phase approach: deterministic checks first (free, fast),
    then LLM-based quality scoring only when deterministic checks pass.
    This follows the cost-optimization principle: deterministic > Haiku > Opus.
    """

    def __init__(self, llm: Any) -> None:
        self._llm = llm

    def evaluate(
        self, output: AgentOutput, criteria: dict[str, Any]
    ) -> dict[str, Any]:
        """Evaluate agent output against the given criteria.

        Args:
            output: The agent output to evaluate.
            criteria: Evaluation criteria. Supported keys:
                - format: Expected format (e.g. "markdown").
                - must_contain: List of strings that must appear in output.
                - must_not_contain: List of forbidden strings.
                - max_length: Maximum character length.

        Returns:
            Dict with keys: score, pass, deterministic_pass, issues, reasoning.
        """
        if not output.success:
            return {
                "score": 0.0,
                "pass": False,
                "deterministic_pass": False,
                "issues": [f"Agent failed: {output.error}"],
                "reasoning": "Agent execution failed.",
            }

        det_result = self._deterministic_checks(output, criteria)
        if not det_result["pass"]:
            return {
                "score": 0.0,
                "pass": False,
                "deterministic_pass": False,
                "issues": det_result["issues"],
                "reasoning": "Deterministic checks failed.",
            }

        llm_result = self._llm_evaluate(output, criteria)
        return {
            "score": llm_result.get("score", 0.5),
            "pass": llm_result.get("pass", llm_result.get("score", 0) >= 0.7),
            "deterministic_pass": True,
            "issues": llm_result.get("issues", []),
            "reasoning": llm_result.get("reasoning", ""),
        }

    def _deterministic_checks(
        self, output: AgentOutput, criteria: dict[str, Any]
    ) -> dict[str, Any]:
        """Run fast, free deterministic checks against the output.

        Args:
            output: The agent output to check.
            criteria: The criteria dict (see evaluate()).

        Returns:
            Dict with 'pass' (bool) and 'issues' (list of strings).
        """
        issues: list[str] = []
        result_str = str(output.result) if output.result is not None else ""

        if "format" in criteria:
            if criteria["format"] == "markdown" and not re.search(
                r"^#+ ", result_str, re.MULTILINE
            ):
                issues.append("Output is not valid Markdown (no headers found)")

        for pattern in criteria.get("must_contain", []):
            if pattern not in result_str:
                issues.append(f"Output missing required content: '{pattern}'")

        for pattern in criteria.get("must_not_contain", []):
            if pattern in result_str:
                issues.append(f"Output contains forbidden content: '{pattern}'")

        if "max_length" in criteria and len(result_str) > criteria["max_length"]:
            issues.append(
                f"Output too long: {len(result_str)} > {criteria['max_length']}"
            )

        return {"pass": len(issues) == 0, "issues": issues}

    def _llm_evaluate(
        self, output: AgentOutput, criteria: dict[str, Any]
    ) -> dict[str, Any]:
        """Use the LLM to score output quality.

        Only called when deterministic checks pass. This is the expensive
        phase, so we minimize its usage.

        Args:
            output: The agent output to evaluate.
            criteria: The criteria dict for context.

        Returns:
            Dict with score, pass, issues, reasoning from LLM.
        """
        prompt = (
            f"Evaluate this agent output:\n\n{output.result}\n\n"
            f"Criteria: {json.dumps(criteria)}\n\n"
            "Respond with JSON: {score, pass, issues, reasoning}"
        )
        response = self._llm.complete([
            {
                "role": "system",
                "content": "You are an output quality evaluator. Respond only with valid JSON.",
            },
            {"role": "user", "content": prompt},
        ])
        try:
            return json.loads(response.content)
        except (json.JSONDecodeError, ValueError):
            return {
                "score": 0.0,
                "pass": False,
                "issues": ["LLM evaluation returned invalid JSON"],
                "reasoning": "LLM response could not be parsed. Failing safe.",
            }
