import pytest
from pathlib import Path

from mycelos.storage.database import SQLiteStorage
from mycelos.audit import SQLiteAuditLogger
from mycelos.protocols import AuditLogger


def test_implements_protocol():
    assert isinstance(SQLiteAuditLogger.__new__(SQLiteAuditLogger), AuditLogger)


@pytest.fixture
def logger(db_path: Path) -> SQLiteAuditLogger:
    storage = SQLiteStorage(db_path)
    storage.initialize()
    return SQLiteAuditLogger(storage)


def test_log_event(logger: SQLiteAuditLogger):
    logger.log("task.created", task_id="t1", details={"goal": "test"})

    events = logger.query(event_type="task.created")
    assert len(events) == 1
    assert events[0]["task_id"] == "t1"


def test_query_by_agent(logger: SQLiteAuditLogger):
    logger.log("agent.action", agent_id="a1")
    logger.log("agent.action", agent_id="a2")

    events = logger.query(agent_id="a1")
    assert len(events) == 1
    assert events[0]["agent_id"] == "a1"


def test_query_limit(logger: SQLiteAuditLogger):
    for i in range(5):
        logger.log("test.event", details={"i": i})

    events = logger.query(limit=3)
    assert len(events) == 3
