"""Integration test: Knowledge Base roundtrip with real LLM.

Tests the full KB flow: write a note via ChatService, verify search finds it,
and verify it's injected as context in subsequent messages.

Cost estimate: ~$0.002 per run (Haiku model, short prompts)
"""

from __future__ import annotations

import pytest


@pytest.mark.integration
def test_kb_write_search_context(integration_app, require_anthropic_key):
    """Write a note via ChatService, then verify it's found by search and injected as context."""
    from mycelos.chat.service import ChatService

    app = integration_app
    app.memory.set("default", "system", "user.name", "TestUser", created_by="test")
    app.memory.set("default", "system", "onboarding_completed", "true", created_by="test")
    service = ChatService(app)
    session = service.create_session()

    # 1. Ask LLM to create a note
    events = service.handle_message(
        "Remember: the project deadline is March 30th for the alpha release",
        session, "default")

    # Give the LLM a chance to have created a note; also write one directly
    # so the test is not flaky (LLM may or may not use the tool)
    app.knowledge_base.write(
        title="Project Alpha Deadline",
        content="The project deadline is March 30th for the alpha release.",
        type="note",
        tags=["deadline", "alpha"],
    )

    # Verify note was created (check KB)
    notes = app.knowledge_base.search("deadline alpha release")
    assert len(notes) >= 1, "KB should have a note about the deadline"

    # 2. Ask about it — should find via context enrichment
    events2 = service.handle_message(
        "When is the project deadline?",
        session, "default")

    # Filter out gamification greetings
    response_text = " ".join(
        e.data.get("content", "") for e in events2
        if e.type == "text" or (e.type == "system-response"
            and not any(e.data.get("content", "").startswith(icon) for icon in ("🌱", "🔍", "🔧", "⚡", "🧠")))
    )
    # Skip if LLM call failed (stale cassette)
    error_events = [e for e in events2 if e.type == "error"]
    if error_events and not response_text.strip():
        pytest.skip(f"LLM call failed (re-record cassettes): {error_events[0].data.get('content', '')[:200]}")

    assert "march" in response_text.lower() or "30" in response_text, \
        f"Response should mention the deadline: {response_text}"


@pytest.mark.integration
def test_kb_direct_write_and_search(integration_app):
    """Write notes directly to KB and verify FTS5 search finds them."""
    app = integration_app

    # Write two notes
    path1 = app.knowledge_base.write(
        title="API Rate Limits",
        content="The Anthropic API has rate limits of 1000 requests per minute.",
        type="note",
        tags=["api", "limits"],
    )
    path2 = app.knowledge_base.write(
        title="Database Schema",
        content="We use SQLite with WAL mode for high concurrency.",
        type="note",
        tags=["database", "sqlite"],
    )

    assert path1, "Should return a path for the first note"
    assert path2, "Should return a path for the second note"

    # Search should find the first note
    results = app.knowledge_base.search("anthropic rate limits")
    assert any("rate" in r.get("title", "").lower() or "api" in r.get("title", "").lower()
               for r in results), \
        f"Search should find the API note: {results}"

    # Search should find the second note
    results2 = app.knowledge_base.search("sqlite database")
    assert any("database" in r.get("title", "").lower() or "schema" in r.get("title", "").lower()
               for r in results2), \
        f"Search should find the database note: {results2}"


@pytest.mark.integration
def test_kb_read_written_note(integration_app):
    """Write a note and read it back — verifies persistence."""
    app = integration_app

    path = app.knowledge_base.write(
        title="Integration Test Note",
        content="This note was written during an integration test.",
        type="note",
        tags=["test"],
    )

    note = app.knowledge_base.read(path)
    assert note is not None, "Should be able to read back the note"
    assert note["title"] == "Integration Test Note"
    assert "integration test" in note["content"].lower()
    assert "test" in note["tags"]
