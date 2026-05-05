"""Cost endpoint."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Request

router = APIRouter()


@router.get("/api/cost")
async def get_cost(request: Request, period: str = "today") -> dict[str, Any]:
    """Token usage aggregated by period (today, week, month, all)."""
    mycelos = request.app.state.mycelos
    now = datetime.now(timezone.utc)
    if period == "today":
        since = now.replace(hour=0, minute=0, second=0, microsecond=0)
    elif period == "week":
        since = now - timedelta(days=7)
    elif period == "month":
        since = now - timedelta(days=30)
    else:
        since = datetime(2020, 1, 1, tzinfo=timezone.utc)

    rows = mycelos.storage.fetchall(
        "SELECT model, SUM(input_tokens) as input_tokens, SUM(output_tokens) as output_tokens, "
        "SUM(total_tokens) as total_tokens, SUM(cost) as total_cost, COUNT(*) as calls "
        "FROM llm_usage WHERE created_at >= ? GROUP BY model ORDER BY total_cost DESC",
        (since.isoformat(),),
    )
    return {
        "period": period,
        "since": since.isoformat(),
        "models": [dict(r) for r in rows],
        "total_cost": sum(r["total_cost"] or 0 for r in rows),
        "total_tokens": sum(r["total_tokens"] or 0 for r in rows),
    }
