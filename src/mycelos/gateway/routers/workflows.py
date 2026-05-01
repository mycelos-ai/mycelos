"""Workflows endpoints — list workflows and inspect runs."""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

router = APIRouter()


@router.get("/api/workflows")
async def list_workflows(request: Request) -> list[dict[str, Any]]:
    """List all workflows."""
    mycelos = request.app.state.mycelos
    return mycelos.workflow_registry.list_workflows()


@router.get("/api/workflows/{workflow_id}/runs")
async def list_workflow_runs(request: Request, workflow_id: str) -> list[dict[str, Any]]:
    """List recent runs for a specific workflow."""
    mycelos = request.app.state.mycelos
    return mycelos.workflow_run_manager.list_runs(workflow_id=workflow_id, limit=20)


@router.get("/api/workflow-runs/scheduled")
async def list_scheduled_workflow_runs(request: Request) -> list[dict[str, Any]]:
    """List active scheduled cron-triggered workflows for the sidebar."""
    mycelos = request.app.state.mycelos
    return mycelos.workflow_run_manager.list_scheduled()


@router.get("/api/workflow-runs/{run_id}")
async def get_workflow_run(request: Request, run_id: str) -> Any:
    """Get a single workflow run with full details including parsed conversation."""
    mycelos = request.app.state.mycelos
    run = mycelos.workflow_run_manager.get(run_id)
    if not run:
        return JSONResponse({"error": "Run not found"}, status_code=404)
    # Parse conversation JSON for the detail view
    if run.get("conversation") and isinstance(run["conversation"], str):
        try:
            run["conversation"] = json.loads(run["conversation"])
        except (json.JSONDecodeError, TypeError):
            run["conversation"] = []
    # Sum tokens from conversation usage metadata if present
    total_tokens = 0
    for msg in (run.get("conversation") or []):
        usage = msg.get("usage") or {}
        total_tokens += usage.get("total_tokens", 0)
    run["total_tokens"] = total_tokens or None
    return run


@router.get("/api/workflow-runs")
async def list_all_workflow_runs(
    request: Request,
    limit: int = 50,
    status: str | None = None,
) -> list[dict[str, Any]]:
    """List workflow runs across all workflows.

    Args:
        limit: Max rows (capped at 100).
        status: "active" returns running+paused+waiting_input runs with
            workflow_name joined (sidebar). Any other value is passed
            through as exact match. None returns all.
    """
    mycelos = request.app.state.mycelos
    if status == "active":
        rows = mycelos.storage.fetchall(
            """SELECT wr.*, w.name as workflow_name
               FROM workflow_runs wr
               LEFT JOIN workflows w ON wr.workflow_id = w.id
               WHERE wr.status IN ('running', 'paused', 'waiting_input')
               ORDER BY wr.updated_at DESC
               LIMIT ?""",
            (min(limit, 100),),
        )
        return [dict(r) for r in rows]
    return mycelos.workflow_run_manager.list_runs(
        status=status, limit=min(limit, 100)
    )
