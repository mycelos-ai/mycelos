"""Telegram webhook endpoint."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

logger = logging.getLogger("mycelos.gateway")

router = APIRouter()


@router.post("/telegram/webhook")
async def telegram_webhook(request: Request) -> dict:
    """Receive Telegram webhook updates."""
    from aiogram import types as aio_types
    from mycelos.channels.telegram import dp, get_bot, verify_webhook_secret

    bot = get_bot()
    if not bot:
        return {"error": "Telegram bot not configured"}

    # C-03: Verify webhook secret token
    secret = request.headers.get("x-telegram-bot-api-secret-token")
    if not verify_webhook_secret(secret):
        logger.warning("Telegram webhook: invalid secret token")
        return JSONResponse({"error": "Invalid secret token"}, status_code=403)

    try:
        update_data = await request.json()
        update = aio_types.Update.model_validate(update_data)
        await dp.feed_update(bot, update)
        return {"ok": True}
    except Exception as e:
        logger.error("Telegram webhook error: %s", e)
        return {"ok": False}  # Don't leak error details (H-04)
