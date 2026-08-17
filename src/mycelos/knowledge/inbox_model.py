"""Unified inbox read model — one list of what needs a human.

Reads four kinds of state and shapes them into one ordered list:

* ``organizer_suggestions`` with ``status='pending'``, filtered through
  :func:`~mycelos.knowledge.inbox_policy.needs_human` (Class 2).
* Notes the organizer gave up on (``organizer_state='manual'``), which
  own no suggestion row (Class 2).
* Source syncs that have failed since they last succeeded, read from
  ``workflow_runs`` (Class 2).
* Due reminders and overdue tasks (Class 3).

Three of the four are **synthesized** rather than stored, so they resolve
by the underlying state changing: a filed note leaves ``manual``, a
working sync writes a completed run. Nothing here can become an entry
whose dismissal has nowhere to persist.

This module is **read-only**. It never writes, never marks anything seen
and never resolves. The resolve dispatch lives in the API layer, so a
read of the inbox has no side effects and can be called from a badge
poll without changing state.

Every function is a plain ``def``. The whole module touches SQLite only,
which is synchronous; an ``async def`` here would block the event loop
without buying concurrency.

Spec: docs/superpowers/specs/2026-W33-inbox-design.md
"""
from __future__ import annotations

import json
import logging
from datetime import date
from typing import Any

from mycelos.knowledge.inbox_policy import collapse_key, needs_human
# One edge from `knowledge` into `scheduler`, and it lands on a leaf:
# `run_recorder` imports only `protocols` and `workflows.run_cause`, so this
# does not pull the scheduler's job surface — every connector, the briefing,
# the reminder tick — into the read model. Importing the constants is the
# alternative to copying their text, which would rot silently the first time
# a cause was reworded on one side only.
from mycelos.scheduler.run_recorder import CAUSES, ORPHANED_RUN_CAUSE
from mycelos.storage.database import SQLiteStorage

logger = logging.getLogger("mycelos.inbox_model")

# Class of an entry. A consequence is a decision the system will not make
# on its own; an obligation is a commitment of the user's that lapses.
CLASS_CONSEQUENCE = "consequence"
CLASS_OBLIGATION = "obligation"

# The states a task must be in to still be owed. 'done', 'archived' and
# 'cancelled' are settled and never overdue.
_OPEN_TASK_STATES = ("open", "in-progress", "active")

# Actions per kind. Never more than three (spec: "The one or two actions
# that resolve it. Never more than three."). Consequence entries resolve
# through the existing accept/dismiss routes; obligations do not have an
# accept, because there is nothing to agree with.
_SUGGESTION_ACTIONS: list[dict[str, str]] = [
    {"id": "accept", "label": "Accept"},
    {"id": "dismiss", "label": "Dismiss"},
]
_OBLIGATION_ACTIONS: list[dict[str, str]] = [
    {"id": "done", "label": "Done"},
    {"id": "snooze", "label": "Snooze"},
    {"id": "open", "label": "Open"},
]
# An unclassifiable note owns no suggestion row, so the accept/dismiss
# routes cannot reach it. Its resolve action is 'retry', which hands the
# note back to the organizer queue via
# ``POST /api/inbox/notes/{path}/retry``. That is the exit for both
# readings of the entry: "I filed it myself, look again" and "the
# provider was down, try again". 'open' is client-side navigation to the
# note, not a resolve — the same role it has for an obligation.
_UNCLASSIFIABLE_ACTIONS: list[dict[str, str]] = [
    {"id": "retry", "label": "File it myself / try again"},
    {"id": "open", "label": "Open"},
]
# A failed run is synthesized from run rows, so accept and dismiss cannot
# reach it: there is no row to update, and a dismissal would have nowhere
# to persist. Offering one would produce the sticky entry nobody can clear
# — the exact defect Package 2's final review found in `unclassifiable`.
# The entry has one real exit, running the routine again, and one implicit
# one: it disappears by itself when the routine next succeeds.
_FAILED_RUN_ACTIONS: list[dict[str, str]] = [
    {"id": "retry", "label": "Run it again"},
]

# The one run kind that becomes an inbox entry. Read `_failed_run_rows`
# for why the filter is on this column and on nothing else.
_FAILED_RUN_KIND = "source_sync"

# How a failed run reads to a human.
_OUTCOME_FAILED = "failed"
_OUTCOME_UNKNOWN = "unknown"

