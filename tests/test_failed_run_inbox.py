"""A routine that stopped working reaches the inbox.

Package 2 reserved the ``failed_run`` kind and left it without a producer,
because nothing durable recorded a run. Package 3's run rows changed that.
These tests pin the entry's shape, its volume rule, and the two traps an
earlier review named:

* Filtering must key on ``kind``, never on ``workflow_id IS NULL``. An
  ad-hoc workflow row carries a null workflow id too, and merging it into
  a source's entry would make the retry action dispatch an ingest for a
  workflow.
* A run stamped by the orphan sweep means "outcome unknown", not "this
  routine is broken". The sweep cannot tell a crash from a run still
  executing in an old process.
"""
from __future__ import annotations

import os
import tempfile
from datetime import date, timedelta
from pathlib import Path

import pytest

from mycelos.knowledge.inbox import InboxService
from mycelos.knowledge.inbox_model import InboxModel
from mycelos.scheduler.jobs import sweep_orphaned_workflow_runs
from mycelos.scheduler.run_recorder import (
    CAUSES,
    ORPHANED_RUN_CAUSE,
    RunRecorder,
)


@pytest.fixture
def app():
    """Same fixture as tests/test_inbox_model.py — one real App on a temp dir."""
    from mycelos.app import App
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["MYCELOS_MASTER_KEY"] = "test-key-failed-run-inbox"
        a = App(Path(tmp))
        a.initialize()
        yield a


def _model(app) -> InboxModel:
    return InboxModel(app.storage, app=app)


def _failed_sync(app, source: str, cause: str = CAUSES["source_failed"]) -> str:
    """One failed source_sync run row, written the way the scheduler writes it."""
    recorder = RunRecorder(app.storage)
    run_id = recorder.start("source_sync", source)
    recorder.fail(run_id, cause)
    return run_id


def _completed_sync(app, source: str, counts: dict | None = None) -> str:
    recorder = RunRecorder(app.storage)
    run_id = recorder.start("source_sync", source)
    recorder.finish(run_id, counts or {"created": 3, "source": source})
    return run_id


def _running_sync(app, source: str) -> str:
    return RunRecorder(app.storage).start("source_sync", source)


def _stamp(app, run_id: str, created_at: str) -> None:
    """Move a run row in time so 'latest' is unambiguous."""
    app.storage.execute(
        "UPDATE workflow_runs SET created_at = ?, updated_at = ? WHERE id = ?",
        (created_at, created_at, run_id),
    )


def _failed_runs(entries: list[dict]) -> list[dict]:
    return [e for e in entries if e["kind"] == "failed_run"]


# ---- the entry ----------------------------------------------------------


def test_a_failed_sync_becomes_a_consequence_entry(app) -> None:
    """Silence would mean a dead sync nobody notices — the Class 2 reason."""
    _failed_sync(app, "yt-summary")
    entries = _model(app).list_entries("default")
    entry = next(e for e in _failed_runs(entries))
    assert entry["class"] == "consequence"
    assert entry["kind"] == "failed_run"


def test_the_entry_names_the_source_and_the_cause(app) -> None:
    """Spec: what, and why — in the user's language, in one line."""
    _failed_sync(app, "yt-summary", CAUSES["source_unreachable"])
    entry = _failed_runs(_model(app).list_entries("default"))[0]
    assert "yt-summary" in entry["title"]
    assert "yt-summary" in entry["why"]
    assert CAUSES["source_unreachable"] in entry["why"]


def test_the_entry_states_where_it_came_from(app) -> None:
    """Every key of the shared entry shape is present, as for every kind."""
    run_id = _failed_sync(app, "gmail")
    entry = _failed_runs(_model(app).list_entries("default"))[0]
    for key in ("id", "kind", "class", "title", "why", "confidence",
                "actions", "source", "created_at", "collapsed_count"):
        assert key in entry, f"entry is missing {key}"
    assert entry["source"]["routine_key"] == "gmail"
    assert entry["source"]["run_id"] == run_id
    assert entry["created_at"]


