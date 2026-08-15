"""The unified inbox: suggestions + obligations + failures, one list."""
from __future__ import annotations

import os
import tempfile
from datetime import date, timedelta
from pathlib import Path

import pytest

from mycelos.knowledge.inbox import InboxService
from mycelos.knowledge.inbox_model import InboxModel, list_uncertain_placements


@pytest.fixture
def app():
    from mycelos.app import App
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["MYCELOS_MASTER_KEY"] = "test-key-inbox-model"
        a = App(Path(tmp))
        a.initialize()
        yield a


def _yesterday() -> str:
    return (date.today() - timedelta(days=1)).isoformat()


def _seed_reminder(app, path: str, title: str = "Call the tax office") -> None:
    """A note that is a due reminder — mirrors how the reminder service
    finds work in :meth:`ReminderService.get_due_reminders_now`."""
    app.storage.execute(
        "INSERT INTO knowledge_notes (path, title, type, status, due, reminder) "
        "VALUES (?, ?, 'task', 'open', ?, 1)",
        (path, title, _yesterday()),
    )


# ---- Class 1 stays out --------------------------------------------------


def test_move_suggestions_never_appear(app) -> None:
    """Legacy rows from before the policy change must not resurface."""
    path = app.knowledge_base.write(title="X", content="y", topic="notes")
    InboxService(app.storage).add(path, "move", {"target": "topics/a"}, 0.6)
    entries = InboxModel(app.storage, app=app).list_entries("default")
    assert all(e["kind"] != "move" for e in entries)


# ---- Class 2: consequence ----------------------------------------------


def test_consequence_suggestions_appear_with_reason(app) -> None:
    path = app.knowledge_base.write(title="X", content="y", topic="notes")
    InboxService(app.storage).add(
        path, "merge", {"duplicate_path": "notes/z", "similarity": 0.95}, 0.95)
    entries = InboxModel(app.storage, app=app).list_entries("default")
    merge = next(e for e in entries if e["kind"] == "merge")
    assert merge["why"]                      # states why it is here
    assert "accept" in [a["id"] for a in merge["actions"]]
    assert merge["class"] == "consequence"


def test_merge_shows_no_confidence(app) -> None:
    """Spec line 85: a merge is never automatic, so a confidence number
    would imply a decision the value does not make."""
    path = app.knowledge_base.write(title="X", content="y", topic="notes")
    InboxService(app.storage).add(path, "merge", {"duplicate_path": "notes/z"}, 0.95)
    entries = InboxModel(app.storage, app=app).list_entries("default")
    merge = next(e for e in entries if e["kind"] == "merge")
    assert merge["confidence"] is None


def test_many_merges_stay_many_entries(app) -> None:
    """The hardest rule in the spec: ten merges are ten irreversible
    decisions, never one summary line."""
    inbox = InboxService(app.storage)
    for i in range(5):
        path = app.knowledge_base.write(
            title=f"Dup {i}", content="y", topic="notes")
        inbox.add(path, "merge", {"duplicate_path": f"notes/z-{i}"}, 0.95)
    entries = InboxModel(app.storage, app=app).list_entries("default")
    merges = [e for e in entries if e["kind"] == "merge"]
    assert len(merges) == 5
    assert all(e["collapsed_count"] == 1 for e in merges)


def test_every_entry_states_where_it_came_from(app) -> None:
    """Spec: what, why, how sure, the actions, and where it came from."""
    path = app.knowledge_base.write(title="X", content="y", topic="notes")
    InboxService(app.storage).add(path, "merge", {"duplicate_path": "notes/z"}, 0.95)
    entry = InboxModel(app.storage, app=app).list_entries("default")[0]
    for key in ("id", "kind", "class", "title", "why", "confidence",
                "actions", "source", "created_at", "collapsed_count"):
        assert key in entry, f"entry is missing {key}"
    assert entry["created_at"]
    assert len(entry["actions"]) <= 3         # "never more than three"


def test_unclassifiable_notes_appear(app) -> None:
    """The organizer gave up after the retry cap. Without a human this
    note stays invisible forever — it has no suggestion row."""
    path = app.knowledge_base.write(title="Gave up", content="y", topic="notes")
    app.storage.execute(
        "UPDATE knowledge_notes SET organizer_state='manual' WHERE path=?",
        (path,))
    entries = InboxModel(app.storage, app=app).list_entries("default")
    assert any(e["kind"] == "unclassifiable" for e in entries)


def test_scope_violations_appear_despite_being_stored_as_move(app) -> None:
    """The handler writes a scope violation with kind='move' — the only
    move row it still produces. Filtering on the kind alone would hide a
    real consequence entry."""
    path = app.knowledge_base.write(title="Mail", content="y", topic="notes")
    InboxService(app.storage).add(
        path, "move", {"target": "topics/work/vorfina"}, 0.0)
    entries = InboxModel(app.storage, app=app).list_entries("default")
    entry = next(e for e in entries if e["kind"] == "scope_violation")
    assert entry["class"] == "consequence"
    assert entry["why"]


