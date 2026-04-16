"""Live-ish integration test for suggest-mode Smart Import.

Runs the real organizer against 10 flat fixture notes and verifies the
inbox fills with suggestions. Uses the cassette infrastructure, so CI
replays without an API key.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from mycelos.knowledge.import_pipeline import FileEntry, run_suggest_import
from mycelos.knowledge.inbox import InboxService


FIXTURE_DIR = Path(__file__).parent.parent / "fixtures" / "flat-notes"


@pytest.mark.integration
def test_suggest_import_creates_inbox_suggestions(integration_app) -> None:
    kb = integration_app.knowledge_base

    entries: list[FileEntry] = []
    for p in sorted(FIXTURE_DIR.glob("*.md")):
        entries.append(FileEntry(relpath=p.name, content=p.read_bytes()))
    assert len(entries) == 10, f"expected 10 fixture notes, got {len(entries)}"

    result = run_suggest_import(entries, kb)
    assert result["mode"] == "suggest"
    assert len(result["created"]) == 10

    run_result = integration_app.knowledge_organizer.run("default")
    # The organizer should have processed at least the 10 imports (topic
    # notes can also bleed in — tolerate extras).
    assert run_result["processed"] >= 10

    # Every note either got a silent move or produced an inbox suggestion.
    handled = run_result["moved"] + run_result["suggested"]
    assert handled >= 10, f"expected all notes handled, got {run_result!r}"

    inbox = InboxService(integration_app.storage)
    pending = inbox.list_pending()
    total = sum(len(v) for v in pending.values())
    assert total >= 1, "expected at least one inbox suggestion"
