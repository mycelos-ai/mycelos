"""Unit tests for bucket_note() — deterministic parent-path choice."""
from __future__ import annotations

from mycelos.knowledge.service import bucket_note


def test_bucket_note_reminder_goes_to_tasks() -> None:
    assert bucket_note({"reminder": True}) == "tasks"


def test_bucket_note_due_goes_to_tasks() -> None:
    assert bucket_note({"due": "2026-04-10T12:00:00Z"}) == "tasks"


def test_bucket_note_reminder_and_due_go_to_tasks() -> None:
    assert bucket_note({"reminder": True, "due": "2026-04-10T12:00:00Z"}) == "tasks"


def test_bucket_note_plain_goes_to_notes() -> None:
    assert bucket_note({}) == "notes"


def test_bucket_note_explicit_parent_wins() -> None:
    assert (
        bucket_note({"parent_path": "projects/mycelos", "reminder": True})
        == "projects/mycelos"
    )


def test_bucket_note_empty_parent_still_buckets() -> None:
    assert bucket_note({"parent_path": "", "due": "2026-04-10T12:00:00Z"}) == "tasks"


def test_bucket_note_none_values_are_ignored() -> None:
    assert bucket_note({"reminder": None, "due": None}) == "notes"
