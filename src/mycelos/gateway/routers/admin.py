"""Admin, notifications, reminders endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

router = APIRouter()


@router.get("/api/admin/doctor")
async def admin_doctor(request: Request) -> list[dict[str, Any]]:
    """Run the read-only health-check suite and return structured results.

    This is the web-UI equivalent of ``mycelos doctor`` without --fix or
    --why — no state mutation, no LLM, no subprocess execution. The
    server-reachability check is skipped because we *are* the server.
    """
    from mycelos.doctor.checks import run_health_checks
    mycelos = request.app.state.mycelos
    return run_health_checks(mycelos, gateway_url=None)


@router.post("/api/admin/inbox/dismiss")
async def admin_inbox_dismiss(request: Request) -> Any:
    """Mark a reminder as handled without firing it.

    Body: ``{"path": "tasks/..."}``. Used when the user clicks an
    inbox entry — we stamp ``reminder_fired_at = now`` so both the
    bell and the scheduler stop showing it, and emit a
    ``reminder.dismissed`` audit event so history can distinguish
    user-dismissed from scheduler-fired.
    """
    from mycelos.knowledge.reminder import ReminderService
    body = await request.json()
    path = (body or {}).get("path")
    if not path:
        return JSONResponse({"error": "path is required"}, status_code=422)
    mycelos = request.app.state.mycelos
    ok = ReminderService(mycelos).mark_dismissed(path, trigger="user")
    if not ok:
        return JSONResponse({"error": "not found or already dismissed"}, status_code=404)
    return {"status": "dismissed", "path": path}


@router.get("/api/admin/inbox")
async def admin_inbox(request: Request) -> dict[str, Any]:
    """Aggregate "needs attention" items for the header bell dropdown.

    Returns three lists:

    * ``reminders``: active knowledge-base tasks with ``reminder=1``
    * ``waiting_workflows``: workflow runs in ``waiting_input``
    * ``failed_workflows``: workflow runs that failed in the last 24h

    Plus a ``total`` convenience counter so the bell badge knows
    whether to show the red indicator. Purely read-only — no state
    mutation, safe to poll.
    """
    mycelos = request.app.state.mycelos

    from mycelos.knowledge.reminder import ReminderService
    reminders = ReminderService(mycelos).get_due_reminders_now()[:20]

    waiting_rows = mycelos.storage.fetchall(
        """SELECT wr.id, wr.workflow_id, wr.status, wr.clarification,
                  wr.updated_at, w.name AS workflow_name
           FROM workflow_runs wr
           LEFT JOIN workflows w ON wr.workflow_id = w.id
           WHERE wr.status = 'waiting_input'
           ORDER BY wr.updated_at DESC
           LIMIT 20""",
    )
    waiting_workflows = [dict(r) for r in waiting_rows]

    failed_rows = mycelos.storage.fetchall(
        """SELECT wr.id, wr.workflow_id, wr.status, wr.error,
                  wr.updated_at, w.name AS workflow_name
           FROM workflow_runs wr
           LEFT JOIN workflows w ON wr.workflow_id = w.id
           WHERE wr.status = 'failed'
             AND wr.updated_at >= datetime('now', '-1 day')
           ORDER BY wr.updated_at DESC
           LIMIT 20""",
    )
    failed_workflows = [dict(r) for r in failed_rows]

    return {
        "reminders": reminders,
        "waiting_workflows": waiting_workflows,
        "failed_workflows": failed_workflows,
        "total": len(reminders) + len(waiting_workflows) + len(failed_workflows),
    }


@router.get("/api/notifications/pending")
async def notifications_pending(request: Request) -> dict[str, Any]:
    """Return and clear any pending in-browser notifications.

    The Reminder service writes reminders to memory under
    system/pending_reminder. The chat page polls this endpoint every
    ~20s; when something is there we return it and delete it in the
    same call so it's delivered exactly once.
    """
    mycelos = request.app.state.mycelos
    try:
        msg = mycelos.memory.get("default", "system", "pending_reminder")
    except Exception:
        msg = None
    if not msg:
        return {"reminder": None}
    try:
        mycelos.memory.delete("default", "system", "pending_reminder")
    except Exception:
        pass
    return {"reminder": msg}


@router.get("/api/reminders/upcoming")
async def reminders_upcoming(request: Request, limit: int = 10) -> list[dict[str, Any]]:
    """Active reminder-notes with a due date, earliest first.

    Used by the sidebar "Reminders" block. Notes without a due date
    are excluded (they can't be scheduled). Done/cancelled notes are
    excluded via status filter.
    """
    rows = request.app.state.mycelos.storage.fetchall(
        """SELECT path, title, due, remind_via
           FROM knowledge_notes
           WHERE reminder = 1
             AND status = 'active'
             AND due IS NOT NULL
           ORDER BY due ASC
           LIMIT ?""",
        (limit,),
    )
    return [dict(r) for r in rows]
