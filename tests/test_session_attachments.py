"""Tests for SessionAttachmentStore — per-session file storage."""

from __future__ import annotations

from pathlib import Path

import pytest

from mycelos.files.session_attachments import (
    SessionAttachmentStore,
    SIZE_CAPS_BYTES,
    content_kind,
    media_type,
)


def test_save_and_read(tmp_path: Path) -> None:
    store = SessionAttachmentStore(tmp_path)
    saved = store.save("sess-1", b"hello world", "greeting.txt")
    assert saved.exists()
    assert saved.read_bytes() == b"hello world"
    assert store.read("sess-1", "greeting.txt") == b"hello world"


def test_save_creates_per_session_folder(tmp_path: Path) -> None:
    store = SessionAttachmentStore(tmp_path)
    store.save("sess-A", b"a", "x.txt")
    store.save("sess-B", b"b", "x.txt")
    assert (tmp_path / "sess-A" / "attachments" / "x.txt").exists()
    assert (tmp_path / "sess-B" / "attachments" / "x.txt").exists()


def test_filename_collision_gets_suffix(tmp_path: Path) -> None:
    store = SessionAttachmentStore(tmp_path)
    a = store.save("s", b"first", "report.pdf")
    b = store.save("s", b"second", "report.pdf")
    assert a.name == "report.pdf"
    assert b.name == "report-2.pdf"
    assert a.read_bytes() == b"first"
    assert b.read_bytes() == b"second"


def test_path_traversal_blocked(tmp_path: Path) -> None:
    store = SessionAttachmentStore(tmp_path)
    # The sanitize_filename helper strips path separators, so this
    # ends up as a flat name inside the session folder.
    saved = store.save("s", b"x", "../../etc/passwd")
    assert saved.parent == tmp_path / "s" / "attachments"
    assert ".." not in saved.name


def test_empty_data_rejected(tmp_path: Path) -> None:
    store = SessionAttachmentStore(tmp_path)
    with pytest.raises(ValueError):
        store.save("s", b"", "empty.txt")


def test_list_returns_attachments_oldest_first(tmp_path: Path) -> None:
    import time
    store = SessionAttachmentStore(tmp_path)
    store.save("s", b"first", "a.txt")
    time.sleep(0.01)
    store.save("s", b"second", "b.txt")
    items = store.list("s")
    assert [p.name for p in items] == ["a.txt", "b.txt"]


def test_list_empty_for_unknown_session(tmp_path: Path) -> None:
    store = SessionAttachmentStore(tmp_path)
    assert store.list("never-saved") == []


def test_read_missing_raises(tmp_path: Path) -> None:
    store = SessionAttachmentStore(tmp_path)
    with pytest.raises(FileNotFoundError):
        store.read("s", "nope.pdf")


def test_delete_session_removes_folder(tmp_path: Path) -> None:
    store = SessionAttachmentStore(tmp_path)
    store.save("doomed", b"x", "f.txt")
    assert (tmp_path / "doomed").exists()
    store.delete_session("doomed")
    assert not (tmp_path / "doomed").exists()


def test_delete_session_idempotent(tmp_path: Path) -> None:
    store = SessionAttachmentStore(tmp_path)
    # No raise even when nothing was ever saved
    store.delete_session("never-existed")


def test_content_kind_classifies() -> None:
    assert content_kind(Path("x.pdf")) == "document"
    assert content_kind(Path("x.PNG")) == "image"
    assert content_kind(Path("x.jpg")) == "image"
    assert content_kind(Path("x.webp")) == "image"
    assert content_kind(Path("x.txt")) == "text"
    assert content_kind(Path("x.md")) == "text"
    assert content_kind(Path("x.csv")) == "text"
    assert content_kind(Path("x.exe")) == "unsupported"


def test_media_type() -> None:
    assert media_type(Path("x.pdf")) == "application/pdf"
    assert media_type(Path("x.png")) == "image/png"
    assert media_type(Path("x.jpg")) == "image/jpeg"
    assert media_type(Path("x.txt")) == "text/plain"
    assert media_type(Path("x.csv")) == "text/plain"
    assert media_type(Path("x.json")) == "text/plain"
    assert media_type(Path("x.yaml")) == "text/plain"
    assert media_type(Path("x.yml")) == "text/plain"
    # Unknown fallback
    assert media_type(Path("x.bin")) == "application/octet-stream"


def test_session_id_sanitized(tmp_path: Path) -> None:
    """Malicious session ids cannot escape the base dir."""
    store = SessionAttachmentStore(tmp_path)
    saved = store.save("../escape", b"x", "f.txt")
    # Sanitized session id keeps the file inside tmp_path
    assert tmp_path in saved.parents
    assert ".." not in str(saved.relative_to(tmp_path))


def test_size_caps_present() -> None:
    assert SIZE_CAPS_BYTES["pdf"] == 32 * 1024 * 1024
    assert SIZE_CAPS_BYTES["image"] == 5 * 1024 * 1024
    assert SIZE_CAPS_BYTES["text"] == 10 * 1024 * 1024


def test_purge_old_drops_attachment_folder(tmp_path: Path) -> None:
    """When SessionStore.purge_old removes a JSONL, the matching
    attachment folder must go too if attachment_store is passed.
    """
    import os
    import time
    from mycelos.sessions.store import SessionStore

    conversations_dir = tmp_path / "conversations"
    sessions_dir = tmp_path / "sessions"
    conversations_dir.mkdir()
    store = SessionStore(conversations_dir)
    attach = SessionAttachmentStore(sessions_dir)

    # Create a session JSONL + attachment folder, then make them ancient
    session_id = "old-sess"
    jsonl = conversations_dir / f"{session_id}.jsonl"
    jsonl.write_text('{"role": "user"}\n')
    attach.save(session_id, b"hello", "memo.txt")
    assert (sessions_dir / session_id).exists()

    ancient = time.time() - 100 * 86400  # 100 days ago
    os.utime(jsonl, (ancient, ancient))

    deleted = store.purge_old(days=30, attachment_store=attach)
    assert deleted == 1
    assert not jsonl.exists()
    assert not (sessions_dir / session_id).exists()


def test_purge_old_without_attachment_store_keeps_folder(tmp_path: Path) -> None:
    """Backwards-compat: without attachment_store, only JSONL is purged
    (folder stays — caller's responsibility to clean it up).
    """
    import os
    import time
    from mycelos.sessions.store import SessionStore

    conversations_dir = tmp_path / "conversations"
    sessions_dir = tmp_path / "sessions"
    conversations_dir.mkdir()
    store = SessionStore(conversations_dir)
    attach = SessionAttachmentStore(sessions_dir)

    session_id = "lonely"
    jsonl = conversations_dir / f"{session_id}.jsonl"
    jsonl.write_text('{"role": "user"}\n')
    attach.save(session_id, b"hi", "f.txt")

    ancient = time.time() - 100 * 86400
    os.utime(jsonl, (ancient, ancient))

    # No attachment_store passed
    deleted = store.purge_old(days=30)
    assert deleted == 1
    assert not jsonl.exists()
    # Folder still exists since cleanup wasn't requested
    assert (sessions_dir / session_id).exists()
