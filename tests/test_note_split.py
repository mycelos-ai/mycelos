"""Tests for note_split tool."""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from mycelos.app import App


@pytest.fixture
def app():
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["MYCELOS_MASTER_KEY"] = "test-key-split"
        a = App(Path(tmp))
        a.initialize()
        yield a


def test_note_split_propose(app):
    """Without confirm, note_split returns proposed sections."""
    kb = app.knowledge_base
    path = kb.write(title="Big Note", content="Topic A content.\n\nTopic B content.", type="note")

    fake = MagicMock()
    fake.content = json.dumps({"sections": [
        {"title": "Topic A", "content": "Topic A content."},
        {"title": "Topic B", "content": "Topic B content."},
    ]})

    from mycelos.tools.knowledge import execute_note_split
    with patch.object(app.llm, "complete", return_value=fake):
        result = execute_note_split({"path": path}, {"app": app, "user_id": "default"})

    assert result["status"] == "proposed"
    assert len(result["sections"]) == 2


def test_note_split_confirm(app):
    """With confirm=True, note_split creates child notes and index."""
    kb = app.knowledge_base
    path = kb.write(title="Meeting Notes", content="Budget: 50k.\n\nHiring: 2 roles.", type="note", tags=["meeting"])

    fake = MagicMock()
    fake.content = json.dumps({"sections": [
        {"title": "Budget Decision", "content": "Budget: 50k."},
        {"title": "Hiring Plan", "content": "Hiring: 2 roles."},
    ]})

    from mycelos.tools.knowledge import execute_note_split
    with patch.object(app.llm, "complete", return_value=fake):
        result = execute_note_split(
            {"path": path, "confirm": True},
            {"app": app, "user_id": "default"},
        )

    assert result["status"] == "split"
    assert len(result["children"]) == 2
    assert result["index"] == path

    # Original should be an index now
    file_path = kb._knowledge_dir / (path + ".md")
    content = file_path.read_text()
    assert "Split into:" in content


def test_note_split_not_found(app):
    """Splitting a non-existent note returns error."""
    from mycelos.tools.knowledge import execute_note_split
    result = execute_note_split({"path": "nonexistent/note"}, {"app": app, "user_id": "default"})
    assert result["status"] == "error"
