"""Morning briefing endpoints — today's briefing and its settings.

Settings live in memory scope (content state, not declarative config):
``briefing_enabled``, ``briefing_time``, ``auto_ingest_enabled``. Every
settings change is audited (Constitution Rule 1); no config generation
is required (Rule 2 covers declarative tables only).
"""

from __future__ import annotations

import re
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from mycelos.gateway.routers._helpers import resolve_user_id

router = APIRouter()

_TIME_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")


@router.get("/api/briefing/today")
async def briefing_today(request: Request) -> dict[str, Any]:
    """Return today's briefing — cached for the day, else freshly built."""
    from mycelos.knowledge.briefing import get_or_build_briefing

    mycelos = request.app.state.mycelos
    return get_or_build_briefing(mycelos, resolve_user_id(request))


@router.get("/api/briefing/settings")
async def briefing_get_settings(request: Request) -> dict[str, Any]:
    from mycelos.knowledge.briefing import DEFAULT_BRIEFING_TIME

    mycelos = request.app.state.mycelos
    user_id = resolve_user_id(request)
    return {
        "enabled": bool(mycelos.memory.get(user_id, "system", "briefing_enabled")),
        "time": mycelos.memory.get(user_id, "system", "briefing_time")
        or DEFAULT_BRIEFING_TIME,
        "auto_ingest_enabled": bool(
            mycelos.memory.get(user_id, "system", "auto_ingest_enabled")
        ),
        "last_sent": mycelos.memory.get(user_id, "system", "briefing_last_sent"),
    }


@router.post("/api/briefing/settings")
async def briefing_set_settings(request: Request) -> Any:
    """Update briefing/auto-ingest settings. Audited."""
    mycelos = request.app.state.mycelos
    user_id = resolve_user_id(request)
    try:
        body = await request.json()
    except Exception:
        body = {}

    changes: dict[str, Any] = {}
    if "time" in body:
        time_str = str(body["time"])
        if not _TIME_RE.match(time_str):
            return JSONResponse(
                {"error": f"Invalid time '{time_str}' — expected HH:MM (24h)"},
                status_code=422,
            )
        changes["briefing_time"] = time_str
    if "enabled" in body:
        changes["briefing_enabled"] = bool(body["enabled"])
    if "auto_ingest_enabled" in body:
        changes["auto_ingest_enabled"] = bool(body["auto_ingest_enabled"])

    if not changes:
        return JSONResponse(
            {"error": "No settings provided — accepted keys: enabled, "
                      "time, auto_ingest_enabled"},
            status_code=422,
        )

    for key, value in changes.items():
        mycelos.memory.set(user_id, "system", key, value, created_by="user")

    try:
        mycelos.audit.log(
            "briefing.settings.updated", user_id=user_id, details=changes,
        )
    except Exception:
        pass

    return {"updated": sorted(changes)}
