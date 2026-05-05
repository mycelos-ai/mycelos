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
    raw_allowed = body.get("allowed_users", [])
    config = body.get("config", {})

    if not channel_id:
        return JSONResponse({"error": "Channel ID required"}, status_code=400)

    # Telegram allowlist must be int IDs — input from the web wizard
    # arrives as strings. Coerce here so the channels table is always
    # consistent and is_user_allowed's int-membership check works.
    allowed_users: list[int] = []
    for u in raw_allowed or []:
        try:
            allowed_users.append(int(u))
        except (TypeError, ValueError):
            return JSONResponse(
                {"error": f"Invalid user ID in allowed_users: {u!r}"},
                status_code=400,
            )

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

    # Hot-start the channel in the running gateway so the user sees a
    # response from their bot immediately — no `docker compose restart`.
    # We reuse the same code path the boot sequence uses.
    started = False
    if channel_id == "telegram":
        try:
            from mycelos.gateway.server import start_telegram_channel
            start_telegram_channel(mycelos, request.app, debug=False)
            started = getattr(request.app.state, "telegram_bot", None) is not None
        except Exception as e:
            mycelos.audit.log(
                "channel.hot_start_failed",
                details={"channel": channel_id, "error": str(e)},
                user_id=resolve_user_id(request),
            )

    return {"status": "configured", "channel": channel_id, "started": started}