def test_a_failed_run_shows_no_confidence(app) -> None:
    """A failure is a fact, not a judgement call. A number would mislead."""
    _failed_sync(app, "gmail")
    entry = _failed_runs(_model(app).list_entries("default"))[0]
    assert entry["confidence"] is None


# ---- what produces nothing ---------------------------------------------


def test_a_completed_run_produces_nothing(app) -> None:
    _completed_sync(app, "yt-summary")
    assert _failed_runs(_model(app).list_entries("default")) == []


def test_a_running_run_produces_nothing(app) -> None:
    """A run in flight is not a failure. It has not ended yet."""
    _running_sync(app, "yt-summary")
    assert _failed_runs(_model(app).list_entries("default")) == []


def test_an_empty_run_table_produces_nothing(app) -> None:
    assert _model(app).list_entries("default") == []


# ---- constraint 1: filter on kind, never on a null workflow_id ---------


def test_an_adhoc_workflow_failure_does_not_merge_into_a_source_entry(app) -> None:
    """The trap this entry was reviewed for, twice.

    An ad-hoc workflow run (one built in code and never registered) carries
    ``workflow_id=NULL`` and keeps its identity in ``routine_key`` — exactly
    the shape a source sync has. A read that filtered on the null workflow id,
    or on the routine key alone, would fold the workflow's failure into the
    source's entry. The retry action would then dispatch an ingest for a
    workflow.
    """
    app.workflow_run_manager.start(
        workflow_id=None, run_id="adhoc-1", routine_key="yt-summary",
    )
    app.workflow_run_manager.fail("adhoc-1", error="The workflow run failed.")
    _failed_sync(app, "yt-summary")

    entries = _failed_runs(_model(app).list_entries("default"))
    assert len(entries) == 1
    entry = entries[0]
    assert entry["source"]["kind"] == "source_sync"
    # One failure of the source itself, not two.
    assert entry["source"]["failure_count"] == 1


def test_an_adhoc_workflow_failure_alone_produces_no_entry(app) -> None:
    """A workflow failure has its own surfaces; it is not a routine entry."""
    app.workflow_run_manager.start(
        workflow_id=None, run_id="adhoc-2", routine_key="daily-report",
    )
    app.workflow_run_manager.fail("adhoc-2", error="The workflow run failed.")
    assert _failed_runs(_model(app).list_entries("default")) == []


def test_a_failed_briefing_produces_no_entry(app) -> None:
    """The briefing retries itself at the next tick and has no retry route.

    An entry advertising an action nothing implements is the defect Package
    2's final review found in ``unclassifiable``. The briefing stays out
    until it has a real exit.
    """
    recorder = RunRecorder(app.storage)
    run_id = recorder.start("briefing", "briefing")
    recorder.fail(run_id, CAUSES["briefing_failed"])
    assert _failed_runs(_model(app).list_entries("default")) == []


# ---- the volume rule ---------------------------------------------------


def test_three_failing_routines_are_three_entries(app) -> None:
    """Consequence entries never collapse across routines (Package 2)."""
    for source in ("yt-summary", "gmail", "calendar"):
        _failed_sync(app, source)
    entries = _failed_runs(_model(app).list_entries("default"))
    assert len(entries) == 3
    assert {e["source"]["routine_key"] for e in entries} == {
        "yt-summary", "gmail", "calendar"
    }
    assert all(e["collapsed_count"] == 1 for e in entries)


