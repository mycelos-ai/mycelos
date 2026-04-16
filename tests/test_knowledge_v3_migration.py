"""v3 migration: organizer_state + organizer_seen_at columns + organizer_suggestions table."""
from __future__ import annotations

import sqlite3
from pathlib import Path

from mycelos.storage.database import SQLiteStorage


def test_v3_migration_adds_columns(tmp_path: Path) -> None:
    storage = SQLiteStorage(tmp_path / "m.db")
    storage.initialize()

    cols = {row["name"] for row in storage.fetchall("PRAGMA table_info(knowledge_notes)")}
    assert "organizer_state" in cols
    assert "organizer_seen_at" in cols


def test_v3_migration_creates_suggestions_table(tmp_path: Path) -> None:
    storage = SQLiteStorage(tmp_path / "m.db")
    storage.initialize()

    rows = storage.fetchall(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='organizer_suggestions'"
    )
    assert len(rows) == 1

    cols = {row["name"] for row in storage.fetchall("PRAGMA table_info(organizer_suggestions)")}
    assert cols >= {"id", "note_path", "kind", "payload", "confidence", "created_at", "status"}


def test_v3_migration_creates_pending_index(tmp_path: Path) -> None:
    storage = SQLiteStorage(tmp_path / "m.db")
    storage.initialize()

    rows = storage.fetchall(
        "SELECT name FROM sqlite_master WHERE type='index' AND name='idx_organizer_pending'"
    )
    assert len(rows) == 1


def test_knowledge_notes_has_remind_at_column(tmp_path: Path) -> None:
    """knowledge_notes must have a nullable remind_at TEXT column for
    full-datetime reminder scheduling (separate from the 'due' date)."""
    storage = SQLiteStorage(tmp_path / "m.db")
    storage.initialize()

    cols = {row["name"] for row in storage.fetchall("PRAGMA table_info(knowledge_notes)")}
    assert "remind_at" in cols


def test_knowledge_notes_remind_at_is_writable(tmp_path: Path) -> None:
    """Round-trip: we can write a remind_at datetime and read it back."""
    storage = SQLiteStorage(tmp_path / "m.db")
    storage.initialize()

    storage.execute(
        "INSERT INTO knowledge_notes (path, title, remind_at) VALUES (?, ?, ?)",
        ("tasks/milk", "Buy milk", "2026-04-12T09:00:00Z"),
    )
    row = storage.fetchone("SELECT remind_at FROM knowledge_notes WHERE path=?", ("tasks/milk",))
    assert row["remind_at"] == "2026-04-12T09:00:00Z"


def test_knowledge_notes_remind_at_is_nullable(tmp_path: Path) -> None:
    """Tasks without a scheduled reminder have remind_at = NULL."""
    storage = SQLiteStorage(tmp_path / "m.db")
    storage.initialize()

    storage.execute(
        "INSERT INTO knowledge_notes (path, title) VALUES (?, ?)",
        ("notes/plain", "Plain"),
    )
    row = storage.fetchone("SELECT remind_at FROM knowledge_notes WHERE path=?", ("notes/plain",))
    assert row["remind_at"] is None


def test_knowledge_notes_has_reminder_fired_at_column(tmp_path: Path) -> None:
    """knowledge_notes.reminder_fired_at tracks "this reminder is done" —
    set either by the scheduler after successful dispatch or by the user
    clicking an inbox entry. Replaces the old 'reset reminder=0' pattern."""
    storage = SQLiteStorage(tmp_path / "m.db")
    storage.initialize()
    cols = {row["name"] for row in storage.fetchall("PRAGMA table_info(knowledge_notes)")}
    assert "reminder_fired_at" in cols


def test_reminder_fired_at_is_nullable_by_default(tmp_path: Path) -> None:
    storage = SQLiteStorage(tmp_path / "m.db")
    storage.initialize()
    storage.execute(
        "INSERT INTO knowledge_notes (path, title) VALUES (?, ?)",
        ("tasks/x", "X"),
    )
    row = storage.fetchone(
        "SELECT reminder_fired_at FROM knowledge_notes WHERE path=?", ("tasks/x",)
    )
    assert row["reminder_fired_at"] is None


