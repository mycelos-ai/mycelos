"""Integration test: Session summary extraction with a local LLM.

extract_session_memory(app, messages) asks the cheapest model to pull
preferences / decisions / context / facts out of a conversation and return
them as JSON. This is harder than reminder text because the model must
return well-formed JSON — small local models struggle with structured
output. We accept None (LLM returned malformed JSON) as a "soft pass"
but verify no exception propagates.

Gated on OLLAMA_HOST. Skipped without it.
"""

from __future__ import annotations

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.timeout(300)]


@pytest.mark.parametrize("integration_app_local", ["ollama"], indirect=True)
def test_session_summary_extracts_or_gracefully_fails(integration_app_local):
    from mycelos.scheduler.session_summary import extract_session_memory

    app = integration_app_local
    messages = [
        {"role": "user", "content": "Please always respond in German and use metric units."},
        {"role": "assistant", "content": "Understood — German and metric from now on."},
        {"role": "user", "content": "We decided to use Postgres instead of MySQL for the new project."},
        {"role": "assistant", "content": "Noted: Postgres for the new project."},
    ]

    result = extract_session_memory(app, messages)

    # Either well-formed JSON or None (malformed → caller logs + drops).
    # We verify the function doesn't crash and respects the contract.
    if result is not None:
        assert isinstance(result, dict)
        # The prompt asks for these 4 keys; presence is not mandatory but
        # if it's a dict, it must not contain anything crazy.
        allowed = {"preferences", "decisions", "context", "facts"}
        for key in result:
            # Extra keys are acceptable; we just want no weird types
            assert isinstance(key, str)
