"""Source-to-folder attachment: pure subtree logic + the storage service.

The pure functions decide *where a source may file*; they know nothing
about storage or LLMs, mirroring ``organizer.py``. The service below owns
the two declarative-state tables (Constitution Rule 2).

An attachment opens a subtree: the attached folder and everything beneath
it, never above and never into a sibling branch. The empty string is the
root attachment and means "anywhere".
"""
from __future__ import annotations

from typing import Any


def _covers(attachment: str, path: str) -> bool:
    """True when `path` is `attachment` itself or lives beneath it.

    Segment-aware on purpose: a naive startswith would let an attachment
    on "topics/work" leak into "topics/workshop".
    """
    if attachment == "":
        return True
    return path == attachment or path.startswith(attachment + "/")


def permitted_paths(attachments: list[str], all_topics: list[str]) -> list[str]:
    """Every existing topic the source may file into, sorted, deduplicated."""
    if not attachments:
        return []
    permitted = {
        path for path in all_topics
        for attachment in attachments
        if _covers(attachment, path)
    }
    return sorted(permitted)


def is_permitted(path: str, attachments: list[str]) -> bool:
    """Whether a proposed path lies inside any attachment's subtree."""
    if not path or not attachments:
        return False
    return any(_covers(a, path) for a in attachments)


def fallback_path(attachments: list[str]) -> str:
    """Where content lands when nothing fits: the first attachment, else root."""
    return attachments[0] if attachments else ""


def needs_confirmation(proposed_path: str, attachments: list[str]) -> bool:
    """True when a NEW folder would sit directly under an attachment.

    Those open a new main category for the source and always go to the
    inbox, regardless of confidence. Anything deeper is fine-sorting
    inside a category the user already accepted.
    """
    if not proposed_path:
        return False
    parent = proposed_path.rsplit("/", 1)[0] if "/" in proposed_path else ""
    for attachment in attachments:
        if attachment == "":
            # Root attachment: a top-level topic is a structural decision.
            if parent in ("", "topics"):
                return True
        elif parent == attachment:
            return True
    return False


class SourceAttachmentService:
    """Owns source_attachments and source_rules (declarative state).

    Every mutation notifies the config layer so the change lands in a
    generation and is rollback-able, and logs an audit event. Audit
    payloads deliberately carry no rule text — a rule may name clients.
    """

    def __init__(self, storage: Any, notifier: Any = None, audit: Any = None) -> None:
        self._storage = storage
        self._notifier = notifier
        self._audit = audit

    # ---- attachments -------------------------------------------------

    def attach(self, source_id: str, topic_path: str, user_id: str = "default") -> None:
        self._storage.execute(
            "INSERT OR IGNORE INTO source_attachments "
            "(source_id, user_id, topic_path) VALUES (?, ?, ?)",
            (source_id, user_id, topic_path),
        )
        self._log(user_id, "source.attached",
                  {"source": source_id, "path": topic_path})
        self._notify(f"Source {source_id} attached to {topic_path or 'root'}",
                     "source_attach")

    def detach(self, source_id: str, topic_path: str, user_id: str = "default") -> None:
        self._storage.execute(
            "DELETE FROM source_attachments "
            "WHERE source_id=? AND user_id=? AND topic_path=?",
            (source_id, user_id, topic_path),
        )
        self._log(user_id, "source.detached",
                  {"source": source_id, "path": topic_path})
        self._notify(f"Source {source_id} detached from {topic_path or 'root'}",
                     "source_detach")

    def list_attachments(self, source_id: str, user_id: str = "default") -> list[str]:
        """Attached folders in creation order — fallback_path uses the first."""
        rows = self._storage.fetchall(
            "SELECT topic_path FROM source_attachments "
            "WHERE source_id=? AND user_id=? ORDER BY id ASC",
            (source_id, user_id),
        )
        return [r["topic_path"] for r in rows]

    # ---- rule --------------------------------------------------------

    def set_rule(self, source_id: str, rule_text: str, user_id: str = "default") -> None:
        self._storage.execute(
            "INSERT INTO source_rules (source_id, user_id, rule_text, updated_at) "
            "VALUES (?, ?, ?, strftime('%Y-%m-%dT%H:%M:%fZ','now')) "
            "ON CONFLICT(source_id, user_id) DO UPDATE SET "
            "rule_text=excluded.rule_text, updated_at=excluded.updated_at",
            (source_id, user_id, rule_text),
        )
        # No rule text in the audit payload — it may name clients.
        self._log(user_id, "source.rule_updated",
                  {"source": source_id, "length": len(rule_text)})
        self._notify(f"Rule updated for source {source_id}", "source_rule")

    def get_rule(self, source_id: str, user_id: str = "default") -> str:
        row = self._storage.fetchone(
            "SELECT rule_text FROM source_rules WHERE source_id=? AND user_id=?",
            (source_id, user_id),
        )
        return row["rule_text"] if row else ""

    # ---- helpers -----------------------------------------------------

    def _notify(self, description: str, trigger: str) -> None:
        if self._notifier:
            self._notifier.notify_change(description, trigger)

    def _log(self, user_id: str, event: str, details: dict) -> None:
        if self._audit:
            self._audit.log(event, user_id=user_id, details=details)
