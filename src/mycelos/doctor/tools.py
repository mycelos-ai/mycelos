"""Doctor diagnostic tools — read-only queries for debugging."""

from __future__ import annotations

import json
from datetime import date
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from mycelos.app import App


def doctor_query_audit(
    app: "App",
    event_type: str | None = None,
    limit: int = 20,
    since: str | None = None,
) -> list[dict]:
    """Query audit events with optional filters."""
    conditions = []
    params: list = []
    if event_type:
        conditions.append("event_type LIKE ?")
        params.append(f"%{event_type}%")
    if since:
        conditions.append("created_at >= ?")
        params.append(since)
    where = f" WHERE {' AND '.join(conditions)}" if conditions else ""
    rows = app.storage.fetchall(
        f"SELECT event_type, user_id, details, created_at FROM audit_events{where} ORDER BY created_at DESC LIMIT ?",
        tuple(params + [limit]),
    )
    return [dict(r) for r in rows]


def doctor_query_db(app: "App", sql: str) -> list[dict]:
    """Execute a read-only SQL query. Only SELECT allowed."""
    sql_stripped = sql.strip().upper()
    if not sql_stripped.startswith("SELECT"):
        return [{"error": "Only SELECT queries allowed"}]
    try:
        rows = app.storage.fetchall(sql)
        return [dict(r) for r in rows]
    except Exception as e:
        return [{"error": str(e)}]


def doctor_config_history(app: "App", limit: int = 10) -> list[dict]:
    """List recent config generations."""
    rows = app.storage.fetchall(
        "SELECT id, description, trigger, created_at FROM config_generations ORDER BY id DESC LIMIT ?",
        (limit,),
    )
    # Mark active generation
    active = app.storage.fetchone("SELECT generation_id FROM active_generation WHERE id = 1")
    active_id = active["generation_id"] if active else None
    result = []
    for r in rows:
        d = dict(r)
        d["active"] = d["id"] == active_id
        result.append(d)
    return result


def doctor_check_reminders(app: "App") -> dict[str, Any]:
    """Check for overdue/due reminders."""
    today = date.today().isoformat()
    rows = app.storage.fetchall(
        """SELECT path, title, due, reminder, remind_via, status
           FROM knowledge_notes
           WHERE reminder = 1 AND status IN ('open', 'in-progress', 'active')
             AND due IS NOT NULL AND due <= ?
           ORDER BY due""",
        (today,),
    )
    tasks = [dict(r) for r in rows]

    # Check last reminder.sent event
    last_sent = app.storage.fetchone(
        "SELECT details, created_at FROM audit_events WHERE event_type = 'reminder.sent' ORDER BY created_at DESC"
    )

    return {
        "due_count": len(tasks),
        "tasks": tasks,
        "last_sent": dict(last_sent) if last_sent else None,
    }


def doctor_check_schedules(app: "App") -> dict[str, Any]:
    """Check scheduled tasks and their last runs."""
    rows = app.storage.fetchall(
        """SELECT id, workflow_id, schedule, status, last_run, next_run, run_count
           FROM scheduled_tasks ORDER BY status, next_run"""
    )
    schedules = [dict(r) for r in rows]

    # Check for missed runs
    today = date.today().isoformat()
    missed = [s for s in schedules if s.get("next_run") and s["next_run"] < today and s["status"] == "active"]

    return {
        "schedules": schedules,
        "total": len(schedules),
        "active": sum(1 for s in schedules if s["status"] == "active"),
        "missed": len(missed),
        "missed_details": missed,
    }


def doctor_check_credentials(app: "App") -> dict[str, Any]:
    """Check which credentials are configured (never returns values!)."""
    rows = app.storage.fetchall(
        "SELECT DISTINCT service, label FROM credentials ORDER BY service"
    )
    services = [{"service": r["service"], "label": r["label"]} for r in rows]
    return {
        "services": services,
        "count": len(services),
    }


def doctor_check_telegram(app: "App") -> dict[str, Any]:
    """Check Telegram configuration."""
    import json

    channel = app.storage.fetchone(
        "SELECT id, status, config, allowed_users FROM channels WHERE id = 'telegram'"
    )
    chat_id = None
    try:
        chat_id = app.memory.get("default", "system", "telegram_chat_id")
    except Exception:
        pass

    has_token = False
    try:
        cred = app.credentials.get_credential("telegram")
        has_token = bool(cred and cred.get("api_key"))
    except Exception:
        pass

    # Parse allowlist
    allowed_users: list[int] = []
    if channel:
        try:
            allowed_users = json.loads(channel["allowed_users"] or "[]")
        except (json.JSONDecodeError, TypeError):
            pass

    return {
        "configured": channel is not None,
        "status": channel["status"] if channel else "not configured",
        "has_token": has_token,
        "has_chat_id": bool(chat_id),
        "chat_id": chat_id,
        "allowed_users": allowed_users,
        "allowlist_empty": channel is not None and len(allowed_users) == 0,
    }
