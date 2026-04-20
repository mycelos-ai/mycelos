"""Telegram Channel — aiogram-based bot that routes messages to ChatService.

Supports two modes (configured in channels table, NixOS-style):
- **polling** (default): Long polling via dp.start_polling(). No webhook needed.
- **webhook**: Receives messages via POST /telegram/webhook.

Access is restricted via an allowlist stored in the channels table.
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
from typing import Any

import uuid

from aiogram import Bot, Dispatcher, F, types
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from mycelos.chat.events import ChatEvent
from mycelos.chat.service import ChatService
from mycelos.i18n import t

logger = logging.getLogger("mycelos.telegram")

# Module-level dispatcher — shared across the Gateway
dp = Dispatcher()

# These are set during setup
_bot: Bot | None = None
_chat_service: ChatService | None = None
_app: Any | None = None  # App instance — avoid accessing _chat_service._app
_session_map: dict[int, str] = {}  # telegram_user_id → mycelos_session_id
_webhook_secret: str | None = None
_allowed_users: set[int] = set()  # Telegram user IDs allowed to use the bot
_polling_thread: threading.Thread | None = None
_pending_permissions: dict[str, dict] = {}  # permission_id → {telegram_user_id, permission}


def _build_permission_keyboard(permission_id: str, agent_name: str) -> InlineKeyboardMarkup:
    """Build inline keyboard for permission prompt."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text=t("permission.button.allow_session", agent=agent_name),
                callback_data=f"perm:allow_session:{permission_id}"),
            InlineKeyboardButton(
                text=t("permission.button.allow_always", agent=agent_name),
                callback_data=f"perm:allow_always:{permission_id}"),
        ],
        [
            InlineKeyboardButton(
                text=t("permission.button.allow_all"),
                callback_data=f"perm:allow_all:{permission_id}"),
            InlineKeyboardButton(
                text=t("permission.button.deny"),
                callback_data=f"perm:deny:{permission_id}"),
        ],
        [
            InlineKeyboardButton(
                text=t("permission.button.never", agent=agent_name),
                callback_data=f"perm:never:{permission_id}"),
        ],
    ])


def _parse_permission_callback(data: str) -> tuple[str, str] | None:
    """Parse 'perm:{decision}:{id}' callback data. Returns (decision, perm_id) or None."""
    parts = data.split(":")
    if len(parts) != 3 or parts[0] != "perm":
        return None
    return parts[1], parts[2]


def setup_telegram(
    bot_token: str,
    chat_service: ChatService,
    webhook_secret: str | None = None,
    allowed_users: list[int] | None = None,
    app: Any | None = None,
) -> Bot:
    """Initialize the Telegram bot with a token and ChatService.

    Args:
        bot_token: The Telegram Bot API token from @BotFather.
        chat_service: The Mycelos ChatService instance.
        webhook_secret: Secret for webhook verification (webhook mode only).
        allowed_users: List of Telegram user IDs allowed to use the bot.

    Returns:
        The configured Bot instance.
    """
    global _bot, _chat_service, _webhook_secret, _app
    _bot = Bot(token=bot_token, default=DefaultBotProperties(parse_mode=ParseMode.MARKDOWN))
    _chat_service = chat_service
    _webhook_secret = webhook_secret
    _app = app

    # Set allowlist
    _allowed_users.clear()
    if allowed_users:
        _allowed_users.update(allowed_users)

    logger.info(
        "Telegram bot initialized (allowed_users: %s, webhook_secret: %s)",
        list(_allowed_users) if _allowed_users else "any",
        "set" if webhook_secret else "none",
    )
    return _bot


def start_polling() -> threading.Thread | None:
    """Start long polling in a daemon thread.

    Returns the thread, or None if bot is not configured.
    """
    global _polling_thread

    if not _bot:
        logger.warning("Cannot start polling: bot not initialized")
        return None

    if _polling_thread and _polling_thread.is_alive():
        logger.warning("Polling already running")
        return _polling_thread

    def _run_polling():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            # handle_signals=False — required when running in a daemon thread
            # (signal handlers only work in the main thread)
            loop.run_until_complete(dp.start_polling(_bot, handle_signals=False))
        except Exception as e:
            logger.error("Telegram polling stopped: %s", e)
        finally:
            loop.close()

    _polling_thread = threading.Thread(
        target=_run_polling,
        daemon=True,
        name="telegram-polling",
    )
    _polling_thread.start()
    logger.info("Telegram polling started")
    return _polling_thread


