"""Shared mock LLM factory for all Creator/Pipeline tests.

Usage:
    from tests.helpers.mock_llm import mock_llm, approve_audit

    app._llm = mock_llm(GHERKIN, TESTS, CODE)
    app._auditor = approve_audit()
"""

from unittest.mock import MagicMock


def mock_llm(*responses: str, prepend_trivial: bool = True, tokens_per_call: int = 100) -> MagicMock:
    """Create a mock LLM that returns responses in sequence.

    Args:
        responses: LLM response strings in order.
        prepend_trivial: If True, prepends "trivial" for classify_effort's LLM call.
        tokens_per_call: Token count per call (for cost tracking).
    """
    mock = MagicMock()
    mock.total_tokens = 0
    mock.total_cost = 0.0

    all_responses = ("trivial",) + responses if prepend_trivial else responses
    idx = [0]

    def side_effect(*args, **kwargs):
        i = min(idx[0], len(all_responses) - 1)
        idx[0] += 1
        mock.total_tokens += tokens_per_call
        r = MagicMock()
        r.content = all_responses[i]
        r.total_tokens = tokens_per_call
        r.model = "test-model"
        r.tool_calls = None
        return r

    mock.complete.side_effect = side_effect
    return mock


def approve_audit() -> MagicMock:
    """Create a mock auditor that approves everything."""
    auditor = MagicMock()
    auditor.review_code_and_tests.return_value = {"approved": True, "findings": []}
    return auditor
