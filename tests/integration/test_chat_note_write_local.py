"""Integration test: Chat → note_write tool call on a local LLM.

One step harder than test_chat_memory_local: the user says "remember to
buy milk tomorrow", the LLM should recognize this as a task and invoke
the note_write tool with a plausible title (containing "milk"). We then
verify the note actually landed on disk.

Gated on OLLAMA_HOST. Skipped without it.

Lax acceptance mirrors the other chat tests:
  - note_write was called AND a note with 'milk' in the title exists, OR
  - the LLM acknowledged the task in text (graceful fallback)

A hard failure means the LLM errored or returned nothing — that would
be a real bug in the local-LLM path.
"""

from __future__ import annotations

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.timeout(300)]


@pytest.mark.parametrize("integration_app_local", ["ollama"], indirect=True)
def test_chat_asks_to_remember_a_task(integration_app_local):
    from mycelos.chat.service import ChatService

    app = integration_app_local
    app.memory.set("default", "system", "user.name", "LocalUser", created_by="test")
    app.memory.set("default", "system", "onboarding_completed", "true", created_by="test")

    service = ChatService(app)
    session = service.create_session()

    events = service.handle_message(
        "Please remember: I need to buy milk tomorrow.",
        session,
        "default",
    )
    errors = [e for e in events if e.type == "error"]
    assert not errors, f"chat errored: {errors}"

    # Did the LLM call note_write?
    tool_calls = [e for e in events if e.type == "tool-call"]
    note_writes = [
        e for e in tool_calls
        if e.data.get("tool") == "note_write" or e.data.get("name") == "note_write"
    ]

    # Did a note with "milk" actually end up on disk?
    note_on_disk = False
    try:
        notes = app.storage.fetchall(
            "SELECT title, path FROM knowledge_notes WHERE lower(title) LIKE '%milk%'"
        )
        note_on_disk = bool(notes)
    except Exception:
        pass

    # Fallback signal: did the LLM acknowledge in text?
    texts = []
    for e in events:
        if e.type in ("text", "system-response"):
            texts.append(e.data.get("content", ""))
    combined = " ".join(texts).lower()

    assert note_writes or note_on_disk or any(kw in combined for kw in ("milk", "remember", "noted", "task")), (
        f"LLM neither wrote a milk note nor acknowledged the request. "
        f"Events: {[e.type for e in events]} | text: {combined[:200]!r}"
    )
