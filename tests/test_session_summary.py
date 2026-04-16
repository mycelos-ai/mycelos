"""Tests for Session Summary -- memory extraction from conversations."""

from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from mycelos.app import App
from mycelos.scheduler.session_summary import (
    extract_session_memory,
    process_stale_sessions,
    save_memory_entries,
)


@pytest.fixture
def app() -> App:
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["MYCELOS_MASTER_KEY"] = "test-key-session-summary"
        a = App(Path(tmp))
        a.initialize()
        yield a


MOCK_SUMMARY: dict = {
    "preferences": [{"key": "user.preference.format", "value": "Prefers Markdown"}],
    "decisions": [{"key": "user.decision.model", "value": "Uses Claude Sonnet"}],
    "context": [{"key": "user.context.project", "value": "Building Mycelos"}],
    "facts": [],
}


def _mock_llm_summary() -> MagicMock:
    mock = MagicMock()
    r = MagicMock()
    r.content = json.dumps(MOCK_SUMMARY)
    r.total_tokens = 50
    r.model = "haiku"
    r.tool_calls = None
    mock.complete.return_value = r
    return mock


def _create_stale_session(app: App, messages_count: int = 5) -> str:
    """Create a session with messages, made to look stale."""
    session_id = app.session_store.create_session()
    for i in range(messages_count):
        role = "user" if i % 2 == 0 else "assistant"
        app.session_store.append_message(session_id, role, f"Message {i}")
    # Make it look old by updating the file mtime
    session_path = app.session_store._session_path(session_id)
    old_time = time.time() - 3600  # 1 hour ago
    os.utime(session_path, (old_time, old_time))
    return session_id


# --- save_memory_entries ---


def test_save_memory_entries(app: App) -> None:
    count = save_memory_entries(app, MOCK_SUMMARY)
    assert count == 3  # 1 pref + 1 decision + 1 context

    pref = app.memory.get("default", "system", "user.preference.format")
    assert pref == "Prefers Markdown"

    ctx = app.memory.get("default", "system", "user.context.project")
    assert ctx == "Building Mycelos"


def test_save_empty_summary(app: App) -> None:
    count = save_memory_entries(
        app,
        {"preferences": [], "decisions": [], "context": [], "facts": []},
    )
    assert count == 0


def test_save_partial_summary(app: App) -> None:
    partial = {"preferences": [{"key": "k1", "value": "v1"}]}
    count = save_memory_entries(app, partial)
    assert count == 1


def test_save_skips_empty_keys(app: App) -> None:
    bad = {"preferences": [{"key": "", "value": "v"}, {"key": "k", "value": ""}]}
    count = save_memory_entries(app, bad)
    assert count == 0


# --- extract_session_memory ---


def test_extract_memory_calls_llm(app: App) -> None:
    app._llm = _mock_llm_summary()
    messages = [
        {"role": "user", "content": "I like Markdown"},
        {"role": "assistant", "content": "Noted!"},
    ]
    result = extract_session_memory(app, messages)
    assert result is not None
    assert "preferences" in result


def test_extract_memory_empty_messages(app: App) -> None:
    result = extract_session_memory(app, [])
    assert result is None


def test_extract_memory_system_only_messages(app: App) -> None:
    result = extract_session_memory(
        app, [{"role": "system", "content": "You are helpful"}]
    )
    assert result is None


def test_extract_memory_invalid_json(app: App) -> None:
    mock = MagicMock()
    r = MagicMock()
    r.content = "not json"
    r.total_tokens = 10
    r.tool_calls = None
    mock.complete.return_value = r
    app._llm = mock

    result = extract_session_memory(
        app, [{"role": "user", "content": "test"}]
    )
    assert result is None


def test_extract_memory_non_dict_json(app: App) -> None:
    mock = MagicMock()
    r = MagicMock()
    r.content = json.dumps(["not", "a", "dict"])
    r.total_tokens = 10
    r.tool_calls = None
    mock.complete.return_value = r
    app._llm = mock

    result = extract_session_memory(
        app, [{"role": "user", "content": "test"}]
    )
    assert result is None


# --- process_stale_sessions ---


def test_process_no_sessions(app: App) -> None:
    result = process_stale_sessions(app)
    assert result == []


def test_process_stale_session_summarized(app: App) -> None:
    session_id = _create_stale_session(app, messages_count=5)
    app._llm = _mock_llm_summary()

    result = process_stale_sessions(app, stale_minutes=0)
    assert session_id in result

    # Verify memory entries were saved
    pref = app.memory.get("default", "system", "user.preference.format")
    assert pref == "Prefers Markdown"


def test_process_skips_already_summarized(app: App) -> None:
    session_id = _create_stale_session(app)
    # Mark as already summarized
    app.memory.set(
        "default",
        "system",
        f"session.summary.{session_id[:8]}",
        "done",
        created_by="test",
    )

    app._llm = _mock_llm_summary()
    result = process_stale_sessions(app, stale_minutes=0)
    assert session_id not in result


def test_process_skips_short_sessions(app: App) -> None:
    """Sessions with < 3 messages should be marked empty, not summarized."""
    session_id = app.session_store.create_session()
    app.session_store.append_message(session_id, "user", "Hi")
    # Make stale
    path = app.session_store._session_path(session_id)
    os.utime(path, (time.time() - 3600, time.time() - 3600))

    app._llm = _mock_llm_summary()
    result = process_stale_sessions(app, stale_minutes=0)
    # Should be marked as empty, not summarized
    assert session_id not in result
    summary = app.memory.get(
        "default", "system", f"session.summary.{session_id[:8]}"
    )
    assert summary == "empty"


def test_process_respects_max_sessions(app: App) -> None:
    """Should not process more than max_sessions."""
    sessions = []
    for _ in range(4):
        sid = _create_stale_session(app)
        sessions.append(sid)

    app._llm = _mock_llm_summary()
    result = process_stale_sessions(app, stale_minutes=0, max_sessions=2)
    assert len(result) <= 2


def test_process_skips_recent_sessions(app: App) -> None:
    """Sessions with recent activity should not be processed."""
    session_id = app.session_store.create_session()
    for i in range(5):
        role = "user" if i % 2 == 0 else "assistant"
        app.session_store.append_message(session_id, role, f"Msg {i}")
    # Do NOT make it stale -- mtime is now

    app._llm = _mock_llm_summary()
    result = process_stale_sessions(app, stale_minutes=30)
    assert session_id not in result
