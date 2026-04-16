"""Pure organizer logic — classification branching + lifecycle predicates.

No scheduler, no LLM wiring, no storage writes. This module is the
testable core; the runtime glue lives in
``agents/handlers/knowledge_organizer_handler.py``.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Protocol


SILENT_CONFIDENCE = 0.8
DUPLICATE_THRESHOLD = 0.92
DUPLICATE_TOP_K = 3


def _parse_iso(value: str | None) -> datetime | None:
    """Parse an ISO datetime string, always returning a timezone-aware datetime.

    DB values may be naive (no timezone suffix) or aware (with Z or +00:00).
    We normalize to UTC so comparisons with ``datetime.now(tz=utc)`` work.
    """
    if not value:
        return None
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def is_done_task_older_than(note: dict, *, days: int) -> bool:
    if note.get("type") != "task":
        return False
    if note.get("status") != "done":
        return False
    updated = _parse_iso(note.get("updated_at"))
    if not updated:
        return False
    now = datetime.now(tz=timezone.utc)
    return (now - updated) >= timedelta(days=days)


def is_fired_reminder_past(note: dict, *, days: int) -> bool:
    if not note.get("reminder"):
        return False
    due = _parse_iso(note.get("due"))
    if not due:
        return False
    now = datetime.now(tz=timezone.utc)
    return (now - due) >= timedelta(days=days)


def is_archived_older_than(note: dict, *, days: int) -> bool:
    """True if the note is archived and the archive timestamp exceeds `days`."""
    if note.get("status") != "archived":
        return False
    ts = _parse_iso(note.get("organizer_seen_at") or note.get("updated_at"))
    if not ts:
        return False
    now = datetime.now(tz=timezone.utc)
    return (now - ts) >= timedelta(days=days)


@dataclass
class Classification:
    topic_path: str | None
    confidence: float
    related_note_paths: list[str]
    new_topic_name: str | None


class ClassifierCallable(Protocol):
    def __call__(self, note: dict, topics: list[str], neighbors: list[dict]) -> Classification: ...


def decide_action(
    result: Classification,
    *,
    topic_exists: bool,
) -> str:
    """Return one of: 'silent_move' | 'suggest_move' | 'suggest_new_topic'."""
    if result.confidence >= SILENT_CONFIDENCE and topic_exists and result.topic_path:
        return "silent_move"
    if result.confidence >= SILENT_CONFIDENCE and result.new_topic_name:
        return "suggest_new_topic"
    return "suggest_move"
