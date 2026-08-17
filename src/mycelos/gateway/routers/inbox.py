"""Inbox endpoints — what needs a human, and the placement review view.

Two surfaces, deliberately separate:

* ``/api/inbox`` and ``/api/inbox/count`` — Class 2 (decisions with
  consequences) and Class 3 (the user's own obligations). The count is
  the number on the home surface, so it must never include optimization
  noise.
* ``/api/inbox/placements`` — notes the organizer filed below its
  silent-apply floor. Reviewing them is an opportunity, not a debt: they
  are never in the inbox and never in the count.

Resolving a suggestion entry is NOT done here. The entry carries the
suggestion id (``"suggestion:<id>"``) and the existing
``/api/organizer/suggestions/{id}/accept`` and ``.../dismiss`` routes
apply it, with the fail-closed handling they already have.

Two kinds are exceptions, and both own a retry route here because they
own no suggestion row:

* ``unclassifiable`` — ``POST /api/inbox/notes/{path}/retry`` hands the
  note back to the organizer queue.
* ``failed_run`` — ``POST /api/inbox/runs/{routine_key}/retry`` runs the
  source sync again.

Every entry the inbox shows must have a working exit, or inbox zero stops
being reachable.

Every handler is a plain ``def``. They touch SQLite only, which is
synchronous; ``async def`` would block the event loop without buying
concurrency, which a prior review already caught once in this project.

Spec: docs/superpowers/specs/2026-W33-inbox-design.md
"""
from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from mycelos.gateway.routers._helpers import resolve_user_id
from mycelos.knowledge.inbox_model import InboxModel, list_uncertain_placements

logger = logging.getLogger("mycelos.gateway")

router = APIRouter()

# Upper bound for the review view. A caller-supplied limit is clamped
# rather than trusted: this endpoint is reachable from the browser and an
# unbounded LIMIT is a cheap way to make the process read the whole table.
_MAX_PLACEMENT_LIMIT = 500


def _model(request: Request) -> InboxModel:
    mycelos = request.app.state.mycelos
    return InboxModel(mycelos.storage, app=mycelos)


def _is_safe_note_path(path: str) -> bool:
    """Whether a caller-supplied note path may be used at all.

    This route is ``{path:path}``, so the handler receives the raw
    remainder of the URL — Starlette hands over whatever is left after
    percent-decoding, including ``..`` segments and leading slashes.

    Nothing here touches the filesystem (the confirm handler only runs an
    exact-match UPDATE), so a traversal string could not escape a
    directory even if it got through. The check exists anyway for two
    reasons: the path is written to an audit payload, and a later change
    that does touch the disk must not silently inherit an unchecked path.
    Validate at the boundary, once.

    Rejected: empty paths, absolute paths, any ``..`` segment, any
    backslash (a Windows separator that ``..\\`` traversal rides on) and
    NUL bytes.
    """
    if not path or path.startswith("/") or path.startswith("\\"):
        return False
    if "\\" in path or "\x00" in path:
        return False
    return not any(segment == ".." for segment in path.split("/"))


@router.get("/api/inbox")
def inbox_list(request: Request) -> dict[str, Any]:
    """Everything that needs a human, obligations first."""
    user_id = resolve_user_id(request)
    return {"entries": _model(request).list_entries(user_id)}


@router.get("/api/inbox/count")
def inbox_count(request: Request) -> dict[str, int]:
    """The one number on the home surface.

    It is the length of the same list ``/api/inbox`` returns, never a
    separate query — a count that disagrees with the list is worse than
    no count.
    """
    user_id = resolve_user_id(request)
    return {"count": _model(request).count(user_id)}


@router.get("/api/inbox/placements")
def inbox_placements(request: Request, limit: int = 50) -> dict[str, Any]:
    """Notes filed below the silent-apply floor, shakiest first."""
    mycelos = request.app.state.mycelos
    user_id = resolve_user_id(request)
    safe_limit = max(1, min(int(limit), _MAX_PLACEMENT_LIMIT))
    return {
        "placements": list_uncertain_placements(
            mycelos.storage, user_id, limit=safe_limit
        )
    }