# The only cause strings this surface will show, and it is deliberately
# narrower than "every fixed cause this package authored".
#
# `_failed_run_why` renders a cause *after* "The last 'x' sync failed.", so a
# cause only belongs here if the pair reads as something a person would write.
# Three of the recorder's seven do not:
#
# * ``unrecognised`` opens "The run failed.", which produces "The last 'gmail'
#   sync failed. The run failed. No cause was recorded…" — a doubled sentence
#   that reads like a concatenation bug on a surface whose whole value is
#   being believable. Dropping it falls to the cause-free line, which carries
#   the same information once.
# * ``briefing_undeliverable`` and ``briefing_failed`` name the briefing. Only
#   ``kind='source_sync'`` rows reach this read model, so on an entry titled
#   "The 'gmail' sync failed" they would name a routine that is not the one
#   that failed. The scheduler writes them only to ``kind='briefing'`` rows
#   today, but this allowlist exists precisely for rows the scheduler did not
#   write.
#
# The orphan sweep's cause is not here either, and does not need to be: an
# orphaned run takes the `_OUTCOME_UNKNOWN` branch, which composes its own
# sentence and never appends a stored cause. It stays in the set only so
# `_run_outcome` can recognise it by value.
_RENDERABLE_CAUSES = frozenset({
    CAUSES["source_failed"],
    CAUSES["source_rejected"],
    CAUSES["source_unreachable"],
    CAUSES["response_unreadable"],
})

# Kinds whose confidence must not be rendered. A merge is never automatic
# at any confidence, so showing a number would imply a decision the value
# does not make (spec line 85). A scope violation is a deterministic
# rejection, not a judgement call, and its stored confidence is 0.0.
_NO_CONFIDENCE_KINDS = frozenset({"merge", "scope_violation"})


def _why(kind: str, payload: dict[str, Any], title: str) -> str:
    """One line of plain language: why is this entry here?

    Returned to the user's own UI. It must never be logged or written to
    an audit payload — it can carry note titles.
    """
    if kind == "merge":
        other = payload.get("duplicate_path") or "another note"
        return (
            f"This looks like a duplicate of {other}. Merging archives the "
            "other note, so it is never done without you."
        )
    if kind == "scope_violation":
        return (
            "The classifier proposed a folder outside this source's "
            "permitted area. The proposal was rejected; the note is "
            "waiting for a folder you choose."
        )
    if kind == "new_topic_confirm":
        name = payload.get("name") or "a new area"
        parent = payload.get("parent") or "the source folder"
        return (
            f"Opening '{name}' under {parent} adds a new main category. "
            "Cheap to accept, expensive to undo once notes collect in it."
        )
    if kind == "new_topic":
        name = payload.get("name") or "a new topic"
        return f"No existing folder fits. The organizer proposes '{name}'."
    if kind == "unclassifiable":
        return (
            "The organizer tried repeatedly and found no folder for this "
            "note. Without you it stays unfiled."
        )
    if kind == "reminder":
        return "You asked to be reminded about this, and it is due."
    if kind == "overdue_task":
        return "This task is past its due date."
    # Fail open on the text as the policy fails open on the kind: an
    # unknown entry is shown with a neutral line rather than an empty one.
    return f"'{title}' needs a decision from you."


def _run_outcome(stored_cause: str | None) -> str:
    """Whether the routine is broken, or its outcome is merely unknown.

    The orphan sweep stamps *every* row still marked ``running`` when the
    gateway starts. It cannot tell a crashed run from one still executing
    in an old process, and a redeploy during an hourly sync is an ordinary
    event rather than a rare one. Rendering that as "the sync failed"
    sends the reader hunting for a broken connector when the gateway
    simply restarted under it — a wrong cause is worse than no cause.

    So the sweep's own cause is recognised by value and reported as
    unknown. Everything else the row can carry is a cause a writer in this
    package chose deliberately, and means the routine really did fail.
    """
    if (stored_cause or "").strip() == ORPHANED_RUN_CAUSE:
        return _OUTCOME_UNKNOWN
    return _OUTCOME_FAILED