def test_an_hourly_routine_failing_all_day_is_one_entry(app) -> None:
    """One entry per routine, carrying a failure count — not 24 entries.

    An entry per tick would make an hourly sync its own landfill, which is
    the inbox failure mode this redesign exists to remove.

    The count assertion is the load-bearing half. A ``count()`` that added
    the failure count instead of one would put **24** on the home badge
    while ``/api/inbox`` returned a single entry — a number that disagrees
    with the list, which is the count people learn to ignore. Every other
    count assertion in this file runs against a routine that failed once,
    where the two implementations are indistinguishable.
    """
    for hour in range(24):
        run_id = _failed_sync(app, "yt-summary")
        _stamp(app, run_id, f"2026-08-14T{hour:02d}:00:00.000Z")

    model = _model(app)
    entries = _failed_runs(model.list_entries("default"))
    assert len(entries) == 1
    assert entries[0]["source"]["failure_count"] == 24
    # The badge shows what the list shows: one, not twenty-four.
    assert model.count("default") == 1
    assert model.count("default") == len(model.list_entries("default"))


def test_the_entry_carries_the_latest_cause(app) -> None:
    """The reader needs the cause that is true now, not the first one.

    The rows are written so that insertion order disagrees with time order:
    the *latest* failure is inserted first. A query that returned "some row
    in the group" would pass on insertion order alone and hide the bug the
    day the two orders diverge on a real database.
    """
    late = _failed_sync(app, "yt-summary", CAUSES["response_unreadable"])
    _stamp(app, late, "2026-08-14T09:00:00.000Z")
    early = _failed_sync(app, "yt-summary", CAUSES["source_unreachable"])
    _stamp(app, early, "2026-08-14T01:00:00.000Z")

    entry = _failed_runs(_model(app).list_entries("default"))[0]
    assert CAUSES["response_unreadable"] in entry["why"]
    assert CAUSES["source_unreachable"] not in entry["why"]
    assert entry["source"]["run_id"] == late
    assert entry["source"]["last_failure_at"] == "2026-08-14T09:00:00.000Z"


def test_the_entry_is_dated_when_the_problem_started(app) -> None:
    """``created_at`` is the first failure since the last success.

    ``list_entries`` sorts consequence entries oldest first, so the thing
    waiting longest surfaces first. Dating the entry by its *latest*
    failure would invert that: a sync broken since Monday would sink below
    one that broke an hour ago, and sink again on every further tick.
    """
    first = _failed_sync(app, "yt-summary")
    _stamp(app, first, "2026-08-10T01:00:00.000Z")
    latest = _failed_sync(app, "yt-summary")
    _stamp(app, latest, "2026-08-14T01:00:00.000Z")

    entry = _failed_runs(_model(app).list_entries("default"))[0]
    assert entry["created_at"] == "2026-08-10T01:00:00.000Z"
    assert entry["source"]["last_failure_at"] == "2026-08-14T01:00:00.000Z"


def test_the_longest_broken_routine_sorts_first(app) -> None:
    """The ordering that dating decision exists to produce."""
    old = _failed_sync(app, "gmail")
    _stamp(app, old, "2026-08-10T01:00:00.000Z")
    # The long-broken one keeps failing; it must not sink for doing so.
    recent_repeat = _failed_sync(app, "gmail")
    _stamp(app, recent_repeat, "2026-08-14T09:00:00.000Z")
    fresh = _failed_sync(app, "yt-summary")
    _stamp(app, fresh, "2026-08-14T08:00:00.000Z")

    entries = _failed_runs(_model(app).list_entries("default"))
    assert [e["source"]["routine_key"] for e in entries] == ["gmail", "yt-summary"]


def test_a_repeated_failure_is_not_a_collapsed_summary(app) -> None:
    """The count lives in the entry's own provenance, not in collapse.

    ``collapsed_count`` is Package 2's bulk-import mechanism and belongs to
    optimization volume. A consequence entry must never use it, or a later
    reader would think ten failures had been folded into a summary that
    hides them.
    """
    for _ in range(3):
        _failed_sync(app, "gmail")
    entry = _failed_runs(_model(app).list_entries("default"))[0]
    assert entry["collapsed_count"] == 1
    assert entry["source"]["failure_count"] == 3