@router.post("/api/inbox/placements/{path:path}/confirm")
def inbox_confirm_placement(path: str, request: Request) -> Any:
    """Confirm an uncertain placement: the note is where it belongs.

    Clearing ``placement_confidence`` removes the note from the review
    view. It is idempotent — confirming a note that carries no marker is
    a 200, because the note exists and the post-state is what the caller
    asked for. Only an unknown note is a 404.
    """
    if not _is_safe_note_path(path):
        # Fail closed and say nothing about why. No row is touched.
        logger.warning("inbox: rejected unsafe placement path")
        return JSONResponse({"error": "invalid path"}, status_code=400)

    mycelos = request.app.state.mycelos
    row = mycelos.storage.fetchone(
        "SELECT path FROM knowledge_notes WHERE path=?", (path,)
    )
    if not row:
        return JSONResponse({"error": "not_found", "path": path}, status_code=404)

    mycelos.storage.execute(
        "UPDATE knowledge_notes SET placement_confidence=NULL WHERE path=?",
        (path,),
    )
    try:
        mycelos.audit.log(
            "knowledge.placement_confirmed",
            user_id=resolve_user_id(request),
            # Path only. The title and the note body never enter an audit
            # payload (Constitution Rule 1).
            details={"path": path},
        )
    except Exception:
        # Audit must never break the write path.
        pass
    return {"ok": True, "path": path}


@router.post("/api/inbox/notes/{path:path}/retry")
def inbox_retry_note(path: str, request: Request) -> Any:
    """Return an unclassifiable note to the organizer queue.

    An ``unclassifiable`` entry owns no suggestion row — the handler parks
    the note at ``organizer_state='manual'`` and stops — so the
    accept/dismiss routes cannot reach it. This is its resolve action.

    It covers both ways a user clears one: "I filed it myself, look at it
    again" and "the provider was down, try again". Resetting the state to
    ``'pending'`` and the attempt counter to 0 puts the note back in front
    of the next organizer run, which is the only path that ends in a
    filed note. Archiving — destroying the knowledge — was the previous
    only exit and is not a resolution.

    Fail closed (Constitution Rule 3): the note is reported resolved only
    when the UPDATE actually changed a row. An unknown path is a 404 and
    a failed write is a 500; neither claims success.
    """
    if not _is_safe_note_path(path):
        # Fail closed and say nothing about why. No row is touched.
        logger.warning("inbox: rejected unsafe note path")
        return JSONResponse({"error": "invalid path"}, status_code=400)

    mycelos = request.app.state.mycelos
    row = mycelos.storage.fetchone(
        "SELECT organizer_state FROM knowledge_notes WHERE path=?", (path,)
    )
    if not row:
        return JSONResponse({"error": "not_found", "path": path}, status_code=404)

    try:
        cursor = mycelos.storage.execute(
            "UPDATE knowledge_notes SET organizer_state='pending', "
            "organizer_attempts=0 WHERE path=?",
            (path,),
        )
        changed = int(getattr(cursor, "rowcount", 0) or 0)
    except Exception:
        logger.warning("inbox: retry update failed", exc_info=True)
        changed = 0

    if changed < 1:
        # The state did not change, so the entry is NOT resolved. Say so
        # rather than returning a 200 the badge will contradict.
        return JSONResponse({"error": "retry failed"}, status_code=500)

    try:
        mycelos.audit.log(
            "knowledge.note_requeued",
            user_id=resolve_user_id(request),
            # Path and the previous state only. The title and the note
            # body never enter an audit payload (Constitution Rule 1).
            details={"path": path, "previous_state": row.get("organizer_state")},
        )
    except Exception:
        # Audit must never break the write path.
        pass
    return {"ok": True, "path": path, "organizer_state": "pending"}


