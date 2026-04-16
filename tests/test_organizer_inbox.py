from __future__ import annotations

from pathlib import Path

import pytest

from mycelos.knowledge.inbox import InboxService
from mycelos.storage.database import SQLiteStorage


@pytest.fixture
def storage(tmp_path: Path) -> SQLiteStorage:
    s = SQLiteStorage(tmp_path / "inbox.db")
    s.initialize()
    return s


def test_add_move_suggestion(storage: SQLiteStorage) -> None:
    inbox = InboxService(storage)
    sid = inbox.add(
        note_path="notes/x",
        kind="move",
        payload={"target": "projects/mycelos", "alternatives": []},
        confidence=0.65,
    )
    assert sid > 0

    pending = inbox.list_pending()
    assert len(pending["move"]) == 1
    assert pending["move"][0]["note_path"] == "notes/x"
    assert pending["move"][0]["payload"]["target"] == "projects/mycelos"


def test_accept_suggestion_marks_accepted(storage: SQLiteStorage) -> None:
    inbox = InboxService(storage)
    sid = inbox.add("notes/x", "move", {"target": "notes"}, 0.7)
    inbox.accept(sid)

    row = storage.fetchone("SELECT status FROM organizer_suggestions WHERE id=?", (sid,))
    assert row["status"] == "accepted"
    assert inbox.list_pending() == {"move": [], "new_topic": [], "link": [], "refine_type": [], "merge": []}


def test_dismiss_suggestion_marks_dismissed(storage: SQLiteStorage) -> None:
    inbox = InboxService(storage)
    sid = inbox.add("notes/x", "link", {"from": "notes/x", "to": "notes/y"}, 0.9)
    inbox.dismiss(sid)

    row = storage.fetchone("SELECT status FROM organizer_suggestions WHERE id=?", (sid,))
    assert row["status"] == "dismissed"


def test_list_pending_groups_by_kind(storage: SQLiteStorage) -> None:
    inbox = InboxService(storage)
    inbox.add("notes/x", "move", {"target": "notes"}, 0.7)
    inbox.add("notes/x", "new_topic", {"name": "Coffee"}, 0.9)
    inbox.add("notes/x", "link", {"from": "notes/x", "to": "notes/y"}, 0.8)

    grouped = inbox.list_pending()
    assert len(grouped["move"]) == 1
    assert len(grouped["new_topic"]) == 1
    assert len(grouped["link"]) == 1
    assert len(grouped["refine_type"]) == 0


def test_unknown_kind_raises(storage: SQLiteStorage) -> None:
    inbox = InboxService(storage)
    with pytest.raises(ValueError):
        inbox.add("notes/x", "unknown", {}, 0.5)


def test_add_merge_suggestion(storage: SQLiteStorage) -> None:
    inbox = InboxService(storage)
    sid = inbox.add(
        note_path="notes/original",
        kind="merge",
        payload={"duplicate_path": "notes/copy", "similarity": 0.95},
        confidence=0.95,
    )
    assert sid > 0
    pending = inbox.list_pending()
    assert len(pending["merge"]) == 1
    assert pending["merge"][0]["payload"]["duplicate_path"] == "notes/copy"
