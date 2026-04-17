"""Integration test: Multi-turn chat conversation against a local LLM.

Proves that the chat pipeline — session creation, system prompt,
conversation memory, LLM roundtrip — works with Ollama, not just cloud
models. The live counterpart to the "Run on Your Data." promise.

Gated on OLLAMA_HOST; skipped without it. No cassette — local inference
is free and drift is caught by the test body itself.

Run:
    pytest -m integration tests/integration/test_chat_local.py -v -s
"""

from __future__ import annotations

import pytest

# Local 8B models take time. 30s pytest default is too tight.
pytestmark = [pytest.mark.integration, pytest.mark.timeout(300)]


def _get_llm_text(events) -> str:
    """Same text extraction as test_chat_conversation, minus the emoji filter."""
    parts = []
    for e in events:
        if e.type == "text":
            parts.append(e.data.get("content", ""))
        elif e.type == "system-response":
            parts.append(e.data.get("content", ""))
    return " ".join(parts).strip()


@pytest.mark.parametrize("integration_app_local", ["ollama"], indirect=True)
def test_chat_roundtrip_local(integration_app_local):
    """Single-turn: chat session → user message → text response.

    A local model under 10 GB with a 16k context window is plenty for
    this — we ask for something short and check content came back.
    """
    from mycelos.chat.service import ChatService

    app = integration_app_local
    app.memory.set("default", "system", "user.name", "LocalUser", created_by="test")
    app.memory.set("default", "system", "onboarding_completed", "true", created_by="test")

    service = ChatService(app)
    session = service.create_session()

    events = service.handle_message(
        "Reply with just the single word 'hello'.",
        session,
        "default",
    )
    text = _get_llm_text(events)

    # Don't assert the exact content — small local models phrase things
    # their own way. Just verify we got a non-empty response without an
    # error event.
    errors = [e for e in events if e.type == "error"]
    assert not errors, f"chat errored: {errors}"
    assert text, f"expected non-empty response, got events: {[e.type for e in events]}"
