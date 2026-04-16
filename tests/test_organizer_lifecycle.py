from __future__ import annotations

import freezegun

from mycelos.knowledge.organizer import (
    is_archived_older_than,
    is_done_task_older_than,
    is_fired_reminder_past,
)


@freezegun.freeze_time("2026-04-15T12:00:00Z")
def test_done_task_older_than_7_days() -> None:
    note = {"status": "done", "updated_at": "2026-04-07T11:00:00Z", "type": "task"}
    assert is_done_task_older_than(note, days=7) is True


@freezegun.freeze_time("2026-04-15T12:00:00Z")
def test_done_task_exactly_under_7_days() -> None:
    note = {"status": "done", "updated_at": "2026-04-08T13:00:00Z", "type": "task"}
    assert is_done_task_older_than(note, days=7) is False


@freezegun.freeze_time("2026-04-15T12:00:00Z")
def test_active_task_not_archived() -> None:
    note = {"status": "active", "updated_at": "2026-01-01T00:00:00Z", "type": "task"}
    assert is_done_task_older_than(note, days=7) is False


@freezegun.freeze_time("2026-04-15T12:00:00Z")
def test_fired_reminder_past_one_day() -> None:
    note = {"reminder": True, "due": "2026-04-13T12:00:00Z"}
    assert is_fired_reminder_past(note, days=1) is True


@freezegun.freeze_time("2026-04-15T12:00:00Z")
def test_reminder_not_yet_due() -> None:
    note = {"reminder": True, "due": "2026-04-20T12:00:00Z"}
    assert is_fired_reminder_past(note, days=1) is False


@freezegun.freeze_time("2026-04-15T12:00:00Z")
def test_non_reminder_never_matches() -> None:
    note = {"reminder": False, "due": "2020-01-01T00:00:00Z"}
    assert is_fired_reminder_past(note, days=1) is False


@freezegun.freeze_time("2026-05-15T12:00:00Z")
def test_archived_older_than_30_days() -> None:
    note = {"status": "archived", "organizer_seen_at": "2026-04-10T10:00:00Z"}
    assert is_archived_older_than(note, days=30) is True


@freezegun.freeze_time("2026-05-15T12:00:00Z")
def test_archived_under_30_days() -> None:
    note = {"status": "archived", "organizer_seen_at": "2026-05-01T10:00:00Z"}
    assert is_archived_older_than(note, days=30) is False


@freezegun.freeze_time("2026-05-15T12:00:00Z")
def test_active_note_never_hard_deleted() -> None:
    note = {"status": "active", "organizer_seen_at": "2026-01-01T00:00:00Z"}
    assert is_archived_older_than(note, days=30) is False
