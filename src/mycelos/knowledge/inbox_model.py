"""Unified inbox read model — one list of what needs a human.

Reads three kinds of state and shapes them into one ordered list:

* ``organizer_suggestions`` with ``status='pending'``, filtered through
  :func:`~mycelos.knowledge.inbox_policy.needs_human` (Class 2).
* Notes the organizer gave up on (``organizer_state='manual'``), which
  own no suggestion row (Class 2).
* Due reminders and overdue tasks (Class 3).

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
_UNCLASSIFIABLE_ACTIONS: list[dict[str, str]] = [
    {"id": "open", "label": "File it myself"},
    {"id": "dismiss", "label": "Leave it"},
]

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
        """Every Class 2 and Class 3 entry, collapsed and ordered."""
        entries: list[dict[str, Any]] = []
        entries.extend(self._suggestion_entries())
        entries.extend(self._unclassifiable_entries())

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

    # NOTE on failed source runs (the plan's fourth source):
    # Nothing durable records a *run*. `auto_ingest_check` collects errors
    # into a return value and one audit event; the ingest functions return
    # an "error" key. The only durable failure state is
    # `connectors.last_error_at`, which is per connector and overwritten by
    # the next call — it answers "is this connector failing now?", not "did
    # this run fail?", and it cannot be resolved or dismissed as an inbox
    # entry. Doctor already surfaces it (doctor/checks.check_connectors).
    # Synthesising an inbox entry from it would produce a sticky entry
    # nobody can clear, which is the failure mode this redesign exists to
    # remove. Package 3 (Routines) lands run history; the failed_run entry
    # belongs there. The `failed_run` kind stays in INBOX_KINDS so the
    # policy is ready for it.

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
