"""UI theme endpoints — user-customizable appearance.

A theme is a named preset (CSS variable set in shared/base.css) plus an
optional accent color. Persisted in the system memory scope so it follows
the user across devices; every change is audited (Constitution Rule 1).
Content state only — no declarative tables, no config generation (Rule 2).
"""
from __future__ import annotations

import re
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from mycelos.gateway.routers._helpers import resolve_user_id

router = APIRouter()

# Presets must match the [data-theme="..."] blocks in shared/base.css.
THEME_PRESETS = ("mycelium-dark", "mycelium-light", "graphite-dark")
DEFAULT_PRESET = "mycelium-dark"

_ACCENT_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
_MEMORY_KEY = "ui_theme"


def _load_theme(app: Any, user_id: str) -> dict:
    stored = None
    try:
        stored = app.memory.get(user_id, "system", _MEMORY_KEY)
    except Exception:
        pass
    theme = stored if isinstance(stored, dict) else {}
    preset = theme.get("preset")
    accent = theme.get("accent")
    return {
        "preset": preset if preset in THEME_PRESETS else DEFAULT_PRESET,
        "accent": accent if isinstance(accent, str) and _ACCENT_RE.match(accent) else None,
        "presets": list(THEME_PRESETS),
    }


@router.get("/api/ui/theme")
async def get_theme(request: Request) -> dict[str, Any]:
    mycelos = request.app.state.mycelos
    return _load_theme(mycelos, resolve_user_id(request))


@router.post("/api/ui/theme")
async def set_theme(request: Request) -> dict[str, Any]:
    mycelos = request.app.state.mycelos
    user_id = resolve_user_id(request)
    body = await request.json()

    current = _load_theme(mycelos, user_id)
    preset = current["preset"]
    accent = current["accent"]

    if "preset" in body:
        if body["preset"] not in THEME_PRESETS:
            return JSONResponse(
                {"error": f"Unknown preset: {body['preset']}",
                 "presets": list(THEME_PRESETS)},
                status_code=422,
            )
        preset = body["preset"]

    if "accent" in body:
        value = body["accent"]
        if value is None:
            accent = None
        elif isinstance(value, str) and _ACCENT_RE.match(value):
            accent = value.lower()
        else:
            return JSONResponse(
                {"error": "Accent must be a #rrggbb hex color or null"},
                status_code=422,
            )

    mycelos.memory.set(user_id, "system", _MEMORY_KEY,
                       {"preset": preset, "accent": accent})
    mycelos.audit.log(
        "ui.theme.updated",
        user_id=user_id,
        details={"preset": preset, "accent": accent},
    )
    return {"preset": preset, "accent": accent, "presets": list(THEME_PRESETS)}
