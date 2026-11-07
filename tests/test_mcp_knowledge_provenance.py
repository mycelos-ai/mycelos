"""Selected MCP results use the existing note store, with their source."""
from datetime import datetime, timezone

import pytest

from mycelos.app import App
from mycelos.tools.knowledge import execute_note_write


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("MYCELOS_MASTER_KEY", "test-mcp-provenance")
    app = App(tmp_path)
    app.initialize()
    yield app
    app.storage.close()


def test_selected_result_retains_connector_source_and_chat_context(app):
    result = execute_note_write({
        "title": "Release date", "content": "The release starts on Monday.",
        "source": {"connector": "docs", "tool": "search", "url": "https://example.test/release",
                   "external_id": "doc-42", "timestamp": "2026-09-06T09:00:00Z"},
    }, {"app": app, "session_id": "chat-1", "agent_id": "mycelos"})
    note = app.knowledge_base.read(result["path"])
    assert note["source"] == {
        "kind": "connector", "connector": "docs", "tool": "search",
        "url": "https://example.test/release", "external_id": "doc-42",
        "timestamp": "2026-09-06T09:00:00Z", "conversation_id": "chat-1",
    }
    assert note["created_by"] == "mycelos"


def test_connector_note_has_save_time_when_query_time_is_unknown(app):
    before = datetime.now(timezone.utc)
    result = execute_note_write({"title": "Result", "content": "A useful fact.",
                                 "source": {"connector": "docs", "tool": "search"}}, {"app": app})
    source = app.knowledge_base.read(result["path"])["source"]
    assert before <= datetime.fromisoformat(source["timestamp"]) <= datetime.now(timezone.utc)


@pytest.mark.parametrize("source", ["docs", {}, {"connector": "docs"},
    {"connector": "docs", "tool": " "}, {"connector": "docs", "tool": "search", "url": []},
    {"connector": "docs", "tool": "search", "timestamp": "yesterday"}])
def test_invalid_connector_source_does_not_create_note(app, source):
    result = execute_note_write({"title": "Invalid", "content": "Do not save.", "source": source}, {"app": app})
    assert "error" in result
    assert app.storage.fetchone("SELECT COUNT(*) AS n FROM knowledge_notes WHERE title='Invalid'")["n"] == 0


def test_plain_note_still_uses_chat_source(app):
    result = execute_note_write({"title": "Idea", "content": "A local thought."},
                                {"app": app, "session_id": "chat-1"})
    assert app.knowledge_base.read(result["path"])["source"] == {"kind": "chat", "conversation_id": "chat-1"}