def stop_polling() -> None:
    """Stop polling gracefully."""
    global _polling_thread
    if _polling_thread and _polling_thread.is_alive():
        dp.shutdown()
        logger.info("Telegram polling stopped")
    _polling_thread = None


def verify_webhook_secret(request_secret: str | None) -> bool:
    """Verify the webhook secret token from Telegram.

    Fail-closed: no secret configured = reject all webhooks (Constitution Rule 3).
    Polling mode does not use webhooks, so this only matters for webhook mode.
    """
    if _webhook_secret is None:
        logger.warning("Telegram webhook secret not configured — rejecting request (fail-closed)")
        return False
    import hmac
    return hmac.compare_digest(request_secret or "", _webhook_secret or "")


def is_user_allowed(user_id: int) -> bool:
    """Check if a Telegram user is allowed to use the bot.

    Fail-closed: empty allowlist means NO users are allowed (Constitution Rule 3).
    Exception: on first message after setup, auto-add the first user (bootstrap).
    """
    if not _allowed_users:
        # Bootstrap: auto-add the first user who messages the bot
        if _app:
            try:
                import json as _json
                _allowed_users.add(user_id)
                _app.storage.execute(
                    "UPDATE channels SET allowed_users = ? WHERE id = 'telegram'",
                    (_json.dumps(list(_allowed_users)),),
                )
                logger.info("Telegram: auto-added first user %s to allowlist (bootstrap)", user_id)
                _app.audit.log("telegram.user_bootstrapped", details={"user_id": user_id})
                return True
            except Exception as e:
                logger.error("Failed to bootstrap Telegram user: %s", e)
        logger.warning("Telegram allowlist is empty — blocking user %s (fail-closed)", user_id)
        return False
    return user_id in _allowed_users


def get_bot() -> Bot | None:
    """Get the current bot instance."""
    return _bot


def get_allowed_users() -> set[int]:
    """Get the current allowlist."""
    return _allowed_users.copy()


@dp.callback_query(F.data.startswith("perm:"))
async def handle_permission_callback(callback: types.CallbackQuery) -> None:
    """Handle permission button clicks."""
    if not _chat_service:
        await callback.answer(t("telegram.service_not_ready"))
        return

    result = _parse_permission_callback(callback.data)
    if not result:
        await callback.answer(t("telegram.invalid_callback"))
        return

    decision_key, perm_id = result
    pending = _pending_permissions.get(perm_id)

    if not pending:
        await callback.answer(t("permission.expired"))
        return

    # SECURITY: verify the clicking user matches the requesting user
    if callback.from_user.id != pending["telegram_user_id"]:
        await callback.answer(t("telegram.not_your_permission"))
        return

    # Map callback to numeric input for _handle_permission_response
    DECISION_MAP = {
        "allow_session": "1",
        "allow_always": "2",
        "allow_all": "3",
        "deny": "4",
        "never": "5",
    }
    numeric = DECISION_MAP.get(decision_key, "4")

    # Remove from pending
    del _pending_permissions[perm_id]

    # Process via ChatService
    user_id = pending["telegram_user_id"]
    session_id = _get_session(user_id)
    try:
        events = _chat_service.handle_message(
            numeric, session_id, f"telegram:{user_id}"
        )
    except Exception as e:
        logger.error("Permission callback error: %s", e)
        events = []

    # Edit original message to show result
    decision_labels = {
        "allow_session": t("permission.decision.allow_session"),
        "allow_always": t("permission.decision.allow_always"),
        "allow_all": t("permission.decision.allow_all"),
        "deny": t("permission.decision.deny"),
        "never": t("permission.decision.never"),
    }
    label = decision_labels.get(decision_key, t("permission.decision.processed"))
    try:
        await callback.message.edit_text(
            f"{callback.message.text}\n\n✅ {label}",
            reply_markup=None,
        )
    except Exception:
        pass  # Message may have been deleted
    await callback.answer(label)

    # Send response if any
    response_text = _render_events(events)
    if response_text:
        await _safe_answer(callback.message, response_text)