def test_legacy_low_confidence_move_rows_stay_hidden(app) -> None:
    """Rows written before the policy change carry reason='low_confidence'
    and are optimization — they must not resurface as scope violations."""
    path = app.knowledge_base.write(title="Old", content="y", topic="notes")
    InboxService(app.storage).add(
        path, "move",
        {"target": "topics/a", "alternatives": [], "reason": "low_confidence"},
        0.6)
    assert InboxModel(app.storage, app=app).list_entries("default") == []


def test_accepted_suggestions_do_not_appear(app) -> None:
    path = app.knowledge_base.write(title="X", content="y", topic="notes")
    inbox = InboxService(app.storage)
    sid = inbox.add(path, "merge", {"duplicate_path": "notes/z"}, 0.95)
    inbox.accept(sid)
    assert InboxModel(app.storage, app=app).list_entries("default") == []


# ---- Class 3: obligation ------------------------------------------------


def test_due_reminders_appear_as_obligations(app) -> None:
    """A commitment is not a suggestion: it resolves by being done or
    postponed, never by accept/dismiss."""
    _seed_reminder(app, "notes/call-office")
    entries = InboxModel(app.storage, app=app).list_entries("default")
    entry = next(e for e in entries if e["kind"] == "reminder")
    assert entry["class"] == "obligation"
    action_ids = [a["id"] for a in entry["actions"]]
    assert "done" in action_ids
    assert "snooze" in action_ids
    assert "accept" not in action_ids
    assert "dismiss" not in action_ids
    assert entry["confidence"] is None       # an obligation is not a guess


def test_fired_reminders_do_not_appear(app) -> None:
    """reminder_fired_at is the guard the scheduler already honours."""
    _seed_reminder(app, "notes/already-sent")
    app.storage.execute(
        "UPDATE knowledge_notes SET reminder_fired_at='2026-08-14T09:00:00Z' "
        "WHERE path=?", ("notes/already-sent",))
    entries = InboxModel(app.storage, app=app).list_entries("default")
    assert all(e["kind"] != "reminder" for e in entries)


def test_overdue_tasks_appear_as_obligations(app) -> None:
    """An overdue task without a reminder flag is still a commitment."""
    app.storage.execute(
        "INSERT INTO knowledge_notes (path, title, type, status, due) "
        "VALUES ('notes/file-the-vat', 'File the VAT return', 'task', 'open', ?)",
        (_yesterday(),))
    entries = InboxModel(app.storage, app=app).list_entries("default")
    entry = next(e for e in entries if e["kind"] == "overdue_task")
    assert entry["class"] == "obligation"
    assert "done" in [a["id"] for a in entry["actions"]]


def test_a_task_is_listed_once_even_when_it_is_also_a_reminder(app) -> None:
    """A due reminder is already an overdue task. Counting it twice would
    make the number lie."""
    _seed_reminder(app, "notes/both")
    entries = InboxModel(app.storage, app=app).list_entries("default")
    assert len([e for e in entries if e["source"].get("path") == "notes/both"]) == 1


def test_future_tasks_do_not_appear(app) -> None:
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    app.storage.execute(
        "INSERT INTO knowledge_notes (path, title, type, status, due) "
        "VALUES ('notes/later', 'Later', 'task', 'open', ?)", (tomorrow,))
    entries = InboxModel(app.storage, app=app).list_entries("default")
    assert entries == []


def test_done_tasks_do_not_appear(app) -> None:
    app.storage.execute(
        "INSERT INTO knowledge_notes (path, title, type, status, due) "
        "VALUES ('notes/done', 'Done', 'task', 'done', ?)", (_yesterday(),))
    assert InboxModel(app.storage, app=app).list_entries("default") == []


# ---- Collapsing ---------------------------------------------------------


def test_a_collapsible_group_becomes_one_entry(app) -> None:
    """A 499-note import is one event, not 140 lines. The group carries
    how many entries it stands for."""
    model = InboxModel(app.storage, app=app)
    raw = [
        {"id": i, "kind": "move", "class": "consequence", "title": f"Note {i}",
         "why": "low confidence", "confidence": 0.6,
         "actions": [{"id": "accept", "label": "Accept"}],
         "source": {"path": f"notes/{i}", "run_id": "run-1",
                    "source": "yt-summary"},
         "created_at": "2026-08-15T10:00:00Z", "collapsed_count": 1,
         "run_id": "run-1"}
        for i in range(7)
    ]
    got = model._collapse(raw)
    assert len(got) == 1
    assert got[0]["collapsed_count"] == 7


