from __future__ import annotations

import pytest

from mycelos.knowledge.topic_map import build_topic_mermaid


@pytest.mark.integration
def test_empty_topic_returns_empty_string(integration_app) -> None:
    kb = integration_app.knowledge_base
    topic_path = kb.create_topic("Coffee")
    out = build_topic_mermaid(topic_path, kb)
    assert out == ""


@pytest.mark.integration
def test_topic_with_one_note_renders_graph(integration_app) -> None:
    kb = integration_app.knowledge_base
    topic_path = kb.create_topic("Coffee")
    kb.write(title="Espresso", content="body", topic=topic_path)

    out = build_topic_mermaid(topic_path, kb)
    assert out.startswith("```mermaid")
    assert "graph TD" in out
    assert "Espresso" in out
    assert out.rstrip().endswith("```")


@pytest.mark.integration
def test_topic_with_wikilink_edge(integration_app) -> None:
    kb = integration_app.knowledge_base
    topic_path = kb.create_topic("Coffee")
    grinder_path = kb.write(title="Grinder", content="tuning notes", topic=topic_path)
    kb.write(
        title="Espresso",
        content=f"Pairs with [[{grinder_path}]]",
        topic=topic_path,
    )

    out = build_topic_mermaid(topic_path, kb)
    assert out.count("-->") >= 3


@pytest.mark.integration
def test_large_topic_wraps_in_details(integration_app) -> None:
    kb = integration_app.knowledge_base
    topic_path = kb.create_topic("Big")
    for i in range(16):
        kb.write(title=f"Note {i}", content="x", topic=topic_path)

    out = build_topic_mermaid(topic_path, kb)
    assert "<details>" in out
    assert "```mermaid" in out