@dp.message(F.document)
async def handle_document(message: types.Message) -> None:
    """Handle incoming file attachments — save to inbox and analyze."""
    if not _chat_service or not _bot or not _app:
        return
    user_id = message.from_user.id if message.from_user else 0
    if not is_user_allowed(user_id):
        return

    doc = message.document
    # Size check BEFORE download
    if doc.file_size and doc.file_size > 50 * 1024 * 1024:
        await _safe_answer(message, t("telegram.file_too_large"))
        return

    await _bot.send_chat_action(message.chat.id, "typing")

    try:
        file = await _bot.get_file(doc.file_id)
        data = await _bot.download_file(file.file_path)
        file_bytes = data.read()

        from mycelos.files.inbox import InboxManager
        inbox = InboxManager(_app.data_dir / "inbox")
        inbox_path = inbox.save(file_bytes, doc.file_name or "unnamed")

        # PDF/DOCX → Knowledge ingest path
        suffix = inbox_path.suffix.lower()
        if suffix in ('.pdf', '.docx', '.doc'):
            from mycelos.knowledge.ingest import ingest_pdf

            chat_id = message.chat.id
            typing_active = True
            async def _keep_typing():
                while typing_active:
                    try:
                        await _bot.send_chat_action(chat_id, "typing")
                    except Exception:
                        pass
                    await asyncio.sleep(4)
            typing_task = asyncio.create_task(_keep_typing())

            result = await asyncio.to_thread(ingest_pdf, _app, inbox_path)
            typing_active = False
            typing_task.cancel()

            if result["vision_needed"]:
                await _safe_answer(message,
                    f"📄 Document saved to Knowledge Base: _{doc.file_name}_\n"
                    f"({result['page_count']} pages, no text layer)\n\n"
                    f"Shall I analyze it with Vision? (~${result['page_count'] * 0.02:.2f})")
            else:
                await _safe_answer(message,
                    f"📄 Document saved to Knowledge Base: _{doc.file_name}_\n"
                    f"Summary note created. The organizer will classify it into a topic.")
            return

        from mycelos.files.extractor import extract_text
        text, method = extract_text(inbox_path)

        if method == "vision_needed":
            await _safe_answer(message,
                f"File saved: _{inbox_path.name}_\n"
                "Shall I analyze the image? (~$0.01)")
            return

        if text:
            # Continuous typing indicator while LLM processes
            chat_id = message.chat.id
            typing_active = True

            async def _keep_typing():
                while typing_active:
                    try:
                        await _bot.send_chat_action(chat_id, "typing")
                    except Exception:
                        pass
                    await asyncio.sleep(4)

            typing_task = asyncio.create_task(_keep_typing())

            session_id = _get_session(user_id)
            events = await asyncio.to_thread(
                _chat_service.handle_message,
                f"[File: {doc.file_name or inbox_path.name}] Analyze this document:\n\n{text[:2000]}",
                session_id, f"telegram:{user_id}",
            )
            typing_active = False
            typing_task.cancel()

            response_text = _render_events(events)
            if response_text:
                await _safe_answer(message, response_text)
        else:
            await _safe_answer(message, f"File saved to inbox: _{inbox_path.name}_")

    except Exception as e:
        logger.error("Document handler error: %s", e, exc_info=True)
        await _safe_answer(message, t("telegram.file_process_failed"))


@dp.message(F.photo)
async def handle_photo(message: types.Message) -> None:
    """Handle incoming photos — save to inbox."""
    if not _chat_service or not _bot or not _app:
        return
    user_id = message.from_user.id if message.from_user else 0
    if not is_user_allowed(user_id):
        return

    try:
        photo = message.photo[-1]  # Largest size
        file = await _bot.get_file(photo.file_id)
        data = await _bot.download_file(file.file_path)

        from mycelos.files.inbox import InboxManager
        inbox = InboxManager(_app.data_dir / "inbox")
        inbox_path = inbox.save(data.read(), f"photo-{photo.file_unique_id}.jpg")

        await _safe_answer(message,
            f"Photo saved: _{inbox_path.name}_\n"
            "Shall I analyze it? (~$0.01)")

    except Exception as e:
        logger.error("Photo handler error: %s", e, exc_info=True)
        await _safe_answer(message, t("telegram.photo_save_failed"))


