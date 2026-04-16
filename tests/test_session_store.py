"""Tests for SessionStore — JSONL session persistence."""
import json
import time
from pathlib import Path

import pytest

from mycelos.sessions.store import SessionStore


@pytest.fixture
def store(tmp_path: Path) -> SessionStore:
    return SessionStore(tmp_path / "conversations")


def test_create_session(store: SessionStore) -> None:
    sid = store.create_session(user_id="stefan")
    assert sid is not None
    assert store.session_exists(sid)


def test_append_and_load_messages(store: SessionStore) -> None:
    sid = store.create_session()
    store.append_message(sid, role="user", content="Hello!")
    store.append_message(sid, role="assistant", content="Hi there!")
    messages = store.load_messages(sid)
    assert len(messages) == 2
    assert messages[0]["role"] == "user"
    assert messages[1]["content"] == "Hi there!"


def test_message_order_preserved(store: SessionStore) -> None:
    sid = store.create_session()
    for i in range(10):
        store.append_message(sid, role="user", content=f"Message {i}")
    messages = store.load_messages(sid)
    assert len(messages) == 10
    assert messages[0]["content"] == "Message 0"
    assert messages[9]["content"] == "Message 9"


def test_list_sessions(store: SessionStore) -> None:
    store.create_session(user_id="stefan")
    store.create_session(user_id="stefan")
    sessions = store.list_sessions()
    assert len(sessions) == 2


def test_get_latest_session(store: SessionStore) -> None:
    s1 = store.create_session()
    store.append_message(s1, role="user", content="first")
    time.sleep(0.05)  # ensure different mtime
    s2 = store.create_session()
    store.append_message(s2, role="user", content="second")
    latest = store.get_latest_session()
    assert latest == s2


def test_nonexistent_session(store: SessionStore) -> None:
    assert not store.session_exists("fake-id")
    assert store.load_messages("fake-id") == []


def test_persistence_across_instances(store: SessionStore) -> None:
    sid = store.create_session()
    store.append_message(sid, role="user", content="persisted")
    store2 = SessionStore(store._conversations_dir)
    messages = store2.load_messages(sid)
    assert len(messages) == 1
    assert messages[0]["content"] == "persisted"


def test_append_with_metadata(store: SessionStore) -> None:
    sid = store.create_session()
    store.append_message(
        sid,
        role="assistant",
        content="answer",
        metadata={"tokens": 100, "model": "haiku"},
    )
    messages = store.load_messages(sid)
    assert messages[0]["metadata"]["tokens"] == 100


def test_list_sessions_includes_message_count(store: SessionStore) -> None:
    sid = store.create_session()
    store.append_message(sid, role="user", content="msg1")
    store.append_message(sid, role="assistant", content="msg2")
    sessions = store.list_sessions()
    assert sessions[0]["message_count"] == 2


def test_session_has_timestamp(store: SessionStore) -> None:
    sid = store.create_session()
    store.append_message(sid, role="user", content="test")
    messages = store.load_messages(sid)
    assert "timestamp" in messages[0]


def test_append_llm_round(store: SessionStore) -> None:
    sid = store.create_session()
    store.append_llm_round(
        sid,
        round_num=0,
        model="claude-sonnet-4-5",
        tokens_in=1234,
        tokens_out=567,
        stop_reason="tool_use",
    )
    events = store.load_all_events(sid)
    llm_events = [e for e in events if e["type"] == "llm_round"]
    assert len(llm_events) == 1
    e = llm_events[0]
    assert e["round"] == 0
    assert e["model"] == "claude-sonnet-4-5"
    assert e["tokens_in"] == 1234
    assert e["tokens_out"] == 567
    assert e["stop_reason"] == "tool_use"
    assert "timestamp" in e


def test_append_tool_call(store: SessionStore) -> None:
    sid = store.create_session()
    store.append_tool_call(
        sid,
        tool_call_id="toolu_01ABC",
        name="create_workflow",
        args={"name": "News Search", "steps": ["fetch", "summarize"]},
        agent="mycelos",
    )
    events = store.load_all_events(sid)
    tool_events = [e for e in events if e["type"] == "tool_call"]
    assert len(tool_events) == 1
    e = tool_events[0]
    assert e["tool_call_id"] == "toolu_01ABC"
    assert e["name"] == "create_workflow"
    assert e["args"]["name"] == "News Search"
    assert e["agent"] == "mycelos"
    assert "timestamp" in e