# ---- implicit resolve --------------------------------------------------


def test_the_entry_disappears_once_the_routine_succeeds(app) -> None:
    """A synthesized entry resolves by the routine working again.

    No dismiss row is written, so there is nothing to go stale. This is the
    property that keeps the entry from becoming the sticky one nobody can
    clear.
    """
    failed = _failed_sync(app, "yt-summary")
    _stamp(app, failed, "2026-08-14T01:00:00.000Z")
    assert len(_failed_runs(_model(app).list_entries("default"))) == 1

    ok = _completed_sync(app, "yt-summary")
    _stamp(app, ok, "2026-08-14T02:00:00.000Z")
    assert _failed_runs(_model(app).list_entries("default")) == []


def test_only_failures_since_the_last_success_are_counted(app) -> None:
    """Yesterday's outage is not today's problem, and not today's count."""
    old = _failed_sync(app, "gmail")
    _stamp(app, old, "2026-08-13T01:00:00.000Z")
    ok = _completed_sync(app, "gmail")
    _stamp(app, ok, "2026-08-13T02:00:00.000Z")
    for hour in (3, 4):
        run_id = _failed_sync(app, "gmail")
        _stamp(app, run_id, f"2026-08-13T{hour:02d}:00:00.000Z")

    entry = _failed_runs(_model(app).list_entries("default"))[0]
    assert entry["source"]["failure_count"] == 2


def test_a_success_for_another_source_does_not_clear_the_entry(app) -> None:
    """Each routine is resolved by its own next success, nobody else's."""
    _failed_sync(app, "yt-summary")
    _completed_sync(app, "gmail")
    entries = _failed_runs(_model(app).list_entries("default"))
    assert len(entries) == 1
    assert entries[0]["source"]["routine_key"] == "yt-summary"


def test_a_run_started_after_the_failure_does_not_clear_the_entry(app) -> None:
    """A retry in flight has not succeeded yet. Fail closed.

    Reading the *latest* run and showing an entry only when it is 'failed'
    would drop the entry here — the sync would look fixed while it is still
    unproven. Only a completed run clears it.
    """
    failed = _failed_sync(app, "yt-summary")
    _stamp(app, failed, "2026-08-14T01:00:00.000Z")
    running = _running_sync(app, "yt-summary")
    _stamp(app, running, "2026-08-14T02:00:00.000Z")
    assert len(_failed_runs(_model(app).list_entries("default"))) == 1


def test_a_retry_that_crashes_does_not_hide_the_entry_forever(app) -> None:
    """The worst case of anchoring on the latest run instead of the success.

    A retry that crashes leaves its row 'running' until the next gateway
    start. If the entry keyed off the latest run's status, the dead sync
    would be invisible for exactly as long as the crashed row sat there —
    which is the failure mode the whole ingest hardening exists to prevent.
    """
    failed = _failed_sync(app, "yt-summary", CAUSES["source_unreachable"])
    _stamp(app, failed, "2026-08-14T01:00:00.000Z")
    crashed = _running_sync(app, "yt-summary")
    _stamp(app, crashed, "2026-08-14T02:00:00.000Z")

    entry = _failed_runs(_model(app).list_entries("default"))[0]
    # Still the real cause from the last run that actually ended.
    assert CAUSES["source_unreachable"] in entry["why"]

    # And once the sweep stamps the crashed row, it is still one entry.
    sweep_orphaned_workflow_runs(app)
    entries = _failed_runs(_model(app).list_entries("default"))
    assert len(entries) == 1
    assert entries[0]["source"]["failure_count"] == 2