def _renderable_cause(stored_cause: str | None) -> str | None:
    """The stored cause, but only when this package wrote it.

    :meth:`~mycelos.scheduler.run_recorder.RunRecorder._safe_cause` is an
    allowlist on the write side, and this is the matching one on the read
    side. The two are deliberately not the same guarantee: the column is
    also written by :func:`sweep_orphaned_workflow_runs` and by the
    workflow run manager, whose ``error`` text follows a different rule,
    and a future writer could put anything there.

    The inbox is a rendering surface, so it renders only strings it can
    attribute. Anything else is dropped and the entry falls back to a
    cause-free line. Honest and vague beats readable and leaking.

    It is also narrower than "attributable": a cause this package wrote is
    still dropped when it does not read as a sentence in this entry's frame.
    See :data:`_RENDERABLE_CAUSES` for which three, and why.
    """
    text = (stored_cause or "").strip()
    return text if text in _RENDERABLE_CAUSES else None


def _failed_run_title(routine_key: str, outcome: str) -> str:
    """One line, no jargon: which routine, and what is true about it."""
    if outcome == _OUTCOME_UNKNOWN:
        return f"The '{routine_key}' sync did not finish"
    return f"The '{routine_key}' sync failed"


def _failed_run_why(
    routine_key: str, outcome: str, stored_cause: str | None
) -> str:
    """Why this entry is here, in the user's language.

    Returned to the user's own UI. The routine key is a source name the
    user connected themselves, never note content — but this text must
    still never be logged or written to an audit payload, for the same
    reason as :func:`_why`.
    """
    cause = _renderable_cause(stored_cause)
    if outcome == _OUTCOME_UNKNOWN:
        return (
            f"The last '{routine_key}' sync did not finish and its outcome "
            "is unknown — it was still running when the system next "
            "started. Run it again to find out where it stands."
        )
    if cause:
        return f"The last '{routine_key}' sync failed. {cause}"
    return (
        f"The last '{routine_key}' sync failed. No cause was recorded that "
        "is safe to show here; the server log has the detail."
    )


def _confidence_for(kind: str, value: float | None) -> float | None:
    """The confidence to render, or None when a number would mislead."""
    if kind in _NO_CONFIDENCE_KINDS:
        return None
    return value


def _actions_for(kind: str) -> list[dict[str, str]]:
    if kind in ("reminder", "overdue_task"):
        return list(_OBLIGATION_ACTIONS)
    if kind == "unclassifiable":
        return list(_UNCLASSIFIABLE_ACTIONS)
    if kind == "failed_run":
        return list(_FAILED_RUN_ACTIONS)
    return list(_SUGGESTION_ACTIONS)


def _suggestion_kind(row_kind: str, payload: dict[str, Any]) -> str:
    """The kind to present, which is not always the kind that was stored.

    **Read-side shim, for old rows only.** The handler writes a scope
    violation as ``kind='scope_violation'`` today, and that row returns
    from here unchanged — the security-adjacent path does not depend on a
    payload heuristic.

    Rows already in a live database are the reason this function stays.
    Before the kind existed, the rejection was written as ``kind='move'``
    with a fallback target and nothing else, which at the storage level is
    indistinguishable from a legacy low-confidence move except by its
    payload: a legacy move carries ``reason='low_confidence'`` and an
    ``alternatives`` list, a rejection carries only ``target``.

    For those rows, read the payload, not the kind. A wrong guess either
    hides a real consequence or resurrects the 150-entry landfill, so both
    legacy markers must be absent before a move row is promoted.

    The table is deliberately not migrated: see the Week 33 CHANGELOG.
    """
    if row_kind != "move":
        return row_kind
    if payload.get("reason") == "low_confidence" or "alternatives" in payload:
        return "move"          # legacy optimization row — stays hidden
    if payload.get("target"):
        return "scope_violation"
    return "move"


def _source_of(note_source: str | None) -> dict[str, Any]:
    """Provenance for the entry: where it arrived from, and in which run.

    ``knowledge_notes.source`` is free-form provenance JSON written by the
    ingest. A malformed value must not take the whole inbox down.
    """
    if not note_source:
        return {}
    try:
        parsed = json.loads(note_source)
    except (json.JSONDecodeError, TypeError):
        return {}
    if not isinstance(parsed, dict):
        return {}
    return parsed


