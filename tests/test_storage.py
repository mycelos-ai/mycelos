import pytest
from pathlib import Path

from mycelos.storage.database import SQLiteStorage
from mycelos.protocols import StorageBackend


def test_sqlite_implements_protocol():
    """SQLiteStorage satisfies the StorageBackend protocol."""
    assert isinstance(SQLiteStorage.__new__(SQLiteStorage), StorageBackend)


def test_create_database(db_path: Path):
    """Database is created and schema is applied."""
    storage = SQLiteStorage(db_path)
    storage.initialize()

    result = storage.fetchall(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    )
    table_names = [r["name"] for r in result]

    assert "audit_events" in table_names
    assert "config_generations" in table_names
    assert "active_generation" in table_names
    assert "memory_entries" in table_names
    assert "tasks" in table_names
    assert "agents" in table_names


def test_wal_mode_enabled(db_path: Path):
    """SQLite WAL mode is enabled for concurrent access."""
    storage = SQLiteStorage(db_path)
    storage.initialize()

    result = storage.fetchone("PRAGMA journal_mode")
    assert result["journal_mode"] == "wal"


def test_execute_and_fetch(db_path: Path):
    """Basic insert and fetch operations work."""
    storage = SQLiteStorage(db_path)
    storage.initialize()

    storage.execute(
        "INSERT INTO audit_events (event_type, details) VALUES (?, ?)",
        ("test_event", '{"key": "value"}'),
    )

    result = storage.fetchone(
        "SELECT event_type, details FROM audit_events WHERE event_type = ?",
        ("test_event",),
    )

    assert result is not None
    assert result["event_type"] == "test_event"
    assert result["details"] == '{"key": "value"}'
