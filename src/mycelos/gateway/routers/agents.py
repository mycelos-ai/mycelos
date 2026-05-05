"""Agents endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

router = APIRouter()


# Single source of truth: an agent is "conversational" iff the user
# can actually chat with it. Internal handlers are excluded. Both
# /api/agents (tagging) and /api/agents/conversational (listing) use
# this set so the admin page, sidebar, and chat picker cannot drift.
_INTERNAL_HANDLERS = frozenset({
    "mycelos", "builder", "workflow-agent",
    "evaluator-agent", "auditor-agent",
})


def _is_conversational(agent: dict[str, Any]) -> bool:
    return (
        agent.get("status") == "active"
        and bool(agent.get("user_facing"))
        and agent.get("id") not in _INTERNAL_HANDLERS
    )


@router.get("/api/agents")
async def list_agents(request: Request) -> list[dict[str, Any]]:
    """List all agents with status, capabilities, type.

    Each entry is tagged with ``conversational`` so the admin page can
    decide whether to show a "Chat with" button without duplicating
    the conversational-agent rules.
    """
    mycelos = request.app.state.mycelos
    agents = mycelos.agent_registry.list_agents()
    for agent in agents:
        agent["conversational"] = _is_conversational(agent)
    return agents


@router.get("/api/agents/conversational")
async def list_conversational_agents(request: Request) -> list[dict[str, Any]]:
    """List agents the user can actually chat with.

    Used by the sidebar and the chat agent picker.
    """
    mycelos = request.app.state.mycelos
    agents = mycelos.agent_registry.list_agents()
    return [a for a in agents if _is_conversational(a)]


@router.patch("/api/agents/{agent_id}")
async def update_agent(agent_id: str, body: dict[str, Any], request: Request) -> dict[str, Any]:
    """Edit an agent: display_name (always safe) or persona (advanced).

    Body fields (all optional):
      - display_name: str
      - system_prompt: str
      - model: str
      - allowed_tools: list[str]
    """
    mycelos = request.app.state.mycelos
    agent = mycelos.agent_registry.get(agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")

    result: dict[str, Any] = {"ok": True, "id": agent_id}

    if "display_name" in body:
        new_name = (body.get("display_name") or "").strip()
        if not new_name:
            raise HTTPException(status_code=400, detail="display_name must not be empty")
        mycelos.agent_registry.rename(agent_id, new_name)
        mycelos.audit.log("agent.renamed", details={"agent_id": agent_id, "display_name": new_name})
        result["display_name"] = new_name

    persona_fields = {}
    if "system_prompt" in body:
        persona_fields["system_prompt"] = body["system_prompt"]
    if "model" in body:
        persona_fields["model"] = body["model"]
    if "allowed_tools" in body:
        tools = body["allowed_tools"]
        if not isinstance(tools, list):
            raise HTTPException(status_code=400, detail="allowed_tools must be a list")
        persona_fields["allowed_tools"] = tools

    if persona_fields:
        info = mycelos.agent_registry.update_persona_fields(
            agent_id,
            audit=mycelos.audit,
            actor="web-ui",
            **persona_fields,
        )
        result["changed"] = info["changed"]

    return result


@router.get("/api/agents/{agent_id}/history")
async def agent_history(agent_id: str, request: Request, limit: int = 10) -> list[dict[str, Any]]:
    """Return the persona change history (for Advanced → History tab)."""
    mycelos = request.app.state.mycelos
    return mycelos.agent_registry.persona_history(agent_id, limit=limit)


@router.get("/api/agents/{agent_id}")
async def get_agent(agent_id: str, request: Request) -> dict[str, Any]:
    """Agent detail with code, tests, gherkin from ObjectStore."""
    mycelos = request.app.state.mycelos
    agent = mycelos.agent_registry.get(agent_id)
    if not agent:
        return JSONResponse({"error": "Agent not found"}, status_code=404)
    # Load code from object store
    from mycelos.storage.object_store import ObjectStore
    obj_store = ObjectStore(mycelos.data_dir)
    code_data = mycelos.agent_registry.get_code(agent_id, obj_store)
    return {**dict(agent), "code": code_data}
