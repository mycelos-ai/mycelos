"""Unit tests for new KnowledgeBase methods: rename, merge, delete, archive."""
from __future__ import annotations

import pytest


@pytest.mark.integration
def test_rename_topic(integration_app) -> None:
    kb = integration_app.knowledge_base
    old_path = kb.create_topic("Kaffee")
    note_path = kb.write(title="Espresso", content="18g in", topic=old_path)

    new_path = kb.rename_topic(old_path, "Getraenke")

    assert new_path != old_path
    assert "getraenke" in new_path
    topic_row = integration_app.storage.fetchone(
        "SELECT path, title FROM knowledge_notes WHERE path=?", (new_path,)
    )
    assert topic_row is not None
    assert topic_row["title"] == "Getraenke"
    old_row = integration_app.storage.fetchone(
        "SELECT path FROM knowledge_notes WHERE path=?", (old_path,)
    )
    assert old_row is None
    child = integration_app.storage.fetchone(
        "SELECT parent_path FROM knowledge_notes WHERE path=?", (note_path,)
    )
    assert child["parent_path"] == new_path


@pytest.mark.integration
def test_merge_topics(integration_app) -> None:
    kb = integration_app.knowledge_base
    source = kb.create_topic("Kaffee")
    target = kb.create_topic("Getraenke")
    note1 = kb.write(title="Espresso", content="body", topic=source)
    note2 = kb.write(title="Latte", content="body", topic=source)

    result = kb.merge_topics(source, target)

    assert result["moved"] == 2
    child1 = integration_app.storage.fetchone(
        "SELECT parent_path FROM knowledge_notes WHERE path=?", (note1,)
    )
    assert child1["parent_path"] == target
    source_note = integration_app.storage.fetchone(
        "SELECT title FROM knowledge_notes WHERE path=?", (source,)
    )
    assert source_note is not None
    redirect_file = kb._knowledge_dir / (source + ".md")
    assert redirect_file.exists()
    assert "[[" in redirect_file.read_text()


@pytest.mark.integration
def test_delete_empty_topic(integration_app) -> None:
    kb = integration_app.knowledge_base
    path = kb.create_topic("Empty")
    assert kb.delete_topic(path) is True
    row = integration_app.storage.fetchone(
        "SELECT path FROM knowledge_notes WHERE path=?", (path,)
    )
    assert row is None


@pytest.mark.integration
def test_delete_nonempty_topic_fails(integration_app) -> None:
    kb = integration_app.knowledge_base
    path = kb.create_topic("Full")
    kb.write(title="Child", content="body", topic=path)
    with pytest.raises(ValueError, match="children"):
        kb.delete_topic(path)


@pytest.mark.integration
def test_archive_note(integration_app) -> None:
    kb = integration_app.knowledge_base
    path = kb.write(title="Old idea", content="body")
    kb.archive_note(path)
    row = integration_app.storage.fetchone(
        "SELECT status, organizer_state FROM knowledge_notes WHERE path=?", (path,)
    )
    assert row["status"] == "archived"
    assert row["organizer_state"] == "archived"