@dp.message(F.voice)
async def handle_voice_message(message: types.Message) -> None:
    """Handle incoming voice messages — transcribe and process as text."""
    if not _chat_service or not _bot:
        return

    user_id = message.from_user.id if message.from_user else 0

    # Check allowlist
    if not is_user_allowed(user_id):
        logger.warning("Unauthorized Telegram voice from: %s", user_id)
        return

    # Check proxy is available
    if not _app or not _app.proxy_client:
        await _safe_answer(message, t("telegram.voice_needs_gateway"))
        return

    logger.debug("Telegram voice from %s (%ds)", user_id, message.voice.duration or 0)

    # Typing indicator
    await _bot.send_chat_action(message.chat.id, "typing")

    try:
        # Download voice file
        file = await _bot.get_file(message.voice.file_id)
        audio_data = await _bot.download_file(file.file_path)
        audio_bytes = audio_data.read()

        # Transcribe
        result = _app.proxy_client.stt_transcribe(
            audio=audio_bytes,
            filename="voice.ogg",
            language="auto",
            user_id=f"telegram:{user_id}",
        )

        # Check for error in response (e.g., missing credential)
        if "error" in result and "text" not in result:
            error_msg = result.get("error", "")
            if "not configured" in error_msg.lower() or "credential" in error_msg.lower():
                await _safe_answer(message, t("telegram.voice_needs_key"))
            else:
                await _safe_answer(message, t("telegram.voice_transcription_failed", error=error_msg))
            return

        text = result.get("text", "").strip()

        if not text:
            await _safe_answer(message, t("telegram.voice_not_understood"))
            return

        # Show transcription to user
        await _safe_answer(message, f"_{text}_")

        # Continuous typing indicator while LLM processes
        chat_id = message.chat.id
        typing_active = True

        async def _keep_typing():
            while typing_active:
                try:
                    await _bot.send_chat_action(chat_id, "typing")
                except Exception:
                    pass
                await asyncio.sleep(4)

        typing_task = asyncio.create_task(_keep_typing())

        # Process as chat message — in thread pool to keep event loop free
        session_id = _get_session(user_id)
        events = await asyncio.to_thread(
            _chat_service.handle_message,
            f"[Voice] {text}", session_id, f"telegram:{user_id}",
        )
        typing_active = False
        typing_task.cancel()

        response_text = _render_events(events)
        if response_text:
            await _safe_answer(message, response_text)

    except Exception as e:
        error_str = str(e).lower()
        logger.error("Voice transcription error: %s", e, exc_info=True)
        if "not configured" in error_str or "credential" in error_str or "api_key" in error_str:
            await _safe_answer(message, t("telegram.voice_needs_key_short"))
        else:
            await _safe_answer(message, t("telegram.voice_failed_retry"))


