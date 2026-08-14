"""SourceAttachmentService — declarative state for source placement."""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

from mycelos.knowledge.source_attachment import SourceAttachmentService
from mycelos.storage.database import SQLiteStorage


@pytest.fixture
def storage(tmp_path: Path) -> SQLiteStorage:
    s = SQLiteStorage(tmp_path / "source_attachment.db")
    s.initialize()
    # "other" is used to test per-user isolation and isn't part of the
    # conftest auto-seed list.
    s.execute(
        "INSERT OR IGNORE INTO users (id, name, status) VALUES (?, ?, ?)",
        ("other", "other", "active"),
    )
    return s


@pytest.fixture
def app():
    """A real App + KnowledgeBase, needed for the topic-lifecycle tests below
    (rename/merge/delete run real KnowledgeBase code, not a fake)."""
    from mycelos.app import App
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["MYCELOS_MASTER_KEY"] = "test-key-source-attachment"
        a = App(Path(tmp))
        a.initialize()
        yield a


class _FakeNotifier:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def notify_change(self, description: str, trigger: str = "service") -> None:
        self.calls.append((description, trigger))


class _FakeAudit:
    def __init__(self) -> None:
        self.events: list[tuple] = []

    def log(self, event_type, user_id=None, details=None) -> None:
        self.events.append((event_type, user_id, details))


def test_attach_and_list(storage) -> None:
    svc = SourceAttachmentService(storage)
    svc.attach("gmail", "topics/work/vorfina", user_id="default")
    svc.attach("gmail", "topics/private", user_id="default")
    assert svc.list_attachments("gmail", "default") == [
        "topics/work/vorfina", "topics/private",
    ]  # creation order — fallback_path depends on it


def test_attach_is_idempotent(storage) -> None:
    svc = SourceAttachmentService(storage)
    svc.attach("gmail", "topics/work", user_id="default")
    svc.attach("gmail", "topics/work", user_id="default")
    assert svc.list_attachments("gmail", "default") == ["topics/work"]


def test_detach(storage) -> None:
    svc = SourceAttachmentService(storage)
    svc.attach("gmail", "topics/work", user_id="default")
    svc.detach("gmail", "topics/work", user_id="default")
    assert svc.list_attachments("gmail", "default") == []


def test_attachments_are_per_source_and_user(storage) -> None:
    svc = SourceAttachmentService(storage)
    svc.attach("gmail", "topics/a", user_id="default")
    svc.attach("yt_summary", "topics/b", user_id="default")
    svc.attach("gmail", "topics/c", user_id="other")
    assert svc.list_attachments("gmail", "default") == ["topics/a"]
    assert svc.list_attachments("yt_summary", "default") == ["topics/b"]
    assert svc.list_attachments("gmail", "other") == ["topics/c"]


def test_rule_round_trip_and_single_row(storage) -> None:
    svc = SourceAttachmentService(storage)
    svc.set_rule("gmail", "Invoices go to Vorfina.", user_id="default")
    svc.set_rule("gmail", "Newsletters go to Archive.", user_id="default")
    assert svc.get_rule("gmail", "default") == "Newsletters go to Archive."
    row = storage.fetchone(
        "SELECT COUNT(*) AS c FROM source_rules WHERE source_id='gmail'")
    assert row["c"] == 1          # one rule set per source, by primary key


def test_missing_rule_is_empty_string(storage) -> None:
    svc = SourceAttachmentService(storage)
    assert svc.get_rule("never_configured", "default") == ""


def test_mutations_notify_config(storage) -> None:
    notifier = _FakeNotifier()
    svc = SourceAttachmentService(storage, notifier=notifier)
    svc.attach("gmail", "topics/work", user_id="default")
    svc.set_rule("gmail", "x", user_id="default")
    svc.detach("gmail", "topics/work", user_id="default")
    assert [t for _, t in notifier.calls] == [
        "source_attach", "source_rule", "source_detach",
    ]


def test_audit_never_contains_rule_text(storage) -> None:
    audit = _FakeAudit()
    svc = SourceAttachmentService(storage, audit=audit)
    secret = "Mails from klaus@mueller-gmbh.de go to Mandanten"
    svc.set_rule("gmail", secret, user_id="default")
    payloads = [str(details) for _, _, details in audit.events]
    assert all("mueller-gmbh" not in p for p in payloads)
    assert any("source.rule_updated" == e for e, _, _ in audit.events)


def test_migration_recreates_tables_on_existing_database(tmp_path: Path) -> None:
    """_ensure_schema() must create source_attachments/source_rules even on
    a database that was already initialized before these tables existed —
    not just on a brand-new file. Simulate that by dropping both tables on
    an initialized DB, then opening a *fresh* SQLiteStorage on the same
    file (mirrors the real "existing ~/.mycelos/mycelos.db" scenario)."""
    db_path = tmp_path / "existing.db"
    old = SQLiteStorage(db_path)
    old.initialize()
    old.execute("DROP TABLE source_attachments")
    old.execute("DROP TABLE source_rules")
    with pytest.raises(Exception):
        old.fetchone("SELECT 1 FROM source_attachments LIMIT 0")

    # Fresh instance, as a new process opening the pre-existing file would.
    reopened = SQLiteStorage(db_path)
    reopened.execute(
        "INSERT OR IGNORE INTO users (id, name, status) VALUES (?, ?, ?)",
        ("default", "default", "active"),
    )

    svc = SourceAttachmentService(reopened)
    svc.attach("gmail", "topics/work", user_id="default")
    assert svc.list_attachments("gmail", "default") == ["topics/work"]
    assert reopened.fetchone(
        "SELECT COUNT(*) AS c FROM source_rules")["c"] == 0


# ─── Topic lifecycle ────────────────────────────────────────────────────────
# Attachments point at topic paths, not ids — the topic operations below
# must keep those paths in sync so an attachment never silently points at a
# gone or stale folder.


def test_rename_topic_repoints_attachments(app) -> None:
    kb = app.knowledge_base
    svc = SourceAttachmentService(app.storage)
    old = kb.create_topic("Vorfina")
    svc.attach("gmail", old)
    new = kb.rename_topic(old, "Vorfina GmbH")
    assert svc.list_attachments("gmail") == [new]


def test_merge_topic_moves_attachments_to_target(app) -> None:
    kb = app.knowledge_base
    svc = SourceAttachmentService(app.storage)
    src = kb.create_topic("Alt")
    dst = kb.create_topic("Neu")
    svc.attach("gmail", src)
    kb.merge_topics(src, dst)
    assert svc.list_attachments("gmail") == [dst]


def test_merge_topic_drops_leftover_when_already_attached_to_target(app) -> None:
    """UPDATE OR IGNORE leaves the old row behind on a UNIQUE collision (the
    source is already attached to both source and target) — that leftover,
    still pointing at the merged-away topic, must be deleted too."""
    kb = app.knowledge_base
    svc = SourceAttachmentService(app.storage)
    src = kb.create_topic("Alt2")
    dst = kb.create_topic("Neu2")
    svc.attach("gmail", src)
    svc.attach("gmail", dst)
    kb.merge_topics(src, dst)
    assert svc.list_attachments("gmail") == [dst]


def test_delete_topic_removes_attachments(app) -> None:
    kb = app.knowledge_base
    svc = SourceAttachmentService(app.storage)
    topic = kb.create_topic("Weg")
    svc.attach("gmail", topic)
    kb.delete_topic(topic)
    assert svc.list_attachments("gmail") == []
