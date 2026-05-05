"""Chat + health endpoints."""

from __future__ import annotations

import logging
import time
from typing import Any

from fastapi import APIRouter, Request
from starlette.responses import StreamingResponse

from mycelos.gateway.routers._helpers import ChatRequest, resolve_user_id

logger = logging.getLogger("mycelos.gateway")

_LOCALHOST_ADDRS = ("127.0.0.1", "::1")

router = APIRouter()


@router.post("/api/chat")
async def chat(http_request: Request, request: ChatRequest) -> StreamingResponse:
    """Process a chat message and stream SSE response."""
    service = http_request.app.state.chat_service
    debug = getattr(http_request.app.state, "debug", False)

    # Security: resolve user_id from auth context, NOT from request body
    user_id = resolve_user_id(http_request)

    # Create or use existing session
    session_id = request.session_id
    if not session_id:
        session_id = service.create_session(user_id=user_id)

    if debug:
        logger.debug(
            "Chat request: user=%s session=%s channel=%s message=%s",
            user_id, session_id[:8], request.channel,
            request.message[:80],
        )

    # Onboarding gate — if Mycelos has no credential/model yet, return a
    # setup widget instead of calling the LLM. Skip for slash commands so
    # power users can still run /credential store etc. even pre-setup.
    if not request.message.startswith("/"):
        from mycelos.setup import is_initialized
        from mycelos.chat.events import system_response_event, done_event, session_event
        mycelos = http_request.app.state.mycelos
        if not is_initialized(mycelos):
            welcome = (
                "👋 Welcome to Mycelos! Before we can chat, I need an LLM provider.\n\n"
                "Enter an API key (Anthropic, OpenAI) or an Ollama URL in the setup "
                "form below — no CLI required."
            )
            setup_event = system_response_event(welcome)
            # Frontend watches for `setup_required` and opens the onboarding modal.
            setup_event.data["widget"] = "setup_required"
            events_out = [session_event(session_id), setup_event, done_event()]

            async def setup_stream():
                for ev in events_out:
                    yield ev.to_sse()
            return StreamingResponse(
                setup_stream(),
                media_type="text/event-stream",
                headers={"Cache-Control": "no-cache"},
            )

    # Slash commands bypass LLM entirely (except /run which needs
    # streaming progress and session persistence via ChatService)
    if request.message.startswith("/") and not request.message.startswith("/run"):
        from mycelos.chat.slash_commands import handle_slash_command
        from mycelos.chat.events import system_response_event, done_event, session_event

        mycelos = http_request.app.state.mycelos

        # Persist the user message to session store
        mycelos.session_store.append_message(
            session_id, role="user", content=request.message,
        )

        result = handle_slash_command(mycelos, request.message)
        if isinstance(result, list):
            # ChatEvent list (e.g. from /demo widget)
            all_events = [session_event(session_id)] + result + [done_event()]
        else:
            all_events = [session_event(session_id), system_response_event(result), done_event()]

        # Persist response content to session store so it survives page reload
        for evt in all_events:
            if evt.type in ("system-response", "text"):
                content = evt.data.get("content", "")
                if content:
                    mycelos.session_store.append_message(
                        session_id, role="assistant", content=content,
                        metadata={"agent": "System"},
                    )

        async def slash_stream():
            for event in all_events:
                yield event.to_sse()
        return StreamingResponse(slash_stream(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache"})

    # Process message in a thread, streaming SSE events as they arrive.
    # ChatService appends to the events list; we poll it incrementally
    # so step-progress events (workflow tool calls) appear in real time.
    # Note: list.append() and len() are atomic under CPython's GIL.
    # This is sufficient for our use case (single writer thread, single reader coroutine).
    import asyncio
    from mycelos.chat.events import session_event

    events: list = []
    done_flag = asyncio.Event()
    start = time.time()

    def _run_sync():
        try:
            result = service.handle_message(
                message=request.message,
                session_id=session_id,
                user_id=user_id,
                channel=request.channel or "api",
                workflow_run_id=request.workflow_run_id,
                target_agent_id=request.target_agent_id,
            )
            events.extend(result)
        except Exception as exc:
            logger.error("Chat handler error: %s", exc, exc_info=True)
            from mycelos.chat.events import error_event, done_event as _done
            events.extend([error_event("An internal error occurred."), _done()])
        finally:
            done_flag.set()

    loop = asyncio.get_event_loop()
    loop.run_in_executor(None, _run_sync)

    async def event_stream():
        yield session_event(session_id).to_sse()
        sent = 0
        while True:
            # Yield any new events that appeared since last check
            while sent < len(events):
                yield events[sent].to_sse()
                sent += 1
            if done_flag.is_set():
                # Flush remaining events
                while sent < len(events):
                    yield events[sent].to_sse()
                    sent += 1
                break
            await asyncio.sleep(0.05)  # 50ms poll interval

    if debug:
        # Log after completion (schedule as background task)
        async def _log_after():
            await done_flag.wait()
            duration_ms = int((time.time() - start) * 1000)
            event_types = [e.type for e in events]
            logger.debug(
                "Response: %d events in %dms — %s",
                len(events), duration_ms, ", ".join(event_types),
            )

        asyncio.ensure_future(_log_after())

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/api/health")
async def health(request: Request) -> dict[str, Any]:
    """Health check endpoint — also exposes security status."""
    mycelos = request.app.state.mycelos
    uptime = time.time() - request.app.state.start_time
    bind_host = getattr(request.app.state, "bind_host", "127.0.0.1")
    password_protected = getattr(request.app.state, "password_protected", False)
    network_exposed = bind_host not in _LOCALHOST_ADDRS
    return {
        "status": "ok",
        "uptime_seconds": round(uptime, 1),
        "generation_id": mycelos.config.get_active_generation_id(),
        "scheduler": getattr(request.app.state, "scheduler_running", False),
        "user": getattr(request.app.state, "default_user", {"id": "default", "name": "Default User"}),
        "security": {
            "bind_host": bind_host,
            "network_exposed": network_exposed,
            "password_protected": password_protected,
            "client_ip": request.client.host if request.client else None,
            "warning": (
                "Network access enabled without password protection"
                if network_exposed and not password_protected
                else None
            ),
        },
    }
