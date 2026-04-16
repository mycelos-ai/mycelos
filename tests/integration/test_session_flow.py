"""Integration test: Session creation, messages, and resume."""

import tempfile
import time
from pathlib import Path

import pytest

from mycelos.sessions.store import SessionStore


@pytest.mark.integration
def test_session_create_message_resume():
    """Full session flow: create -> messages -> new store -> load."""
    with tempfile.TemporaryDirectory() as tmp:
        conv_dir = Path(tmp) / "conversations"

        # Session 1: create and add messages
        store1 = SessionStore(conv_dir)
        sid = store1.create_session(user_id="stefan")
        store1.append_message(sid, role="user", content="Hallo!")
        store1.append_message(
            sid,
            role="assistant",
            content="Willkommen!",
            metadata={"tokens": 50, "model": "haiku"},
        )
        store1.append_message(
            sid, role="user", content="Fasse meine Emails zusammen"
        )
        store1.append_message(
            sid,
            role="assistant",
            content="## Zusammenfassung\n...",
            metadata={"tokens": 200, "model": "sonnet"},
        )

        # Simulate restart — new store instance
        store2 = SessionStore(conv_dir)

        # Find latest session
        latest = store2.get_latest_session()
        assert latest == sid

        # Load messages
        messages = store2.load_messages(sid)
        assert len(messages) == 4
        assert messages[0]["content"] == "Hallo!"
        assert messages[3]["metadata"]["model"] == "sonnet"

        # Session listing shows message count
        sessions = store2.list_sessions()
        assert sessions[0]["message_count"] == 4


@pytest.mark.integration
def test_multiple_sessions_ordered():
    """Multiple sessions are listed newest first."""
    with tempfile.TemporaryDirectory() as tmp:
        store = SessionStore(Path(tmp) / "conv")
        s1 = store.create_session()
        store.append_message(s1, role="user", content="Session 1")
        time.sleep(0.05)
        s2 = store.create_session()
        store.append_message(s2, role="user", content="Session 2")
        time.sleep(0.05)
        s3 = store.create_session()
        store.append_message(s3, role="user", content="Session 3")

        sessions = store.list_sessions()
        assert sessions[0]["session_id"] == s3  # newest first
        assert sessions[2]["session_id"] == s1  # oldest last
