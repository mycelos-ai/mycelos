"""Config + i18n endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from mycelos.gateway.routers._helpers import RollbackRequest, resolve_user_id

router = APIRouter()


@router.get("/api/config")
async def config(request: Request) -> dict[str, Any]:
    """Return current config state snapshot."""
    mycelos = request.app.state.mycelos
    return mycelos.state_manager.snapshot()


@router.get("/api/i18n")
async def i18n(request: Request) -> dict[str, Any]:
    """Return web UI translations for the active language."""
    from mycelos.i18n import get_language, get_web_translations

    lang = get_language()
    translations = get_web_translations(lang)
    return {"lang": lang, "translations": translations}


@router.post("/api/config/rollback")
async def config_rollback(request: Request, body: RollbackRequest) -> dict[str, Any]:
    """Rollback to a specific config generation."""
    mycelos = request.app.state.mycelos
    try:
        new_gen = mycelos.config.rollback(
            to_generation=body.generation_id,
            state_manager=mycelos.state_manager,
        )
        mycelos.audit.log("config.rollback", details={
            "target_generation": body.generation_id,
            "active_generation": new_gen,
        }, user_id=resolve_user_id(request))
        return {"status": "rolled_back", "active_generation": new_gen}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=400)


@router.get("/api/config/generations")
async def config_generations(request: Request) -> dict[str, Any]:
    """List config generations with active marker."""
    mycelos = request.app.state.mycelos
    generations = mycelos.storage.fetchall(
        "SELECT id, description, trigger, created_at FROM config_generations ORDER BY id DESC LIMIT 50"
    )
    active_id = None
    active_row = mycelos.storage.fetchone("SELECT generation_id FROM active_generation")
    if active_row:
        active_id = active_row["generation_id"]
    return {
        "active_id": active_id,
        "generations": [
            {**dict(g), "is_active": g["id"] == active_id}
            for g in generations
        ],
    }
