"""placement_confidence: the marker that replaces the move suggestion."""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

from mycelos.storage.database import SQLiteStorage


@pytest.fixture
def storage(tmp_path: Path) -> SQLiteStorage:
    s = SQLiteStorage(tmp_path / "uncertain_placement.db")
    s.initialize()
    return s


@pytest.fixture
def app():
    from mycelos.app import App
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["MYCELOS_MASTER_KEY"] = "test-key-uncertain-placement"
        a = App(Path(tmp))
        a.initialize()
        yield a


def test_column_exists_on_fresh_database(storage) -> None:
    cols = [r["name"] for r in storage.fetchall("PRAGMA table_info(knowledge_notes)")]
    assert "placement_confidence" in cols


def _declared_type(storage: SQLiteStorage) -> str:
    """The declared type of knowledge_notes.placement_confidence."""
    row = next(
        r
        for r in storage.fetchall("PRAGMA table_info(knowledge_notes)")
        if r["name"] == "placement_confidence"
    )
    return row["type"]


def test_column_type_is_real_on_fresh_database(storage) -> None:
    """The column must hold numbers, not strings.

    Under TEXT affinity SQLite stores a bound float as '0.6', so
    row["placement_confidence"] == 0.6 is False. Pin the declared type so
    schema.sql and the migration cannot drift apart.
    """
    assert _declared_type(storage) == "REAL"


def test_column_defaults_to_null(app) -> None:
    """A note nobody classified carries no confidence, not a fake zero."""
    path = app.knowledge_base.write(title="Hand written", content="x", topic="notes")
    row = app.storage.fetchone(
        "SELECT placement_confidence FROM knowledge_notes WHERE path=?", (path,))
    assert row["placement_confidence"] is None


def test_migration_adds_column_to_an_existing_database(tmp_path: Path) -> None:
    """A pre-existing DB gets the column without losing data — the
    failure mode that shipped in the source-attachment work.

    Simulate a database written before the column existed by dropping it
    from an initialized file, then opening a *fresh* SQLiteStorage on the
    same file (mirrors a new process opening ~/.mycelos/mycelos.db).
    """
    db_path = tmp_path / "existing.db"
    old = SQLiteStorage(db_path)
    old.initialize()
    old.execute(
        "INSERT INTO knowledge_notes (path, title) VALUES (?, ?)",
        ("notes/pre-existing", "Pre existing"),
    )
    # SQLite 3.35+ supports DROP COLUMN; older builds need a table rebuild.
    old.execute("ALTER TABLE knowledge_notes DROP COLUMN placement_confidence")
    cols = [r["name"] for r in old.fetchall("PRAGMA table_info(knowledge_notes)")]
    assert "placement_confidence" not in cols
    old.close()

    # Fresh instance, as a new process opening the pre-existing file would.
    reopened = SQLiteStorage(db_path)
    cols = [r["name"] for r in reopened.fetchall("PRAGMA table_info(knowledge_notes)")]
    assert "placement_confidence" in cols

    row = reopened.fetchone(
        "SELECT title, placement_confidence FROM knowledge_notes WHERE path=?",
        ("notes/pre-existing",),
    )
    assert row is not None, "the migration must not lose existing rows"
    assert row["title"] == "Pre existing"
    assert row["placement_confidence"] is None

    # A migrated database must declare the same type as a fresh one, or a
    # float written here would read back as a string on one of them.
    assert _declared_type(reopened) == "REAL"

    # Idempotent: opening the same file again must not error.
    again = SQLiteStorage(db_path)
    assert again.fetchone(
        "SELECT COUNT(*) AS c FROM knowledge_notes")["c"] == 1
