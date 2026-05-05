"""Setup, credentials, telegram, and memory endpoints."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from mycelos.gateway.routers._helpers import (
    CredentialAddRequest,
    resolve_user_id as _resolve_user_id,
)

logger = logging.getLogger("mycelos.gateway")

router = APIRouter()


# ── Setup / Onboarding ─────────────────────────────────────


@router.get("/api/setup/status")
async def setup_status(request: Request) -> dict[str, Any]:
    """Tell the frontend whether onboarding is still required."""
    from mycelos.setup import is_initialized
    mycelos = request.app.state.mycelos
    return {"initialized": is_initialized(mycelos)}


@router.post("/api/setup")
async def run_setup(request: Request, body: dict[str, Any]) -> dict[str, Any]:
    """Run the web onboarding flow: credential + provider + models + agents."""
    from mycelos.setup import SetupError, web_init
    mycelos = request.app.state.mycelos
    try:
        return web_init(
            mycelos,
            api_key=body.get("api_key"),
            provider_id=body.get("provider_id"),
            ollama_url=body.get("ollama_url"),
        )
    except SetupError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception("web_init failed")
        raise HTTPException(status_code=500, detail=f"Setup failed: {e}")


# ── Credentials ────────────────────────────────────────────


@router.get("/api/credentials")
async def list_credentials(request: Request) -> list[dict[str, Any]]:
    """List credentials (service + label only, NO keys)."""
    mycelos = request.app.state.mycelos
    try:
        creds = mycelos.credentials.list_credentials()
        return creds
    except Exception:
        # Gateway mode — credentials managed by proxy
        services = mycelos.storage.fetchall(
            "SELECT service, label, description, created_at FROM credentials ORDER BY service"
        )
        return [dict(s) for s in services]


@router.post("/api/credentials")
async def add_credential(request: Request, body: CredentialAddRequest) -> dict[str, Any]:
    """Store a credential (encrypted)."""
    mycelos = request.app.state.mycelos
    try:
        mycelos.credentials.store_credential(
            body.service,
            {"api_key": body.secret},
            label=body.label,
            description=body.description,
        )
        mycelos.audit.log("credential.stored", details={"service": body.service, "label": body.label}, user_id=_resolve_user_id(request))
        return {"status": "stored", "service": body.service, "label": body.label}
    except RuntimeError as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@router.delete("/api/credentials/{service}")
async def delete_credential(request: Request, service: str, label: str = "default") -> dict[str, Any]:
    """Delete a credential."""
    mycelos = request.app.state.mycelos
    try:
        mycelos.credentials.delete_credential(service, label=label)
        mycelos.audit.log("credential.deleted", details={"service": service, "label": label}, user_id=_resolve_user_id(request))
        return {"status": "deleted", "service": service, "label": label}
    except RuntimeError as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@router.post("/api/credentials/oauth-keys/validate")
async def validate_oauth_keys(payload: dict[str, Any]) -> dict[str, Any]:
    """Cheap shape-check on uploaded gcp-oauth.keys.json content.

    Returns {ok: bool, kind?: str, error?: str}. Non-200 is reserved
    for framework errors; validation failures are ok=False with a
    human-readable message so the UI can keep showing the dialog.
    """
    import json as _json

    content = payload.get("content", "")
    if not content:
        return {"ok": False, "error": "Empty content — paste the gcp-oauth.keys.json file."}
    try:
        data = _json.loads(content)
    except _json.JSONDecodeError as e:
        return {"ok": False, "error": f"Not valid JSON: {e}"}
    if not isinstance(data, dict):
        return {"ok": False, "error": "Top-level must be a JSON object."}
    if "installed" in data and isinstance(data["installed"], dict):
        inst = data["installed"]
        if "client_id" in inst and "client_secret" in inst:
            return {"ok": True, "kind": "desktop"}
        return {"ok": False, "error": "Missing client_id or client_secret in 'installed' section."}
    if "web" in data:
        return {
            "ok": False,
            "error": (
                "This looks like a Web-app OAuth credential. Mycelos needs a "
                "Desktop-app credential. Go back to Cloud Console → Credentials "
                "→ Create credentials → OAuth client ID → Desktop app."
            ),
        }
    return {
        "ok": False,
        "error": (
            "File doesn't look like a gcp-oauth.keys.json. Expected a top-level "
            "'installed' or 'web' key. Make sure you downloaded the OAuth-client JSON, "
            "not the project's service-account key."
        ),
    }


# ── Telegram Setup ──────────────────────────────────────────


def _scrub_token(text: str, token: str) -> str:
    """Remove any occurrence of the bot token from an error message.

    Telegram's API requires the token in the URL path, so if an
    exception includes the request URL (httpx does this for timeouts
    and connection errors), the raw token would leak into the
    response body. Strip it defensively.
    """
    if not token or not text:
        return text
    return text.replace(token, "<redacted>")


@router.post("/api/telegram/check")
async def telegram_check(request: Request) -> dict[str, Any]:
    """Check for Telegram bot messages to detect chat ID.

    Validates the token via getMe, then tries getUpdates to find
    the user's chat ID. Handles conflict with running long-polling.
    Routed through the SecurityProxy in two-container mode — the
    gateway never opens a direct socket to api.telegram.org.
    """
    from mycelos.channels.telegram import call_telegram_api_with_token
    mycelos = request.app.state.mycelos
    body = await request.json()
    token = (body.get("token") or "").strip()
    if not token or ":" not in token:
        return JSONResponse({"error": "Invalid bot token format"}, status_code=400)

    mycelos.audit.log("telegram.setup.check_started", user_id="default", details={})

    # Step 1: Validate token via getMe
    me = call_telegram_api_with_token(
        mycelos, token, "getMe", http_method="GET", timeout=10,
    )
    if not me.get("ok"):
        desc = me.get("description", "Invalid bot token")
        mycelos.audit.log(
            "telegram.setup.check_failed",
            user_id="default",
            details={"stage": "getMe"},
        )
        return {"error": _scrub_token(desc, token)}

    bot_name = me.get("result", {}).get("first_name", "Bot")
    bot_username = me.get("result", {}).get("username", "")

    # Step 2: Try getUpdates to find chat ID
    chat_id = None
    updates_data = call_telegram_api_with_token(
        mycelos, token, "getUpdates",
        payload={"limit": 100, "timeout": 1},
        http_method="GET", timeout=10,
    )

    if not updates_data.get("ok") and "Conflict" in (updates_data.get("description") or ""):
        # Long-polling is running — stop temporarily and retry
        tg_channel = getattr(request.app.state, "_telegram_channel", None)
        if tg_channel and hasattr(tg_channel, "stop"):
            try:
                await tg_channel.stop()
            except Exception:
                pass
        import asyncio
        await asyncio.sleep(1)
        updates_data = call_telegram_api_with_token(
            mycelos, token, "getUpdates",
            payload={"limit": 100, "timeout": 2},
            http_method="GET", timeout=10,
        )
        if tg_channel and hasattr(tg_channel, "start"):
            try:
                await tg_channel.start()
            except Exception:
                pass

    # Find any chat ID from updates
    results = updates_data.get("result", []) if updates_data.get("ok") else []
    for update in reversed(results):
        msg = update.get("message") or update.get("my_chat_member", {}).get("chat")
        if msg and isinstance(msg, dict):
            chat = msg.get("chat", msg)
            if isinstance(chat, dict) and chat.get("id"):
                chat_id = str(chat["id"])
                break

    mycelos.audit.log(
        "telegram.setup.check_succeeded",
        user_id="default",
        details={"bot_username": bot_username, "chat_id_found": chat_id is not None},
    )
    return {
        "valid": True,
        "bot_name": bot_name,
        "bot_username": bot_username,
        "chat_id": chat_id,
        "updates": len(results),
    }


@router.post("/api/telegram/verify")
async def telegram_verify(request: Request) -> dict[str, Any]:
    """Send a test message to verify the chat ID works.

    Routed through the SecurityProxy in two-container mode so the
    gateway never opens a direct socket to api.telegram.org.
    """
    from mycelos.channels.telegram import call_telegram_api_with_token
    mycelos = request.app.state.mycelos
    body = await request.json()
    token = (body.get("token") or "").strip()
    chat_id = (body.get("chat_id") or "").strip()
    if not token or not chat_id:
        return JSONResponse({"error": "token and chat_id required"}, status_code=400)

    data = call_telegram_api_with_token(
        mycelos, token, "sendMessage",
        payload={
            "chat_id": chat_id,
            "text": "Mycelos connected! This bot is ready to use.",
        },
        timeout=10,
    )

    if not data.get("ok"):
        desc = data.get("description", "Unknown error")
        if "chat not found" in desc.lower() or "CHAT_NOT_FOUND" in desc:
            return {
                "error": (
                    "Chat ID not found. Open your new bot in Telegram and send "
                    "it any message (e.g. /start), then try again."
                )
            }
        mycelos.audit.log(
            "telegram.setup.verify_failed", user_id="default", details={},
        )
        return {"error": _scrub_token(desc, token)}

    mycelos.audit.log("telegram.setup.verify_succeeded", user_id="default", details={})
    return {"ok": True, "message_id": data.get("result", {}).get("message_id")}


# ── Memory (key-value) ──────────────────────────────────────


@router.post("/api/memory")
async def set_memory(request: Request) -> dict[str, Any]:
    """Set a memory entry."""
    mycelos = request.app.state.mycelos
    body = await request.json()
    scope = body.get("scope", "system")
    key = body.get("key", "")
    value = body.get("value", "")
    if not key:
        return JSONResponse({"error": "key is required"}, status_code=400)
    user_id = _resolve_user_id(request)
    mycelos.memory.set(user_id, scope, key, value)
    mycelos.audit.log("memory.set", details={"scope": scope, "key": key}, user_id=user_id)
    return {"status": "stored", "scope": scope, "key": key}
