import pytest
from pathlib import Path

from mycelos.storage.database import SQLiteStorage
from mycelos.memory.service import SQLiteMemoryService
from mycelos.protocols import MemoryService


def test_implements_protocol():
    assert isinstance(SQLiteMemoryService.__new__(SQLiteMemoryService), MemoryService)


@pytest.fixture
def memory(db_path: Path) -> SQLiteMemoryService:
    storage = SQLiteStorage(db_path)
    storage.initialize()
    # Create test users to satisfy FK constraints
    for uid in ("user1", "user2"):
        storage.execute(
            "INSERT OR IGNORE INTO users (id, name, status) VALUES (?, ?, ?)",
            (uid, uid, "active"),
        )
    return SQLiteMemoryService(storage)


def test_set_and_get_system_memory(memory: SQLiteMemoryService):
    memory.set("user1", "system", "user.name", "Stefan", created_by="system")
    result = memory.get("user1", "system", "user.name")
    assert result == "Stefan"


def test_agent_memory_isolation(memory: SQLiteMemoryService):
    memory.set("user1", "agent", "pref", "dark", agent_id="agent-a", created_by="agent-a")
    memory.set("user1", "agent", "pref", "light", agent_id="agent-b", created_by="agent-b")

    assert memory.get("user1", "agent", "pref", agent_id="agent-a") == "dark"
    assert memory.get("user1", "agent", "pref", agent_id="agent-b") == "light"


def test_get_nonexistent_returns_none(memory: SQLiteMemoryService):
    result = memory.get("user1", "system", "nonexistent")
    assert result is None


def test_search_memory(memory: SQLiteMemoryService):
    memory.set("user1", "shared", "project.alpha.deadline", "2026-04-15", created_by="planner")
    memory.set("user1", "shared", "project.beta.status", "active", created_by="planner")

    results = memory.search("user1", "shared", "project.alpha")
    assert len(results) == 1
    assert results[0]["key"] == "project.alpha.deadline"


def test_delete_memory(memory: SQLiteMemoryService):
    memory.set("user1", "agent", "temp", "data", agent_id="a1", created_by="a1")
    assert memory.delete("user1", "agent", "temp", agent_id="a1")
    assert memory.get("user1", "agent", "temp", agent_id="a1") is None


def test_user_scoping(memory: SQLiteMemoryService):
    memory.set("user1", "system", "name", "Stefan", created_by="system")
    memory.set("user2", "system", "name", "Max", created_by="system")

    assert memory.get("user1", "system", "name") == "Stefan"
    assert memory.get("user2", "system", "name") == "Max"


def test_update_existing_key(memory: SQLiteMemoryService):
    memory.set("user1", "system", "lang", "de", created_by="system")
    memory.set("user1", "system", "lang", "en", created_by="system")

    assert memory.get("user1", "system", "lang") == "en"