def test_the_sweep_stamps_the_cause_the_entry_reads(app) -> None:
    """The sweep writes the constant the read side compares against.

    It does **not** guard against a reworded cause, and cannot: both sides
    reference :data:`ORPHANED_RUN_CAUSE` by import, so rewording it moves
    both at once and renaming it is a ``NameError`` rather than a silent
    degradation. That is the point — the coupling is by reference, so
    drift is impossible by construction rather than caught by a test.

    What this pins is one step weaker and still worth holding: the sweep
    writes *that* constant to the ``error`` column and nothing else, and a
    row carrying it renders as ``outcome == 'unknown'``. A sweep that
    stamped some other text would leave the orphan indistinguishable from
    a real failure, and this catches that.
    """
    _running_sync(app, "gmail")
    sweep_orphaned_workflow_runs(app)
    row = app.storage.fetchone(
        "SELECT error FROM workflow_runs WHERE routine_key = 'gmail'"
    )
    assert row["error"] == ORPHANED_RUN_CAUSE
    entry = _failed_runs(_model(app).list_entries("default"))[0]
    assert entry["source"]["outcome"] == "unknown"


# ---- constraint 2: the orphan case -------------------------------------


def test_an_orphaned_run_reads_as_outcome_unknown(app) -> None:
    """The sweep stamps every 'running' row at gateway start.

    It cannot tell a crashed run from one still executing in an old process,
    so the entry must not send the reader hunting for a broken connector when
    the gateway simply restarted.
    """
    _running_sync(app, "yt-summary")
    sweep_orphaned_workflow_runs(app)

    entry = _failed_runs(_model(app).list_entries("default"))[0]
    assert entry["source"]["outcome"] == "unknown"
    why = entry["why"].lower()
    assert "unknown" in why or "not know" in why
    # It must not claim the routine is broken.
    assert "failed" not in entry["title"].lower()


def test_an_orphaned_run_is_distinguishable_from_a_real_failure(app) -> None:
    """Two shapes, two renderings — a reader can act on the difference."""
    _running_sync(app, "yt-summary")
    sweep_orphaned_workflow_runs(app)
    _failed_sync(app, "gmail")

    entries = {
        e["source"]["routine_key"]: e
        for e in _failed_runs(_model(app).list_entries("default"))
    }
    assert entries["yt-summary"]["source"]["outcome"] == "unknown"
    assert entries["gmail"]["source"]["outcome"] == "failed"
    assert entries["yt-summary"]["why"] != entries["gmail"]["why"]


def test_a_real_failure_after_an_orphan_wins(app) -> None:
    """The latest run decides how the routine reads right now."""
    orphan = _running_sync(app, "yt-summary")
    sweep_orphaned_workflow_runs(app)
    _stamp(app, orphan, "2026-08-14T01:00:00.000Z")
    real = _failed_sync(app, "yt-summary", CAUSES["source_unreachable"])
    _stamp(app, real, "2026-08-14T02:00:00.000Z")

    entry = _failed_runs(_model(app).list_entries("default"))[0]
    assert entry["source"]["outcome"] == "failed"
    assert CAUSES["source_unreachable"] in entry["why"]


# ---- constraint 3: a missing count key must not break the entry --------


def test_the_entry_survives_a_run_row_with_no_counts(app) -> None:
    """``_safe_counts`` drops every key outside its allowlist.

    A run row whose artifacts are empty is normal, not exceptional: a sync
    that failed before it counted anything writes exactly that.
    """
    _failed_sync(app, "yt-summary")
    app.storage.execute(
        "UPDATE workflow_runs SET artifacts = '{}' WHERE routine_key = ?",
        ("yt-summary",),
    )
    entry = _failed_runs(_model(app).list_entries("default"))[0]
    assert entry["why"]
    assert entry["title"]


def test_the_entry_survives_unparseable_artifacts(app) -> None:
    """A malformed JSON blob must not take the whole inbox down."""
    _failed_sync(app, "yt-summary")
    app.storage.execute(
        "UPDATE workflow_runs SET artifacts = 'not json' WHERE routine_key = ?",
        ("yt-summary",),
    )
    entries = _model(app).list_entries("default")
    assert len(_failed_runs(entries)) == 1


