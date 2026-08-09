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


from mycelos.knowledge.organizer import AUTO_ACCEPT_CONFIDENCE, should_auto_accept


def test_should_auto_accept_high_confidence_move() -> None:
    assert should_auto_accept("move", 0.95) is True
    assert should_auto_accept("new_topic", 1.0) is True
    assert should_auto_accept("link", 0.99) is True


def test_should_auto_accept_below_floor_is_rejected() -> None:
    assert should_auto_accept("move", 0.94) is False
    assert should_auto_accept("link", 0.0) is False


def test_should_auto_accept_merge_never() -> None:
    # Merges are destructive (archive + eventual hard-delete of the
    # secondary note) — never auto-accepted regardless of confidence.
    assert should_auto_accept("merge", 1.0) is False


def test_should_auto_accept_unknown_kind_fails_closed() -> None:
    assert should_auto_accept("refine_type", 1.0) is False
    assert should_auto_accept("frobnicate", 1.0) is False


def test_auto_accept_floor_is_stricter_than_silent_apply() -> None:
    # The silent-apply path (fresh classification) uses 0.8; unattended
    # acceptance of *stale* suggestions must be stricter, not looser.
    from mycelos.knowledge.organizer import SILENT_CONFIDENCE
    assert AUTO_ACCEPT_CONFIDENCE > SILENT_CONFIDENCE
