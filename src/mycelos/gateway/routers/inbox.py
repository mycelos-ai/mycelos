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

Every handler is a plain ``def``. They touch SQLite only, which is
synchronous; ``async def`` would block the event loop without buying
concurrency, which a prior review already caught once in this project.

Spec: docs/superpowers/specs/2026-W33-inbox-design.md
"""
from __future__ import annotations

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
