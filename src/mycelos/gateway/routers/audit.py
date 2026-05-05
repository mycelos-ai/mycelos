"""Audit log endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

router = APIRouter()


@router.get("/api/audit/activity")
async def audit_activity(
    request: Request,
    level: str = "noteworthy",
    since: str | None = "24h",
    limit: int = 100,
) -> dict[str, Any]:
    """Recent audit events classified for the Doctor Activity panel.

    level:
        "suspicious"  — only security-relevant events (tamper, blocks, denies, …)
        "noteworthy"  — everything except high-volume noise (default)
        "all"         — raw feed, includes reminder.tick etc.

    since: shorthand like 30m, 1h, 24h, 7d (default 24h). `None` or "all"
    disables the time filter.

    Returns {events: [...], counts: {suspicious, noteworthy, all}}.
    The counts let the UI render tab badges without a second roundtrip.
    """
    import json as _json
    from datetime import datetime, timedelta, timezone
    import re as _re

    mycelos = request.app.state.mycelos
    limit = max(1, min(limit, 500))

    cutoff: str | None = None
    if since and since != "all":
        match = _re.match(r"^(\d+)([smhd])$", since.strip().lower())
        if not match:
            return JSONResponse(
                {"error": "since must look like 30m, 1h, 24h, 7d or 'all'"},
                status_code=400,
            )
        amount = int(match.group(1))
        unit = match.group(2)
        delta = {
            "s": timedelta(seconds=amount),
            "m": timedelta(minutes=amount),
            "h": timedelta(hours=amount),
            "d": timedelta(days=amount),
        }[unit]
        cutoff = (datetime.now(timezone.utc) - delta).strftime("%Y-%m-%dT%H:%M:%fZ")

    # Fetch a larger window and classify in Python — the event_type list is
    # small and this keeps the SQL simple.
    from mycelos.audit_patterns import (
        NOISY_EVENT_TYPES,
        SUSPICIOUS_EVENT_SUFFIXES,
        SUSPICIOUS_EVENT_TYPES,
        is_noisy,
        is_suspicious,
    )

    if cutoff:
        rows = mycelos.storage.fetchall(
            "SELECT * FROM audit_events WHERE created_at >= ? ORDER BY created_at DESC LIMIT ?",
            (cutoff, 2000),
        )
    else:
        rows = mycelos.storage.fetchall(
            "SELECT * FROM audit_events ORDER BY created_at DESC LIMIT ?",
            (2000,),
        )

    events_all: list[dict[str, Any]] = []
    count_suspicious = 0
    count_noteworthy = 0
    for r in rows:
        event = dict(r)
        if event.get("details") and isinstance(event["details"], str):
            try:
                event["details"] = _json.loads(event["details"])
            except Exception:
                pass
        etype = event["event_type"]
        event["suspicious"] = is_suspicious(etype)
        event["noisy"] = is_noisy(etype)
        if event["suspicious"]:
            count_suspicious += 1
        if not event["noisy"]:
            count_noteworthy += 1
        events_all.append(event)

    if level == "suspicious":
        filtered = [e for e in events_all if e["suspicious"]]
    elif level == "all":
        filtered = events_all
    else:  # "noteworthy" — default
        filtered = [e for e in events_all if not e["noisy"]]

    return {
        "events": filtered[:limit],
        "counts": {
            "suspicious": count_suspicious,
            "noteworthy": count_noteworthy,
            "all": len(events_all),
        },
        "level": level,
        "since": since,
    }


@router.get("/api/audit")
async def audit_events(
    request: Request,
    limit: int = 10,
    event_type: str | None = None,
) -> list[dict[str, Any]]:
    """Return recent audit events, newest first."""
    import json as _json
    mycelos = request.app.state.mycelos
    limit = min(limit, 100)

    if event_type:
        rows = mycelos.storage.fetchall(
            "SELECT * FROM audit_events WHERE event_type LIKE ? ORDER BY created_at DESC LIMIT ?",
            (event_type + "%", limit),
        )
    else:
        rows = mycelos.storage.fetchall(
            "SELECT * FROM audit_events ORDER BY created_at DESC LIMIT ?",
            (limit,),
        )

    result = []
    for row in rows:
        entry = dict(row)
        if entry.get("details") and isinstance(entry["details"], str):
            try:
                entry["details"] = _json.loads(entry["details"])
            except (ValueError, TypeError):
                pass
        result.append(entry)
    return result