def test_the_entry_survives_a_missing_cause(app) -> None:
    """A failed row with a null error still says something useful."""
    _failed_sync(app, "yt-summary")
    app.storage.execute(
        "UPDATE workflow_runs SET error = NULL WHERE routine_key = ?",
        ("yt-summary",),
    )
    entry = _failed_runs(_model(app).list_entries("default"))[0]
    assert entry["why"]


def test_a_failed_run_with_no_routine_key_is_skipped(app) -> None:
    """Without an identity the entry could name nothing and retry nothing."""
    app.storage.execute(
        """INSERT INTO workflow_runs (id, kind, routine_key, workflow_id,
                                      user_id, status, error)
           VALUES ('nameless', 'source_sync', NULL, NULL, 'default',
                   'failed', 'Something went wrong.')"""
    )
    assert _failed_runs(_model(app).list_entries("default")) == []


# ---- actions -----------------------------------------------------------


def test_every_advertised_action_is_one_the_inbox_implements(app) -> None:
    """Package 2's final review found ``unclassifiable`` advertising actions
    no endpoint implemented, which broke inbox zero. Not again.

    The entry is synthesized, so ``accept`` and ``dismiss`` cannot reach it —
    there is no row to update. Only actions with a real exit may appear.
    """
    _failed_sync(app, "yt-summary")
    entry = _failed_runs(_model(app).list_entries("default"))[0]
    ids = [a["id"] for a in entry["actions"]]
    assert ids == ["retry"]
    assert len(entry["actions"]) <= 3      # "never more than three"
    assert "dismiss" not in ids            # nothing would persist it
    assert "accept" not in ids


def test_the_retry_action_targets_the_routine_key(app) -> None:
    """Task 5's route is keyed by routine, so the entry must carry it."""
    _failed_sync(app, "gmail")
    entry = _failed_runs(_model(app).list_entries("default"))[0]
    assert entry["source"]["routine_key"] == "gmail"
    assert entry["id"] == "run:gmail"


def test_the_entry_id_is_stable_across_further_failures(app) -> None:
    """A client holding the id must not lose it when the sync fails again."""
    _failed_sync(app, "gmail")
    first = _failed_runs(_model(app).list_entries("default"))[0]["id"]
    _failed_sync(app, "gmail")
    second = _failed_runs(_model(app).list_entries("default"))[0]["id"]
    assert first == second


# ---- no content leaks ---------------------------------------------------


def test_the_entry_carries_no_note_content_or_traceback(app) -> None:
    """Constitution Rule 1, on the read side.

    The recorder's allowlist keeps content out of the column. This asserts
    the inbox does not re-introduce it by reading something else from the
    row.
    """
    app.knowledge_base.write(
        title="Kontoauszug Sparkasse 4711", content="secret body",
        topic="notes",
    )
    _failed_sync(app, "gmail")
    entry = _failed_runs(_model(app).list_entries("default"))[0]
    blob = f"{entry['title']} {entry['why']} {entry['source']}"
    assert "Kontoauszug" not in blob
    assert "secret body" not in blob
    assert "Traceback" not in blob
    assert ".py" not in blob


def test_an_unrecognised_stored_cause_is_not_rendered_verbatim(app) -> None:
    """A row written outside the recorder must not become a display channel.

    The recorder refuses anything outside :data:`CAUSES`, but a row can also
    arrive from the orphan sweep or from a future writer. The inbox renders
    only causes it recognises, so an arbitrary column value never reaches
    the surface.
    """
    _failed_sync(app, "gmail")
    app.storage.execute(
        "UPDATE workflow_runs SET error = ? WHERE routine_key = ?",
        ("/srv/mycelos/ingest.py line 42: Kontoauszug Sparkasse 4711", "gmail"),
    )
    entry = _failed_runs(_model(app).list_entries("default"))[0]
    assert "Kontoauszug" not in entry["why"]
    assert "Sparkasse" not in entry["why"]
    assert ".py" not in entry["why"]
    assert entry["why"]                    # still says something