@router.post("/api/inbox/runs/{routine_key}/retry")
def inbox_retry_run(routine_key: str, request: Request) -> Any:
    """Run a failed source sync again. The ``failed_run`` entry's exit.

    **The run row is the point, not the ingest.** A ``failed_run`` entry
    is synthesized from ``workflow_runs`` — it has no stored row, so
    nothing can mark it resolved and nothing needs to. It disappears when
    the routine's next *completed* run row proves the sync works again.

    That makes the obvious implementation wrong.
    ``POST /api/knowledge/ingest/{source}`` already dispatches the same
    ingest functions, but it calls them directly and records nothing. A
    retry built on it would import the data, return 200, and leave the
    entry and the badge standing for good — the sticky entry Package 2's
    final review had to remove from ``unclassifiable``. So dispatch is
    wrapped in :class:`~mycelos.scheduler.run_recorder.RunRecorder`, the
    same recorder the hourly tick uses, and a retry is an ordinary run of
    the routine that happens to have been asked for by a human.

    The three outcomes match ``auto_ingest_check`` exactly, because a
    disagreement between the two would mean the same failure cleared the
    entry here and kept it there:

    * the ingest returns counts → ``completed``, 200;
    * the ingest returns ``{"error": ...}`` → ``failed``, 502;
    * the ingest raises → ``failed``, 502, with a cause from the type.

    Fail closed (Constitution Rule 3): only the first is ``ok: true``. A
    failed retry answers 502 and its own run row keeps the entry alive
    with an incremented failure count.

    A fourth outcome sits outside that table, because the source is not the
    thing that failed: the ingest works but the row cannot be closed. The
    entry then stays — ``last_ok`` anchors on ``completed``, and a row left
    ``running`` is not that — so the answer is 500, not ``ok: true``. The
    row-is-the-deliverable rule applies to the close as it does to the open.
    """
    # The allowlist runs first, before storage, dispatch, the recorder and
    # the audit payload — `routine_key` arrives from a URL path, and the
    # recorder documents itself as safe on the assumption that its only
    # writer is this hardcoded dict. Membership is the whole check: a key
    # is retryable because it is a known ingest source, never because it
    # failed to look dangerous. Traversal, SQL metacharacters, NUL bytes
    # and an oversized string are all simply not in the dict.
    from mycelos.knowledge.connector_ingest import INGEST_SOURCES

    ingest_fn = INGEST_SOURCES.get(routine_key)
    if ingest_fn is None:
        # No echo of the key. It is attacker-controlled, and this response
        # is rendered by a browser.
        logger.warning("inbox: retry rejected an unknown routine key")
        return JSONResponse(
            {"error": "not_found", "available": sorted(INGEST_SOURCES)},
            status_code=404,
        )

    # Past this line `routine_key` is a literal from INGEST_SOURCES, not
    # caller text.
    #
    # `_ingest_failure_cause` is imported across a module boundary despite
    # its underscore, deliberately. It maps an exception type to a fixed
    # cause, and the scheduler and this handler must map the *same*
    # exception to the *same* cause: they write rows the same read model
    # renders, and a second copy would drift the first time one side
    # learned a new exception type. The alternative — a private copy here
    # — trades a naming smell for a correctness bug. Promoting it to a
    # public name belongs in the module that owns it, not in this commit.
    from mycelos.scheduler.jobs import _ingest_failure_cause
    from mycelos.scheduler.run_recorder import CAUSES, RunRecorder

    mycelos = request.app.state.mycelos
    user_id = resolve_user_id(request)
    recorder = RunRecorder(mycelos.storage)

    try:
        run_id: str | None = recorder.start("source_sync", routine_key, user_id)
    except Exception:
        # Unlike the scheduler, a retry that cannot be recorded must not
        # run. There the work is the user's data arriving and the row is
        # observability; here the row IS the deliverable, because it is
        # the only thing that can clear the entry. Running the sync
        # anyway would leave the user clicking a button that works and
        # changes nothing visible.
        logger.warning("inbox: could not open a run row for a retry", exc_info=True)
        return JSONResponse({"ok": False, "error": "retry failed"}, status_code=500)

    counts: dict[str, Any] = {}
    cause: str | None = None
    try:
        result = ingest_fn(mycelos, user_id=user_id)
        if isinstance(result, dict) and result.get("error"):
            # The connector answered and said no. Its error string stays
            # out of the row and out of this response: it is built from
            # the data that failed.
            cause = CAUSES["source_failed"]
        else:
            counts = result if isinstance(result, dict) else {}
    except BaseException as e:
        # BaseException for the same reason auto_ingest_check uses it: an
        # interrupt ends the run as finally as a RuntimeError, and used to
        # leave the row 'running' forever.
        logger.error("inbox: retry of a source sync failed", exc_info=True)
        cause = _ingest_failure_cause(e)
        if not isinstance(e, Exception):
            _close_run(recorder, run_id, cause=cause)
            raise

    closed = _close_run(
        recorder, run_id,
        counts=None if cause else {**counts, "source": routine_key},
        cause=cause,
    )
    # The counts this handler reports are read back from the row the
    # recorder just wrote, never taken from the connector's return value.
    # The recorder's allowlist is then the single filter for the row, the
    # audit payload and the response body — three surfaces that would
    # otherwise each need their own, and drift.
    safe_counts = _recorded_counts(mycelos.storage, run_id)

    try:
        mycelos.audit.log(
            "knowledge.run_retried",
            user_id=user_id,
            # The routine key, the outcome and the filtered counts. A
            # connector's own fields — a subject, a sender, an error
            # string — never enter an audit payload (Rule 1).
            details={
                "routine_key": routine_key,
                "ok": cause is None,
                "counts": safe_counts,
            },
        )
    except Exception:
        # Audit must never break the write path.
        pass

    if cause:
        # Fail closed: the entry stays, and the run row this retry just
        # wrote is what keeps it there. The cause is one of the recorder's
        # fixed strings, so it is safe to render.
        return JSONResponse(
            {"ok": False, "routine_key": routine_key, "error": cause},
            status_code=502,
        )
    if not closed:
        # The sync worked, but the row it had to close did not. This handler
        # says the row is the deliverable, and that argument holds at the
        # close exactly as it holds at the open: a row left 'running' is not
        # a 'completed' run, so the inbox entry stays and the user is left
        # clicking a button that answers 200 and changes nothing visible.
        # The entry surviving is correct (Rule 3) — only the response was
        # wrong. 500, not 502: the source answered, our storage did not.
        return JSONResponse(
            {
                "ok": False,
                "routine_key": routine_key,
                "error": (
                    "The sync ran, but its result could not be recorded, so "
                    "this entry stays until the next run succeeds."
                ),
            },
            status_code=500,
        )
    return {"ok": True, "routine_key": routine_key, "counts": safe_counts}