@dp.message()
async def handle_message(message: types.Message) -> None:
    """Handle incoming Telegram messages."""
    if not message.text or not _chat_service or not _bot:
        return

    user_id = message.from_user.id if message.from_user else 0
    text = message.text.strip()

    # Security: check user authorization (H-01)
    if not is_user_allowed(user_id):
        await message.answer(t("telegram.not_authorized"))
        logger.warning("Unauthorized Telegram user: %s", user_id)
        return

    logger.debug("Telegram message from %s: %s", user_id, text[:80])

    # Slash commands — handle directly (bypass LLM)
    if text.startswith("/") and not text.startswith("/start"):
        from mycelos.chat.slash_commands import handle_slash_command
        # Need an app instance for slash commands
        if _app is not None:
            result = handle_slash_command(_app, text)
            if isinstance(result, list):
                # ChatEvent list (e.g. from /demo widget)
                response_text = _render_events(result)
                if response_text:
                    for chunk in _split_message(response_text, 4000):
                        await message.answer(chunk, parse_mode=ParseMode.MARKDOWN)
            else:
                await message.answer(result, parse_mode=ParseMode.MARKDOWN)
            return

    # /start command — Telegram special
    if text == "/start":
        await message.answer(
            t("telegram.start_greeting"),
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    # Get or create session for this Telegram user
    session_id = _get_session(user_id)

    # Store chat_id for proactive notifications (reminders etc.)
    if _app:
        try:
            stored_chat_id = _app.memory.get("default", "system", "telegram_chat_id")
            if not stored_chat_id:
                _app.memory.set(
                    "default", "system", "telegram_chat_id",
                    str(message.chat.id), created_by="telegram",
                )
        except Exception:
            pass

    # Show "typing..." indicator continuously while processing
    chat_id = message.chat.id
    typing_active = True

    async def _keep_typing():
        while typing_active:
            try:
                await _bot.send_chat_action(chat_id, "typing")
            except Exception:
                pass
            await asyncio.sleep(4)

    typing_task = asyncio.create_task(_keep_typing())

    # Process through ChatService — run in thread pool so the event loop
    # stays free for the _keep_typing() task to refresh the indicator.
    try:
        events = await asyncio.to_thread(
            _chat_service.handle_message,
            message=text,
            session_id=session_id,
            user_id=f"telegram:{user_id}",
            channel="telegram",
        )
        typing_active = False
        typing_task.cancel()

        # Check for permission prompt in events
        for event in events:
            if (event.type == "widget"
                    and isinstance(event.data, dict)
                    and event.data.get("widget", {}).get("type") == "permission_prompt"):

                widget = event.data["widget"]
                perm_id = widget.get("permission_id", uuid.uuid4().hex[:12])
                agent_name = widget.get("agent", "Agent")

                _pending_permissions[perm_id] = {
                    "telegram_user_id": user_id,
                }

                keyboard = _build_permission_keyboard(perm_id, agent_name)
                text = (
                    f"⚠️ *Permission Required*\n\n"
                    f"*{agent_name}* → `{widget.get('tool', '')}`\n"
                    f"`{widget.get('target', '')}`\n"
                    f"_{widget.get('reason', '')}_"
                )
                try:
                    await message.answer(text, parse_mode="Markdown", reply_markup=keyboard)
                except Exception:
                    await message.answer(text, reply_markup=keyboard)
                return  # Wait for button click, don't send normal response

        # Render events as Telegram messages
        response_text = _render_events(events)
        if response_text:
            # Split long messages (Telegram limit: 4096 chars)
            for chunk in _split_message(response_text, 4000):
                await _safe_answer(message, chunk)

    except Exception as e:
        typing_active = False
        typing_task.cancel()
        logger.error("Telegram handler error: %s", e, exc_info=True)
        await message.answer(t("telegram.error_occurred"))


async def _safe_answer(message: types.Message, text: str) -> None:
    """Send a message to Telegram with robust fallback.

    1. Try Markdown
    2. Try plain text (no parse_mode)
    3. Try stripped text (remove all Markdown chars)
    4. Send error message
    """
    # Attempt 1: Markdown
    try:
        await message.answer(text, parse_mode=ParseMode.MARKDOWN)
        return
    except Exception:
        pass

    # Attempt 2: plain text (no parse_mode — but some chars still cause issues)
    try:
        await message.answer(text, parse_mode=None)
        return
    except Exception:
        pass

    # Attempt 3: strip all Markdown special chars
    try:
        stripped = _strip_markdown(text)
        await message.answer(stripped, parse_mode=None)
        return
    except Exception:
        pass

    # Attempt 4: give up, send error
    try:
        await message.answer(t("telegram.format_failed"))
    except Exception:
        logger.error("Failed to send ANY message to Telegram")


def _strip_markdown(text: str) -> str:
    """Remove Markdown formatting characters that Telegram can't handle."""
    import re
    # Remove backtick code blocks
    text = re.sub(r"```[\s\S]*?```", "[code block]", text)
    # Remove inline code
    text = re.sub(r"`([^`]*)`", r"\1", text)
    # Remove bold/italic markers
    text = text.replace("**", "").replace("__", "")
    text = text.replace("*", "").replace("_", "")
    # Remove link syntax [text](url) → text (url)
    text = re.sub(r"\[([^\]]*)\]\(([^)]*)\)", r"\1 (\2)", text)
    return text


def _get_session(telegram_user_id: int) -> str:
    """Get or create a Mycelos session for a Telegram user.

    Persists across server restarts by looking up the most recent
    session for this Telegram user in the SessionStore.
    """
    if telegram_user_id not in _session_map:
        user_id = f"telegram:{telegram_user_id}"
        # Try to find an existing session for this user
        existing = _app.session_store.list_sessions() if _app else []
        for s in existing:
            if s.get("user_id") == user_id:
                _session_map[telegram_user_id] = s["session_id"]
                logger.debug("Resumed session %s for %s", s["session_id"][:8], user_id)
                break
        else:
            # No existing session — create a new one
            session_id = _chat_service.create_session(user_id=user_id)
            _session_map[telegram_user_id] = session_id
            logger.debug("New session %s for %s", session_id[:8], user_id)
    return _session_map[telegram_user_id]


def _render_events(events: list[ChatEvent]) -> str:
    """Convert ChatEvents to a single Telegram message."""
    parts: list[str] = []
    _tools_used: set[str] = set()

    for event in events:
        if event.type == "text":
            parts.append(event.data.get("content", ""))
        elif event.type == "system-response":
            parts.append(event.data.get("content", ""))
        elif event.type == "error":
            parts.append(f"Error: {event.data.get('message', 'Error')}")
        elif event.type == "step-progress":
            # Collect tool names silently — shown in footer
            step = event.data.get("step_id", "")
            status = event.data.get("status", "")
            if status == "done" and step and step not in ("auto-compact",):
                _tools_used.add(step)
        elif event.type == "done":
            tokens = event.data.get("tokens", 0)
            model = (event.data.get("model", "") or "").split("/")[-1]  # Strip provider prefix
            cost = event.data.get("cost", 0)
            footer_parts = []
            if tokens:
                footer_parts.append(f"{tokens} tokens")
            if model:
                footer_parts.append(model)
            if _tools_used:
                footer_parts.append(f"{len(_tools_used)} tools")
            if cost and cost > 0:
                footer_parts.append(f"${cost:.4f}")
            if footer_parts:
                parts.append(f"\n_({' | '.join(footer_parts)})_")
        elif event.type == "widget":
            from mycelos.widgets import widget_from_dict
            from mycelos.widgets.telegram_renderer import TelegramRenderer
            widget = widget_from_dict(event.data["widget"])
            parts.append(TelegramRenderer().render(widget))

    return "\n".join(parts)


def _split_message(text: str, max_len: int = 4000) -> list[str]:
    """Split a long message into chunks for Telegram."""
    if len(text) <= max_len:
        return [text]

    chunks = []
    while text:
        if len(text) <= max_len:
            chunks.append(text)
            break
        # Find a good split point (newline or space)
        split_at = text.rfind("\n", 0, max_len)
        if split_at < max_len // 2:
            split_at = text.rfind(" ", 0, max_len)
        if split_at < max_len // 2:
            split_at = max_len
        chunks.append(text[:split_at])
        text = text[split_at:].lstrip()

    return chunks


def call_telegram_api(
    app: Any,
    method: str,
    payload: dict | None = None,
    *,
    http_method: str = "POST",
    timeout: int = 10,
) -> dict:
    """Call the Telegram Bot API through the SecurityProxy when available.

    The bot token never crosses the gateway process boundary in the
    two-container deployment: we hand the proxy a URL with a literal
    ``{credential}`` placeholder and let it substitute. In the
    single-container mode (no MYCELOS_PROXY_URL) we fall back to a
    direct call using the local credential.

    Returns the parsed Telegram response (``{"ok": bool, …}``) or, on
    transport failure, a synthetic ``{"ok": False, "description": "..."}``
    so callers can treat all errors uniformly.
    """
    import json as _json
    import urllib.request

    url = f"https://api.telegram.org/bot{{credential}}/{method}"

    # --- Two-container path: delegate to SecurityProxy ---
    from mycelos.connectors import http_tools as _http_tools
    pc = getattr(_http_tools, "_proxy_client", None)
    if pc is not None:
        try:
            if http_method.upper() == "GET":
                resp = pc.http_get(url, credential="telegram", inject_as="url_path", timeout=timeout)
            else:
                resp = pc.http_post(
                    url,
                    body=payload or {},
                    credential="telegram",
                    inject_as="url_path",
                    timeout=timeout,
                )
        except Exception as e:
            return {"ok": False, "description": f"proxy transport error: {e}"}

        status = resp.get("status", 0)
        body = resp.get("body", "")
        if not body:
            return {"ok": False, "description": f"proxy returned empty body (HTTP {status})"}
        try:
            parsed = _json.loads(body)
            if isinstance(parsed, dict):
                return parsed
        except ValueError:
            pass
        return {"ok": False, "description": f"non-JSON response (HTTP {status})"}

    # --- Single-container fallback: local credential ---
    try:
        cred = app.credentials.get_credential("telegram")
    except Exception as e:
        return {"ok": False, "description": f"credential lookup failed: {e}"}
    if not cred or not cred.get("api_key"):
        return {"ok": False, "description": "no telegram credential"}
    token = cred["api_key"]

    resolved = url.replace("{credential}", token)
    try:
        data = _json.dumps(payload or {}).encode()
        req = urllib.request.Request(
            resolved,
            data=data if http_method.upper() == "POST" else None,
            headers={"Content-Type": "application/json"},
            method=http_method.upper(),
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            parsed = _json.loads(resp.read())
            if isinstance(parsed, dict):
                return parsed
            return {"ok": False, "description": "non-dict response"}
    except Exception as e:
        return {"ok": False, "description": str(e)}


def send_notification(app: Any, message: str) -> bool:
    """Send a proactive notification message via Telegram.

    Uses the stored telegram_chat_id from memory. The bot token is
    resolved inside the SecurityProxy (two-container) or via the local
    credential (single-container) — never visible in the gateway.

    Automatically splits messages exceeding Telegram's 4096-character
    limit. Returns True if every chunk was accepted.
    """
    try:
        chat_id = app.memory.get("default", "system", "telegram_chat_id")
        if not chat_id:
            logger.debug("No Telegram chat_id configured — skipping notification")
            return False

        chunks = _split_message(message, max_len=4096)

        for chunk in chunks:
            result = call_telegram_api(
                app,
                "sendMessage",
                {"chat_id": chat_id, "text": chunk},
            )
            if not result.get("ok"):
                logger.warning("Telegram API error: %s", result.get("description"))
                return False

        logger.info("Telegram notification sent to chat_id=%s (%d part(s))", chat_id, len(chunks))
        return True
    except Exception as e:
        logger.warning("Telegram notification failed: %s", e)
        return False


def _split_message(text: str, max_len: int = 4096) -> list[str]:
    """Split a message into chunks that fit Telegram's character limit.

    Tries to split at paragraph boundaries, then line boundaries.
    """
    if len(text) <= max_len:
        return [text]

    chunks: list[str] = []
    remaining = text
    while remaining:
        if len(remaining) <= max_len:
            chunks.append(remaining)
            break

        # Try to split at a paragraph boundary (double newline)
        split_at = remaining.rfind("\n\n", 0, max_len)
        if split_at == -1 or split_at < max_len // 2:
            # Try single newline
            split_at = remaining.rfind("\n", 0, max_len)
        if split_at == -1 or split_at < max_len // 2:
            # Hard split at limit
            split_at = max_len

        chunks.append(remaining[:split_at])
        remaining = remaining[split_at:].lstrip("\n")

    return chunks


def load_channel_config(storage: Any) -> dict[str, Any] | None:
    """Load Telegram channel config from channels table.

    Returns dict with keys: mode, allowed_users, config, status
    or None if not configured.
    """
    try:
        row = storage.fetchone(
            "SELECT * FROM channels WHERE id = 'telegram' AND status = 'active'"
        )
        if not row:
            return None

        config = row["config"]
        allowed = row["allowed_users"]
        if isinstance(config, str):
            config = json.loads(config)
        if isinstance(allowed, str):
            allowed = json.loads(allowed)

        return {
            "mode": row["mode"],
            "config": config,
            "allowed_users": allowed,
            "status": row["status"],
        }
    except Exception:
        return None