# The rendered ``why`` for every cause a ``source_sync`` row can carry, plus
# the two shapes that carry none. Written out in full rather than composed
# from CAUSES, because composing it would reproduce whatever bug the renderer
# has and assert that it is consistent with itself.
_EXPECTED_WHY: dict[str | None, str] = {
    CAUSES["source_failed"]: (
        "The last 'gmail' sync failed. The sync reached the source but it did "
        "not return the data. The server log has the reason this time."
    ),
    CAUSES["source_rejected"]: (
        "The last 'gmail' sync failed. The source rejected the request. Check "
        "that the connector is still authorised, then run the sync again."
    ),
    CAUSES["source_unreachable"]: (
        "The last 'gmail' sync failed. The source could not be reached. It may "
        "be offline or the connector may no longer be running."
    ),
    CAUSES["response_unreadable"]: (
        "The last 'gmail' sync failed. The response from the source could not "
        "be read. The connector returned something this sync does not "
        "understand."
    ),
    # The three that must NOT be appended, each falling to the cause-free
    # line. See `_RENDERABLE_CAUSES` for why each one is excluded.
    CAUSES["unrecognised"]: (
        "The last 'gmail' sync failed. No cause was recorded that is safe to "
        "show here; the server log has the detail."
    ),
    CAUSES["briefing_undeliverable"]: (
        "The last 'gmail' sync failed. No cause was recorded that is safe to "
        "show here; the server log has the detail."
    ),
    CAUSES["briefing_failed"]: (
        "The last 'gmail' sync failed. No cause was recorded that is safe to "
        "show here; the server log has the detail."
    ),
    None: (
        "The last 'gmail' sync failed. No cause was recorded that is safe to "
        "show here; the server log has the detail."
    ),
    ORPHANED_RUN_CAUSE: (
        "The last 'gmail' sync did not finish and its outcome is unknown — it "
        "was still running when the system next started. Run it again to find "
        "out where it stands."
    ),
}


@pytest.mark.parametrize("cause", list(_EXPECTED_WHY))
def test_every_cause_renders_as_one_sentence_a_person_would_write(
    app, cause
) -> None:
    """The whole ``why`` line, pinned per cause — not a substring of it.

    ``_failed_run_why`` composes a cause after "The last 'gmail' sync
    failed.", so a cause that opens with its own "The run failed." produces
    a doubled sentence, and one that names the briefing names a routine
    that is not the one that failed. Both read as a concatenation bug on a
    surface whose only value is being believable.

    Asserting the full string is the point. A substring check on the cause
    passes happily while the sentence in front of it is broken, which is
    how the doubled line shipped in the first place.
    """
    _failed_sync(app, "gmail")
    app.storage.execute(
        "UPDATE workflow_runs SET error = ? WHERE routine_key = 'gmail'",
        (cause,),
    )
    entry = _failed_runs(_model(app).list_entries("default"))[0]
    assert entry["why"] == _EXPECTED_WHY[cause]
    # No cause doubles the opening clause of another.
    assert entry["why"].count("sync failed") <= 1
    assert "The run failed." not in entry["why"]
    assert "briefing" not in entry["why"].lower()


def test_no_cause_is_left_unreviewed(app) -> None:
    """A cause added to the recorder must be judged as a rendered sentence.

    ``_EXPECTED_WHY`` is written out by hand, so a new cause reaches the
    user unreviewed unless something fails. This is that something: it
    fails until the author has looked at the line their cause produces and
    decided whether it belongs in ``_RENDERABLE_CAUSES``.
    """
    unreviewed = set(CAUSES.values()) - set(_EXPECTED_WHY)
    assert not unreviewed, (
        "a new run cause has no pinned rendering — add it to _EXPECTED_WHY "
        "and decide whether it reads as a sentence after \"The last 'x' sync "
        "failed.\""
    )


