"""Live-ish organizer test — real Haiku broker, cassette-replayed in CI."""

from __future__ import annotations

import pytest

from mycelos.knowledge.inbox import InboxService


@pytest.mark.integration
def test_organizer_end_to_end(integration_app) -> None:
    """Seed a small topic + a handful of notes, run the organizer, check it did something."""
    app = integration_app

    kb = app.knowledge_base
    # Seed one topic so a silent_move is possible
    kb.create_topic("Coffee Stuff", tags=["coffee"])

    seeds = [
        ("Coffee with Lisa", "Meet Lisa for coffee next week"),
        ("Espresso grinder tuning", "Notes on grinder adjustment"),
        ("Random idea", "Mycelos should support vision models"),
        ("Shopping list", "Milk, bread, eggs"),
        ("TODO: refactor parser", "Deterministic parser cleanup"),
    ]
    for title, body in seeds:
        kb.write(title=title, content=body)

    result = app.knowledge_organizer.run("default")

    # 5 seeds + the topic note itself (organizer state defaults to pending).
    assert result["processed"] >= 5
    assert result["archived"] == 0
    # Every note should land somewhere: either a silent move or a suggestion.
    handled = result["moved"] + result["suggested"]
    assert handled >= 5, f"expected every note handled, got {result!r}"

    inbox = InboxService(app.storage)
    pending = inbox.list_pending()
    total = sum(len(v) for v in pending.values())
    assert total >= 1, "expected the LLM to produce at least one inbox suggestion"