def test_two_runs_stay_two_entries(app) -> None:
    model = InboxModel(app.storage, app=app)
    raw = []
    for run in ("run-1", "run-2"):
        for i in range(3):
            raw.append({
                "id": f"{run}-{i}", "kind": "move", "class": "consequence",
                "title": "N", "why": "w", "confidence": 0.6, "actions": [],
                "source": {"run_id": run, "source": "yt-summary"},
                "created_at": "2026-08-15T10:00:00Z", "collapsed_count": 1,
                "run_id": run,
            })
    got = model._collapse(raw)
    assert len(got) == 2
    assert {g["collapsed_count"] for g in got} == {3}


def test_collapsing_reads_the_run_id_from_the_note_provenance(app) -> None:
    """Where the run id comes from, end to end.

    Nothing records a run_id yet, so collapsing is dormant in production.
    This pins the field the model reads, so grouping switches on when the
    ingest starts writing one — instead of silently staying off.
    """
    model = InboxModel(app.storage, app=app)
    provenance = {"kind": "connector", "source": "yt-summary", "run_id": "run-42"}
    raw = [
        model._entry(
            entry_id=f"suggestion:{i}", kind="move",
            entry_class="consequence", title=f"Imported {i}", why="w",
            confidence=0.6, actions=[],
            source={**provenance, "path": f"notes/imported-{i}"},
            created_at="2026-08-15T10:00:00Z",
        )
        for i in range(4)
    ]
    got = model._collapse(raw)
    assert len(got) == 1
    assert got[0]["collapsed_count"] == 4
    # The surviving entry keeps a real id, not a synthetic one that
    # resolves to nothing.
    assert got[0]["id"] == "suggestion:0"


def test_entries_without_a_group_key_stand_alone(app) -> None:
    model = InboxModel(app.storage, app=app)
    raw = [
        {"id": i, "kind": "merge", "class": "consequence", "title": "M",
         "why": "w", "confidence": None, "actions": [],
         "source": {"run_id": "run-1", "source": "yt-summary"},
         "created_at": "2026-08-15T10:00:00Z", "collapsed_count": 1,
         "run_id": "run-1"}
        for i in range(4)
    ]
    assert len(model._collapse(raw)) == 4


# ---- The count ----------------------------------------------------------


def test_count_excludes_uncertain_placements(app) -> None:
    """The number must mean 'things that need you' — nothing else."""
    path = app.knowledge_base.write(title="Filed uncertainly", content="y",
                                    topic="notes")
    app.storage.execute(
        "UPDATE knowledge_notes SET placement_confidence=0.55 WHERE path=?",
        (path,))
    assert InboxModel(app.storage, app=app).count("default") == 0


def test_count_equals_the_number_of_entries(app) -> None:
    """One number, one list — they may never disagree."""
    inbox = InboxService(app.storage)
    for i in range(3):
        p = app.knowledge_base.write(title=f"N{i}", content="y", topic="notes")
        inbox.add(p, "merge", {"duplicate_path": f"notes/z-{i}"}, 0.9)
    _seed_reminder(app, "notes/reminder-1")
    model = InboxModel(app.storage, app=app)
    assert model.count("default") == len(model.list_entries("default"))
    assert model.count("default") == 4


def test_empty_inbox_counts_zero(app) -> None:
    assert InboxModel(app.storage, app=app).count("default") == 0


# ---- The review view ----------------------------------------------------


def test_uncertain_placements_list_is_shakiest_first(app) -> None:
    kb = app.knowledge_base
    a = kb.write(title="A", content="x", topic="notes")
    b = kb.write(title="B", content="x", topic="notes")
    app.storage.execute(
        "UPDATE knowledge_notes SET placement_confidence=0.7 WHERE path=?", (a,))
    app.storage.execute(
        "UPDATE knowledge_notes SET placement_confidence=0.4 WHERE path=?", (b,))
    got = list_uncertain_placements(app.storage, "default", limit=10)
    assert [g["path"] for g in got] == [b, a]


def test_certain_notes_are_not_in_the_review_list(app) -> None:
    app.knowledge_base.write(title="Certain", content="x", topic="notes")
    assert list_uncertain_placements(app.storage, "default", limit=10) == []


def test_uncertain_placement_states_where_it_landed(app) -> None:
    """Reviewing needs the folder and the confidence, not just a path."""
    path = app.knowledge_base.write(title="Shaky", content="x", topic="notes")
    app.storage.execute(
        "UPDATE knowledge_notes SET placement_confidence=0.5 WHERE path=?",
        (path,))
    entry = list_uncertain_placements(app.storage, "default", limit=10)[0]
    assert entry["title"] == "Shaky"
    assert entry["confidence"] == 0.5
    assert "parent" in entry
    assert "source" in entry


def test_uncertain_placement_limit_is_honoured(app) -> None:
    kb = app.knowledge_base
    for i in range(5):
        p = kb.write(title=f"N{i}", content="x", topic="notes")
        app.storage.execute(
            "UPDATE knowledge_notes SET placement_confidence=? WHERE path=?",
            (0.1 * (i + 1), p))
    assert len(list_uncertain_placements(app.storage, "default", limit=2)) == 2
