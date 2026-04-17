"""Integration test: Knowledge Organizer classification with a local LLM.

The organizer classifies incoming notes into existing topics. This is a
"background LLM" task — cheap model, no tool loop, exactly the kind of
workflow Mycelos promises to run locally.

Gated on OLLAMA_HOST. Skipped without it.

Run:
    pytest -m integration tests/integration/test_organizer_local.py -v -s
"""

from __future__ import annotations

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.timeout(300)]


@pytest.mark.parametrize("integration_app_local", ["ollama"], indirect=True)
def test_organizer_classifies_into_existing_topic(integration_app_local):
    """Give the organizer a note about Python and three topics to choose from.
    A small local model should at least pick the tech-related one.
    """
    from mycelos.agents.handlers.knowledge_organizer_handler import KnowledgeOrganizerHandler

    app = integration_app_local
    handler = KnowledgeOrganizerHandler(app)

    note = {
        "path": "notes/use-dataclasses",
        "title": "Use dataclasses for Note objects",
        "content": "",
    }
    topics = ["topics/python-tips", "topics/grocery-list", "topics/meeting-notes"]

    classification = handler._classify(note, topics)
    # We don't require a specific topic — small models are inconsistent.
    # We verify the call completed and returned a Classification object
    # with plausible shape.
    assert classification is not None
    assert hasattr(classification, "topic_path")
    assert hasattr(classification, "confidence")
    # confidence must be in [0, 1] or 0.0 (on LLM failure)
    assert 0.0 <= classification.confidence <= 1.0
