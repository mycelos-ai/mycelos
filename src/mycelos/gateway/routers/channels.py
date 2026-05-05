"""Channels endpoint."""

from __future__ import annotations

import json as _json
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from mycelos.gateway.routers._helpers import resolve_user_id

router = APIRouter()


@router.post("/api/channels")
async def setup_channel(request: Request) -> dict[str, Any]:
    """Register a channel (Telegram, Slack) in the channels table + connector registry.

    This mirrors what the CLI does in connector_cmd.py _setup_telegram().
    Body: { "id": "telegram", "mode": "polling", "allowed_users": ["123"], "config": {} }
    """
    mycelos = request.app.state.mycelos
    body = await request.json()
    channel_id = body.get("id", "")
    channel_type = body.get("type", channel_id)
    mode = body.get("mode", "polling")
    status = body.get("status", "active")
    allowed_users = body.get("allowed_users", [])
    config = body.get("config", {})

    if not channel_id:
        return JSONResponse({"error": "Channel ID required"}, status_code=400)

    # Write to channels table (NixOS State)
    mycelos.storage.execute("DELETE FROM channels WHERE id = ?", (channel_id,))
    mycelos.storage.execute(
        """INSERT INTO channels (id, channel_type, mode, status, config, allowed_users)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (channel_id, channel_type, mode, status,
         _json.dumps(config), _json.dumps(allowed_users)),
    )

    # Register as connector so it shows up in /api/connectors
    existing = mycelos.connector_registry.get(channel_id)
    if not existing:
        mycelos.connector_registry.register(
            connector_id=channel_id,
            name=f"{channel_id.title()} Channel",
            connector_type="channel",
            capabilities=[],
            description=f"Chat via {channel_id.title()}",
            setup_type="channel",
        )

    mycelos.audit.log("channel.configured", details={
        "channel": channel_id, "mode": mode, "allowed_users": allowed_users,
    }, user_id=resolve_user_id(request))

    # New config generation
    mycelos.config.apply_from_state(
        state_manager=mycelos.state_manager,
        description=f"{channel_id.title()} channel configured (mode={mode})",
        trigger="channel_setup",
    )

    return {"status": "configured", "channel": channel_id}
