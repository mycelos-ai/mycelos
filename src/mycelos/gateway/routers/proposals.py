"""Resolve approval cards from the chat (Package 8, slice 5).

The card's buttons post the proposal id and one action. The server looks up
what the action does; the request body cannot name a note, a folder or a
source. Refusals are fail-closed and change nothing.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from mycelos.gateway.routers._helpers import resolve_user_id
from mycelos.knowledge.proposals import ProposalError, ProposalService

router = APIRouter()


@router.post("/api/proposals/{proposal_id}/resolve")
async def resolve_proposal(proposal_id: str, request: Request) -> Any:
    try:
        body = await request.json()
    except Exception:
        body = None
    action = body.get("action") if isinstance(body, dict) else None
    if not isinstance(action, str) or not action:
        return JSONResponse({"error": "invalid_action"}, status_code=400)
    service = ProposalService(request.app.state.mycelos, resolve_user_id(request))
    try:
        return service.resolve(proposal_id, action)
    except ProposalError as exc:
        return JSONResponse({"error": exc.code}, status_code=exc.status)