def test_append_tool_result(store: SessionStore) -> None:
    sid = store.create_session()
    store.append_tool_result(
        sid,
        tool_call_id="toolu_01ABC",
        name="create_workflow",
        result={"status": "created", "workflow_id": "wf_123"},
        duration_ms=142,
    )
    events = store.load_all_events(sid)
    result_events = [e for e in events if e["type"] == "tool_result"]
    assert len(result_events) == 1
    e = result_events[0]
    assert e["tool_call_id"] == "toolu_01ABC"
    assert e["name"] == "create_workflow"
    assert e["result"]["workflow_id"] == "wf_123"
    assert e["duration_ms"] == 142


def test_append_tool_error(store: SessionStore) -> None:
    sid = store.create_session()
    store.append_tool_error(
        sid,
        tool_call_id="toolu_02DEF",
        name="schedule_add",
        error="ValueError: cron expression invalid",
        traceback="Traceback (most recent call last):\n  File ...",
    )
    events = store.load_all_events(sid)
    error_events = [e for e in events if e["type"] == "tool_error"]
    assert len(error_events) == 1
    e = error_events[0]
    assert e["tool_call_id"] == "toolu_02DEF"
    assert e["name"] == "schedule_add"
    assert "invalid" in e["error"]
    assert e["traceback"].startswith("Traceback")


def test_append_tool_result_non_dict_result(store: SessionStore) -> None:
    """Tool results can be non-dict (strings, lists, None). Must still serialize."""
    sid = store.create_session()
    store.append_tool_result(
        sid, tool_call_id="t1", name="search", result="plain text result", duration_ms=50,
    )
    events = store.load_all_events(sid)
    assert events[-1]["result"] == "plain text result"


def test_list_sessions_with_stats(store: SessionStore) -> None:
    # Create two sessions, one with an error
    sid1 = store.create_session()
    store.append_message(sid1, role="user", content="Hello")
    store.append_llm_round(sid1, round_num=0, model="claude", tokens_in=10, tokens_out=20, stop_reason="end_turn")
    store.append_message(sid1, role="assistant", content="Hi there")
    store.update_session(sid1, title="Greeting")

    sid2 = store.create_session()
    store.append_message(sid2, role="user", content="Do something")
    store.append_tool_error(sid2, tool_call_id="t1", name="broken_tool", error="boom")

    stats = store.list_sessions_with_stats()
    assert len(stats) >= 2

    by_id = {s["session_id"]: s for s in stats}
    assert by_id[sid1]["title"] == "Greeting"
    assert by_id[sid1]["has_errors"] is False
    assert by_id[sid1]["event_count"] >= 3

    assert by_id[sid2]["has_errors"] is True


def test_purge_old_sessions(store: SessionStore, tmp_path: Path) -> None:
    import os
    from datetime import datetime, timedelta

    sid_old = store.create_session()
    store.append_message(sid_old, role="user", content="old")

    sid_new = store.create_session()
    store.append_message(sid_new, role="user", content="new")

    # Backdate the old session's file mtime to 40 days ago
    old_path = store._session_path(sid_old)
    old_time = (datetime.now() - timedelta(days=40)).timestamp()
    os.utime(old_path, (old_time, old_time))

    deleted = store.purge_old(days=30)
    assert deleted == 1
    assert not old_path.exists()
    assert store._session_path(sid_new).exists()


def test_backfill_titles_sets_first_user_message(store: SessionStore) -> None:
    """Legacy sessions without a title get retitled from their first user
    message. Sessions that already have a title are left alone."""
    untitled = store.create_session()
    store.append_message(untitled, role="user", content="Remind me to buy milk")
    store.append_message(untitled, role="assistant", content="ok")

    titled = store.create_session()
    store.update_session(titled, title="Existing title")
    store.append_message(titled, role="user", content="Something else")

    count = store.backfill_titles_from_first_message()
    assert count == 1  # only the untitled one

    assert store.get_session_meta(untitled)["title"] == "Remind me to buy milk"
    assert store.get_session_meta(titled)["title"] == "Existing title"


def test_backfill_titles_truncates_to_60_chars(store: SessionStore) -> None:
    long_msg = "x" * 200
    sid = store.create_session()
    store.append_message(sid, role="user", content=long_msg)

    store.backfill_titles_from_first_message()
    title = store.get_session_meta(sid)["title"]
    assert len(title) <= 61  # 60 + ellipsis
    assert title.endswith("…")


def test_backfill_titles_skips_sessions_with_no_user_message(store: SessionStore) -> None:
    """Edge case: a session exists but nothing was said yet — leave it alone."""
    sid = store.create_session()
    # no append_message call
    count = store.backfill_titles_from_first_message()
    assert count == 0
    assert "title" not in store.get_session_meta(sid)
