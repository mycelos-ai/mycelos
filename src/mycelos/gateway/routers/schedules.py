"""Schedules endpoint."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

router = APIRouter()


@router.get("/api/schedules")
async def list_schedules(request: Request) -> list[dict[str, Any]]:
    """List all scheduled tasks."""
    mycelos = request.app.state.mycelos
    rows = mycelos.storage.fetchall(
        "SELECT id, workflow_id, schedule, status, last_run, next_run, run_count, budget_per_run, created_at "
        "FROM scheduled_tasks ORDER BY status, next_run"
    )
    return [dict(r) for r in rows]
