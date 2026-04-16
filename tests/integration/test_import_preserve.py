"""Unit tests for preserve-mode import via the Obsidian-vault fixture."""
from __future__ import annotations

from pathlib import Path

import pytest

from mycelos.knowledge.import_pipeline import FileEntry, run_preserve_import


VAULT_ROOT = Path(__file__).parent.parent / "fixtures" / "obsidian-vault"


def _load_vault() -> list[FileEntry]:
    entries: list[FileEntry] = []
    for p in sorted(VAULT_ROOT.rglob("*.md")):
        relpath = str(p.relative_to(VAULT_ROOT))
        entries.append(FileEntry(relpath=relpath, content=p.read_bytes()))
    return entries


@pytest.mark.integration
def test_preserve_creates_three_notes(integration_app) -> None:
    kb = integration_app.knowledge_base
    files = _load_vault()
    assert len(files) == 3  # sanity: fixture has 3 .md files

    result = run_preserve_import(files, kb)
    assert result["mode"] == "preserve"
    assert len(result["created"]) == 3


@pytest.mark.integration
def test_preserve_topics_reflect_folders(integration_app) -> None:
    kb = integration_app.knowledge_base
    result = run_preserve_import(_load_vault(), kb)
    topics = set(result["topics"])
    assert {"topics/journal", "topics/projects", "topics/recipes"} <= topics


@pytest.mark.integration
def test_preserve_strips_frontmatter_and_keeps_body(integration_app) -> None:
    kb = integration_app.knowledge_base
    result = run_preserve_import(_load_vault(), kb)

    mycelos_path = next(p for p in result["created"] if p.endswith("/mycelos"))
    on_disk = kb._knowledge_dir / (mycelos_path + ".md")
    text = on_disk.read_text()
    assert "The AI that grows with you" in text
    assert "tags: project\n---" not in text


@pytest.mark.integration
def test_preserve_marks_notes_as_organized(integration_app) -> None:
    kb = integration_app.knowledge_base
    result = run_preserve_import(_load_vault(), kb)

    for path in result["created"]:
        row = integration_app.storage.fetchone(
            "SELECT organizer_state FROM knowledge_notes WHERE path=?",
            (path,),
        )
        assert row is not None
        assert row["organizer_state"] == "ok"