def _recorded_counts(storage: Any, run_id: str | None) -> dict[str, Any]:
    """What the run row actually stored, or ``{}``.

    Reading the row back rather than reusing the connector's dict means
    the recorder's allowlist is the only filter in this path. A connector
    that grows a field carrying a subject line cannot reach the audit
    payload or the response body through a second, laxer copy.
    """
    if run_id is None:
        return {}
    try:
        row = storage.fetchone(
            "SELECT artifacts FROM workflow_runs WHERE id = ?", (run_id,)
        )
        parsed = json.loads((row or {}).get("artifacts") or "{}")
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _close_run(
    recorder: Any,
    run_id: str | None,
    counts: dict[str, Any] | None = None,
    cause: str | None = None,
) -> bool:
    """Close a retry's run row. Never raises. Says whether it worked.

    A storage error here loses the record of a sync that already ran. The
    consequence is a stale entry, not lost data, and raising would turn a
    successful import into a 500 the user reads as "it did not work".

    It still must not be reported as success. The caller applies the handler's
    own rule — the row is the deliverable, because it is the only thing that
    can clear the entry — to the close as well as to the open.

    Returns:
        True when the row was closed, False when the storage error was
        swallowed. ``run_id`` of None returns False: there is no row.
    """
    if run_id is None:
        return False
    try:
        if cause is None:
            recorder.finish(run_id, counts or {})
        else:
            recorder.fail(run_id, cause)
        return True
    except Exception:
        logger.warning("inbox: could not close a retry's run row", exc_info=True)
        return False