def list_uncertain_placements(
    storage: SQLiteStorage,
    user_id: str,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Notes that were filed below the silent-apply floor, shakiest first.

    This is the review view's data. It is deliberately NOT part of the
    inbox and never part of the count: reviewing these is an opportunity,
    not a debt. Ordering ascending puts the least trustworthy placement
    at the top, which is the only order that makes a partial review
    worthwhile.

    ``user_id`` is accepted but not used in a WHERE clause: the knowledge
    base is single-tenant (``knowledge_notes`` has no owner column) and
    every knowledge route behaves the same way. The parameter is here for
    interface symmetry and for future scoping — it does not filter today.
    """
    try:
        rows = storage.fetchall(
            """SELECT path, title, parent_path, placement_confidence,
                      source, created_at
                 FROM knowledge_notes
                WHERE placement_confidence IS NOT NULL
                  AND status != 'archived'
             ORDER BY placement_confidence ASC, created_at DESC
                LIMIT ?""",
            (int(limit),),
        )
    except Exception:
        logger.warning("uncertain placement query failed", exc_info=True)
        return []

    return [
        {
            "path": row["path"],
            "title": row["title"],
            "parent": row["parent_path"],
            "confidence": row["placement_confidence"],
            "source": _source_of(row.get("source")),
            "created_at": row.get("created_at"),
        }
        for row in rows
    ]


class InboxModel:
    """Read model over everything that needs a human.

    ``app`` is optional so the model can be built from storage alone in
    tests and in contexts where no App is wired; the reminder service is
    only constructed when an App is present.
    """

    def __init__(self, storage: SQLiteStorage, app: Any = None) -> None:
        self._storage = storage
        self._app = app

    # -- public API -------------------------------------------------------

    def list_entries(self, user_id: str) -> list[dict[str, Any]]:
        """Every Class 2 and Class 3 entry, collapsed and ordered.

        ``user_id`` is accepted but not used in a WHERE clause: the
        knowledge base is single-tenant (``knowledge_notes`` has no owner
        column). The parameter is here for interface symmetry and for
        future scoping — it does not filter today.
        """
        entries: list[dict[str, Any]] = []
        entries.extend(self._suggestion_entries())
        entries.extend(self._unclassifiable_entries())
        entries.extend(self._failed_run_entries())

        # Obligations are added whole. De-duplication happens only inside
        # Class 3 (a due reminder is also an overdue task, and one
        # commitment is one entry) — never across classes. A note can
        # carry a pending suggestion and a due reminder at the same time:
        # the suggestion is the system's guess, the reminder is the
        # user's own instruction. Dropping the second because the first
        # shares a path would make a commitment disappear while the count
        # stays constant.
        entries.extend(self._obligation_entries())

        collapsed = self._collapse(entries)
        # Obligations first — a lapsing commitment outranks a decision that
        # can wait. Within a class, oldest first: the thing that has been
        # waiting longest is the thing most likely to be forgotten.
        collapsed.sort(
            key=lambda e: (
                0 if e["class"] == CLASS_OBLIGATION else 1,
                e.get("created_at") or "",
            )
        )
        return collapsed

    def count(self, user_id: str) -> int:
        """The one number on the home surface.

        It is defined as the length of :meth:`list_entries` rather than a
        separate ``COUNT(*)`` on purpose: two queries drift, and a count
        that disagrees with the list is worse than no count. Collapsing
        means a bulk import counts as one, which is the point.

        ``user_id`` is accepted but not used in a WHERE clause: the
        knowledge base is single-tenant (``knowledge_notes`` has no owner
        column). The parameter is here for interface symmetry and for
        future scoping — it does not filter today.
        """
        return len(self.list_entries(user_id))

    # -- sources ----------------------------------------------------------

    def _suggestion_entries(self) -> list[dict[str, Any]]:
        """Pending organizer suggestions that need a human."""
        try:
            rows = self._storage.fetchall(
                """SELECT s.id, s.note_path, s.kind, s.payload, s.confidence,
                          s.created_at, n.title AS note_title, n.source AS note_source
                     FROM organizer_suggestions s
                     LEFT JOIN knowledge_notes n ON s.note_path = n.path
                    WHERE s.status = 'pending'
                 ORDER BY s.created_at ASC"""
            )
        except Exception:
            logger.warning("suggestion query failed", exc_info=True)
            return []

        entries: list[dict[str, Any]] = []
        for row in rows:
            try:
                payload = json.loads(row["payload"])
            except (json.JSONDecodeError, TypeError):
                payload = {}
            if not isinstance(payload, dict):
                payload = {}

            kind = _suggestion_kind(row["kind"], payload)
            if not needs_human(kind):
                continue

            title = row.get("note_title") or row["note_path"]
            source = _source_of(row.get("note_source"))
            source["path"] = row["note_path"]
            entries.append(self._entry(
                entry_id=f"suggestion:{row['id']}",
                kind=kind,
                entry_class=CLASS_CONSEQUENCE,
                title=title,
                why=_why(kind, payload, title),
                confidence=_confidence_for(kind, row.get("confidence")),
                actions=_actions_for(kind),
                source=source,
                created_at=row.get("created_at"),
            ))
        return entries

    def _unclassifiable_entries(self) -> list[dict[str, Any]]:
        """Notes parked as 'manual' after the organizer's retry cap.

        These own no suggestion row — the handler updates the note and
        stops. Without this source they would be invisible forever, which
        is exactly the failure the inbox exists to prevent.
        """
        try:
            rows = self._storage.fetchall(
                """SELECT path, title, source, created_at
                     FROM knowledge_notes
                    WHERE organizer_state = 'manual'
                      AND status != 'archived'
                 ORDER BY created_at ASC"""
            )
        except Exception:
            logger.warning("unclassifiable query failed", exc_info=True)
            return []

        entries: list[dict[str, Any]] = []
        for row in rows:
            source = _source_of(row.get("source"))
            source["path"] = row["path"]
            title = row["title"] or row["path"]
            entries.append(self._entry(
                entry_id=f"note:{row['path']}",
                kind="unclassifiable",
                entry_class=CLASS_CONSEQUENCE,
                title=title,
                why=_why("unclassifiable", {}, title),
                confidence=None,
                actions=_actions_for("unclassifiable"),
                source=source,
                created_at=row.get("created_at"),
            ))
        return entries

    def _obligation_entries(self) -> list[dict[str, Any]]:
        """Due reminders and overdue tasks — Class 3.

        Reminders come from the existing service so the inbox and the
        scheduler agree on what "ripe right now" means, including the
        ``remind_at`` precision and the ``reminder_fired_at`` guard.
        """
        entries: list[dict[str, Any]] = []
        reminder_paths: set[str] = set()

        for row in self._due_reminders():
            path = row.get("path")
            if not path:
                continue
            reminder_paths.add(path)
            title = row.get("title") or path
            entries.append(self._entry(
                entry_id=f"reminder:{path}",
                kind="reminder",
                entry_class=CLASS_OBLIGATION,
                title=title,
                why=_why("reminder", {}, title),
                confidence=None,     # an obligation is not a guess
                actions=_actions_for("reminder"),
                source={"path": path, "due": row.get("due"),
                        "remind_at": row.get("remind_at")},
                created_at=row.get("remind_at") or row.get("due"),
            ))

        for row in self._overdue_tasks():
            path = row["path"]
            if path in reminder_paths:
                continue          # already listed as the reminder it is
            title = row["title"] or path
            source = _source_of(row.get("source"))
            source["path"] = path
            source["due"] = row.get("due")
            entries.append(self._entry(
                entry_id=f"task:{path}",
                kind="overdue_task",
                entry_class=CLASS_OBLIGATION,
                title=title,
                why=_why("overdue_task", {}, title),
                confidence=None,
                actions=_actions_for("overdue_task"),
                source=source,
                created_at=row.get("due"),
            ))
        return entries

    def _due_reminders(self) -> list[dict[str, Any]]:
        """Ripe reminders, via the existing service when an App is wired."""
        if self._app is None:
            return []
        try:
            from mycelos.knowledge.reminder import ReminderService
            return ReminderService(self._app).get_due_reminders_now()
        except Exception:
            logger.warning("due reminder lookup failed", exc_info=True)
            return []

    def _overdue_tasks(self) -> list[dict[str, Any]]:
        try:
            placeholders = ", ".join("?" for _ in _OPEN_TASK_STATES)
            return self._storage.fetchall(
                f"""SELECT path, title, due, source
                      FROM knowledge_notes
                     WHERE type = 'task'
                       AND status IN ({placeholders})
                       AND due IS NOT NULL
                       AND due <= ?
                  ORDER BY due ASC, priority DESC""",
                (*_OPEN_TASK_STATES, date.today().isoformat()),
            )
        except Exception:
            logger.warning("overdue task query failed", exc_info=True)
            return []

    def _failed_run_entries(self) -> list[dict[str, Any]]:
        """Source syncs that are not working right now — Class 2.

        Synthesized from ``workflow_runs``, never stored. Three properties
        follow from that and all three are the point:

        * There is one source of truth. A stored row would have to be kept
          in step with the run history that produced it, and the two would
          drift the first time a sync succeeded while the gateway was down.
        * The entry resolves implicitly. It disappears when the routine
          next succeeds, so there is no dismiss row to go stale and no way
          to reach the sticky entry nobody can clear.
        * ``InboxService._KINDS`` needs no new value, so nothing can write
          a ``failed_run`` suggestion that this reader does not produce.

        Only ``kind='source_sync'`` is read. **The filter is on ``kind``,
        never on a null ``workflow_id`` and never on ``routine_key``
        alone.** An ad-hoc workflow run — one built in code and never
        registered — also carries ``workflow_id=NULL`` with its identity in
        ``routine_key``, and the two can collide on the same key. Folding a
        workflow failure into a source's entry would offer a retry that
        dispatches an ingest for a workflow.

        The other three kinds stay out on purpose. A workflow and a
        scheduled task have their own surfaces and their own retry path; a
        briefing retries itself at the next tick and has no route to offer.
        An entry whose action nothing implements is the defect that broke
        inbox zero once already.
        """
        rows = self._failed_run_rows()
        entries: list[dict[str, Any]] = []
        for row in rows:
            routine_key = row.get("routine_key")
            if not routine_key:
                # Without an identity the entry could name no routine and
                # retry nothing. A row like this is a writer bug, not an
                # entry.
                continue
            outcome = _run_outcome(row.get("error"))
            title = _failed_run_title(routine_key, outcome)
            entries.append(self._entry(
                entry_id=f"run:{routine_key}",
                kind="failed_run",
                entry_class=CLASS_CONSEQUENCE,
                title=title,
                why=_failed_run_why(routine_key, outcome, row.get("error")),
                confidence=None,        # a failure is a fact, not a guess
                actions=_actions_for("failed_run"),
                source={
                    "kind": _FAILED_RUN_KIND,
                    "routine_key": routine_key,
                    "run_id": row.get("id"),
                    "outcome": outcome,
                    "failure_count": int(row.get("failure_count") or 1),
                    "last_failure_at": row.get("last_failure_at"),
                },
                # When the problem started, not when it last recurred.
                # list_entries sorts consequence entries oldest first, on
                # the reasoning that the thing waiting longest is the thing
                # most likely to be forgotten. Using the latest failure
                # would invert exactly that: a sync broken since Monday
                # would sink below one that broke an hour ago, and sink
                # again every hour it failed. The provenance still carries
                # the latest failure, for a surface that wants to show it.
                created_at=row.get("first_failure_at"),
            ))
        return entries

    def _failed_run_rows(self) -> list[dict[str, Any]]:
        """One row per source sync that has failed since it last succeeded.

        **A success clears the entry; nothing else does.** The anchor is
        the routine's last ``completed`` run, and every failure after it
        counts. The obvious alternative — look at the latest run and show
        an entry when it is ``failed`` — fails closed in the wrong
        direction twice:

        * A retry in flight makes the latest run ``running``, so the entry
          would disappear while the sync is still unproven. If that retry
          then crashed and left the row ``running`` forever, the entry
          would never come back and the dead sync would be invisible
          again. Constitution Rule 3: a failed retry never resolves an
          entry, and neither does an unfinished one.
        * A run that is still going is not evidence of anything. Only a
          completed one is.

        The volume rule also lives here. An hourly sync failing all day is
        one entry with a failure count, not 24 entries — a consequence
        list that grows by the clock stops being read, and the reader
        learns nothing from the 24th copy that the first did not tell
        them. Grouping is by ``routine_key``, *within* the kind filter,
        which is what makes that filter load-bearing rather than
        decorative.

        This is not Package 2 collapsing. ``collapse_key`` returns None
        for every consequence kind, so two failing sources stay two
        entries and ``collapsed_count`` stays 1. A repeat count on one
        routine's own entry is a different claim from "several entries
        were folded into a summary that hides them".

        One row per routine, carrying ``id`` and ``error`` from its
        **latest** failure — so the rendered cause is the one that is true
        now — alongside ``first_failure_at`` (when the problem started),
        ``last_failure_at`` and ``failure_count``.
        """
        try:
            return self._storage.fetchall(
                """
                WITH runs AS (
                    SELECT id, routine_key, status, error, created_at
                      FROM workflow_runs
                     WHERE kind = ?
                       AND routine_key IS NOT NULL
                ),
                last_ok AS (
                    SELECT routine_key, MAX(created_at) AS at
                      FROM runs
                     WHERE status = ?
                  GROUP BY routine_key
                ),
                open_failures AS (
                    SELECT f.id, f.routine_key, f.error, f.created_at
                      FROM runs f
                 LEFT JOIN last_ok o ON o.routine_key = f.routine_key
                     WHERE f.status = ?
                       AND (o.at IS NULL OR f.created_at > o.at)
                ),
                -- Two different questions, so two aggregates: when the
                -- problem started (what the inbox sorts on) and how many
                -- times it has happened since.
                spans AS (
                    SELECT routine_key,
                           MIN(created_at) AS first_failure_at,
                           MAX(created_at) AS last_failure_at,
                           COUNT(*) AS failure_count
                      FROM open_failures
                  GROUP BY routine_key
                )
                -- The latest failure is joined explicitly rather than read
                -- as a bare column beside MAX(). SQLite only defines the
                -- bare-column rule for a query with a *single* min/max
                -- aggregate, and this one has two — `id` and `error` would
                -- then come from an unspecified row, which is how a stale
                -- cause reaches the user. An explicit join says what it
                -- means and survives a port to another engine.
                --
                -- `id` breaks a tie on identical timestamps, so the result
                -- is one row per routine even when two failures share a
                -- millisecond.
                SELECT f.id, s.routine_key, f.error,
                       s.first_failure_at, s.last_failure_at,
                       s.failure_count
                  FROM spans s
                  JOIN open_failures f
                    ON f.routine_key = s.routine_key
                   AND f.created_at = s.last_failure_at
                   AND f.id = (SELECT MAX(x.id) FROM open_failures x
                                WHERE x.routine_key = s.routine_key
                                  AND x.created_at = s.last_failure_at)
              ORDER BY s.first_failure_at ASC
                """,
                (_FAILED_RUN_KIND, "completed", "failed"),
            )
        except Exception:
            logger.warning("failed run query failed", exc_info=True)
            return []

    # -- shaping ----------------------------------------------------------

    @staticmethod
    def _entry(
        *,
        entry_id: str,
        kind: str,
        entry_class: str,
        title: str,
        why: str,
        confidence: float | None,
        actions: list[dict[str, str]],
        source: dict[str, Any],
        created_at: str | None,
    ) -> dict[str, Any]:
        """One inbox entry. Every key in the shape is always present, so a
        consumer never has to guess whether a missing key means absent or
        unknown."""
        return {
            "id": entry_id,
            "kind": kind,
            "class": entry_class,
            "title": title,
            "why": why,
            "confidence": confidence,
            "actions": actions,
            "source": source,
            "created_at": created_at,
            "collapsed_count": 1,
        }

    def _collapse(self, entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Fold collapsible entries of the same run/source/kind into one.

        The key comes from :func:`collapse_key` and is opaque: entries are
        grouped by equality and the key is never parsed. Consequence kinds
        return ``None`` there and therefore always stand alone — ten
        merges stay ten irreversible decisions.

        The first entry of a group survives and carries the count, so the
        group keeps a real id and a real timestamp instead of a synthetic
        one that resolves to nothing.

        The run id has exactly one home: the note provenance. An entry
        never carries a top-level one, so there is a single field to
        write when the ingest starts recording runs.
        """
        result: list[dict[str, Any]] = []
        groups: dict[str, dict[str, Any]] = {}

        for entry in entries:
            source = entry.get("source") or {}
            key = collapse_key({
                "kind": entry["kind"],
                "run_id": source.get("run_id"),
                "source": source.get("source") or source.get("connector"),
            })
            if key is None:
                result.append(entry)
                continue
            existing = groups.get(key)
            if existing is None:
                groups[key] = entry
                result.append(entry)
            else:
                existing["collapsed_count"] += 1
        return result