def test_the_sweeps_cause_is_recognised_not_dropped(app) -> None:
    """The sweep's cause is this package's own text, so it is not dropped.

    An unattributable cause falls back to the "no cause safe to show" line.
    The sweep's is attributable, so the entry states the real situation
    instead — it was still running when the system next started.
    """
    _running_sync(app, "gmail")
    sweep_orphaned_workflow_runs(app)
    entry = _failed_runs(_model(app).list_entries("default"))[0]
    assert "still running when the system next started" in entry["why"]
    assert "no cause was recorded" not in entry["why"].lower()


# ---- the count and Package 2's invariants ------------------------------


def test_a_failed_run_is_in_the_count(app) -> None:
    """Class 2 counts. A dead sync is exactly what the number is for."""
    _failed_sync(app, "yt-summary")
    model = _model(app)
    assert model.count("default") == 1
    assert model.count("default") == len(model.list_entries("default"))


def test_the_count_still_equals_the_number_of_entries(app) -> None:
    """One number, one list — across all four sources of entries."""
    inbox = InboxService(app.storage)
    for i in range(2):
        p = app.knowledge_base.write(title=f"N{i}", content="y", topic="notes")
        inbox.add(p, "merge", {"duplicate_path": f"notes/z-{i}"}, 0.9)
    app.storage.execute(
        "INSERT INTO knowledge_notes (path, title, type, status, due, reminder) "
        "VALUES ('notes/r', 'Call', 'task', 'open', ?, 1)",
        ((date.today() - timedelta(days=1)).isoformat(),),
    )
    _failed_sync(app, "yt-summary")
    _failed_sync(app, "gmail")

    model = _model(app)
    entries = model.list_entries("default")
    assert model.count("default") == len(entries)
    assert len(_failed_runs(entries)) == 2
    assert model.count("default") == 5


def test_uncertain_placements_are_still_out_of_the_count(app) -> None:
    """Package 2's load-bearing invariant, re-checked with a failure present."""
    path = app.knowledge_base.write(title="Filed uncertainly", content="y",
                                    topic="notes")
    app.storage.execute(
        "UPDATE knowledge_notes SET placement_confidence=0.55 WHERE path=?",
        (path,))
    _failed_sync(app, "yt-summary")
    assert _model(app).count("default") == 1


def test_obligations_still_sort_before_a_failed_run(app) -> None:
    """A lapsing commitment outranks a decision that can wait."""
    app.storage.execute(
        "INSERT INTO knowledge_notes (path, title, type, status, due, reminder) "
        "VALUES ('notes/r', 'Call', 'task', 'open', ?, 1)",
        ((date.today() - timedelta(days=1)).isoformat(),),
    )
    _failed_sync(app, "yt-summary")
    entries = _model(app).list_entries("default")
    assert entries[0]["class"] == "obligation"


def test_reading_the_inbox_writes_nothing(app) -> None:
    """The model is read-only: a badge poll must not change state."""
    _failed_sync(app, "yt-summary")
    before = app.storage.fetchall(
        "SELECT id, status, error, updated_at FROM workflow_runs ORDER BY id"
    )
    model = _model(app)
    model.list_entries("default")
    model.count("default")
    after = app.storage.fetchall(
        "SELECT id, status, error, updated_at FROM workflow_runs ORDER BY id"
    )
    assert [dict(r) for r in before] == [dict(r) for r in after]


def test_no_suggestion_row_is_written_for_a_failed_run(app) -> None:
    """Synthesized, not stored — there is no second source of truth."""
    _failed_sync(app, "yt-summary")
    _model(app).list_entries("default")
    rows = app.storage.fetchall("SELECT * FROM organizer_suggestions")
    assert rows == []
