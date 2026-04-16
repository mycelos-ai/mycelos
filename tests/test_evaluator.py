"""Tests for EvaluatorAgent."""

import json

import pytest

from mycelos.agents.evaluator import EvaluatorAgent
from mycelos.agents.models import AgentOutput
from mycelos.llm.mock_broker import MockLLMBroker


@pytest.fixture
def evaluator() -> EvaluatorAgent:
    broker = MockLLMBroker().on_message(
        r".*",
        json.dumps({"score": 0.85, "pass": True, "issues": [], "reasoning": "Good quality."}),
    )
    return EvaluatorAgent(llm=broker)


def test_deterministic_check_format_pass(evaluator: EvaluatorAgent) -> None:
    output = AgentOutput(success=True, result="# Summary\nEmail from Chef", artifacts=[], metadata={})
    result = evaluator.evaluate(output=output, criteria={"format": "markdown", "must_contain": ["Summary"]})
    assert result["deterministic_pass"] is True


def test_deterministic_check_format_fail(evaluator: EvaluatorAgent) -> None:
    output = AgentOutput(success=True, result="plain text without markdown", artifacts=[], metadata={})
    result = evaluator.evaluate(output=output, criteria={"format": "markdown", "must_contain": ["# "]})
    assert result["deterministic_pass"] is False
    assert result["score"] == 0.0


def test_deterministic_check_forbidden_content(evaluator: EvaluatorAgent) -> None:
    output = AgentOutput(success=True, result="key: sk-ant-secret12345678901234", artifacts=[], metadata={})
    result = evaluator.evaluate(output=output, criteria={"must_not_contain": ["sk-ant-", "password"]})
    assert result["deterministic_pass"] is False


def test_deterministic_check_max_length(evaluator: EvaluatorAgent) -> None:
    output = AgentOutput(success=True, result="x" * 5000, artifacts=[], metadata={})
    result = evaluator.evaluate(output=output, criteria={"max_length": 2000})
    assert result["deterministic_pass"] is False


def test_llm_evaluation_after_deterministic_pass(evaluator: EvaluatorAgent) -> None:
    output = AgentOutput(success=True, result="# Good Summary\nDetails here.", artifacts=[], metadata={})
    result = evaluator.evaluate(output=output, criteria={"format": "markdown"})
    assert result["deterministic_pass"] is True
    assert result["score"] == 0.85
    assert result["pass"] is True


def test_failed_output_auto_fails(evaluator: EvaluatorAgent) -> None:
    output = AgentOutput(success=False, result=None, artifacts=[], metadata={}, error="timeout")
    result = evaluator.evaluate(output=output, criteria={})
    assert result["score"] == 0.0
    assert result["pass"] is False