def test_reminder_fired_at_migrates_onto_legacy_db(tmp_path: Path) -> None:
    import sqlite3
    db = tmp_path / "legacy_fired.db"
    conn = sqlite3.connect(str(db))
    conn.execute(
        "CREATE TABLE knowledge_notes ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "path TEXT NOT NULL UNIQUE, "
        "title TEXT NOT NULL, "
        "type TEXT NOT NULL DEFAULT 'note', "
        "status TEXT NOT NULL DEFAULT 'active', "
        "tags TEXT DEFAULT '[]', "
        "priority INTEGER NOT NULL DEFAULT 0, "
        "due TEXT, "
        "parent_path TEXT, "
        "reminder INTEGER, "
        "sort_order INTEGER, "
        "created_at TEXT, "
        "updated_at TEXT, "
        "content_hash TEXT)"
    )
    conn.execute("INSERT INTO knowledge_notes (path, title) VALUES ('tasks/old', 'Old')")
    conn.commit()
    conn.close()

    storage = SQLiteStorage(db)
    storage._get_connection()

    cols = {row["name"] for row in storage.fetchall("PRAGMA table_info(knowledge_notes)")}
    assert "reminder_fired_at" in cols
    row = storage.fetchone("SELECT reminder_fired_at FROM knowledge_notes WHERE path='tasks/old'")
    assert row["reminder_fired_at"] is None


def test_remind_at_migrates_onto_legacy_db(tmp_path: Path) -> None:
    """A legacy DB without remind_at gets the column via migration."""
    import sqlite3
    db = tmp_path / "legacy_remind.db"
    conn = sqlite3.connect(str(db))
    conn.execute(
        "CREATE TABLE knowledge_notes ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "path TEXT NOT NULL UNIQUE, "
        "title TEXT NOT NULL, "
        "type TEXT NOT NULL DEFAULT 'note', "
        "status TEXT NOT NULL DEFAULT 'active', "
        "tags TEXT DEFAULT '[]', "
        "priority INTEGER NOT NULL DEFAULT 0, "
        "due TEXT, "
        "parent_path TEXT, "
        "reminder INTEGER, "
        "sort_order INTEGER, "
        "created_at TEXT, "
        "updated_at TEXT, "
        "content_hash TEXT)"
    )
    conn.execute("INSERT INTO knowledge_notes (path, title) VALUES ('tasks/old', 'Old Task')")
    conn.commit()
    conn.close()

    storage = SQLiteStorage(db)
    storage._get_connection()

    cols = {row["name"] for row in storage.fetchall("PRAGMA table_info(knowledge_notes)")}
    assert "remind_at" in cols
    row = storage.fetchone("SELECT remind_at FROM knowledge_notes WHERE path='tasks/old'")
    assert row["remind_at"] is None


def test_v3_migration_defaults_existing_notes_to_pending(tmp_path: Path) -> None:
    """A v2-era DB without organizer_state gets the column with default 'pending'."""
    db = tmp_path / "legacy.db"
    conn = sqlite3.connect(str(db))
    conn.execute(
        "CREATE TABLE knowledge_notes ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "path TEXT NOT NULL UNIQUE, "
        "title TEXT NOT NULL, "
        "type TEXT NOT NULL DEFAULT 'note', "
        "status TEXT NOT NULL DEFAULT 'active', "
        "tags TEXT DEFAULT '[]', "
        "priority INTEGER NOT NULL DEFAULT 0, "
        "due TEXT, "
        "parent_path TEXT, "
        "reminder INTEGER, "
        "sort_order INTEGER, "
        "created_at TEXT, "
        "updated_at TEXT, "
        "content_hash TEXT)"
    )
    conn.execute(
        "INSERT INTO knowledge_notes (path, title) VALUES ('notes/x', 'X')"
    )
    conn.commit()
    conn.close()

    storage = SQLiteStorage(db)
    storage._get_connection()

    row = storage.fetchone("SELECT organizer_state FROM knowledge_notes WHERE path='notes/x'")
    assert row is not None
    assert row["organizer_state"] == "pending"
