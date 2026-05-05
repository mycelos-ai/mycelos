"""Media endpoints — reload, transcribe, audio chat, and file upload."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Form, Request, UploadFile
from fastapi.responses import JSONResponse
from starlette.responses import StreamingResponse

from mycelos.gateway.routers._helpers import (
    resolve_user_id,
    sse_error,
)

logger = logging.getLogger("mycelos.gateway")

router = APIRouter()


@router.post("/api/reload")
async def reload(request: Request) -> dict[str, Any]:
    """Reload MCP connectors and channel config.

    Call this after adding/removing connectors or changing channel config.
    Re-discovers MCP tools without full gateway restart.
    Only accessible from localhost (enforced by LocalhostMiddleware).
    """
    from mycelos.gateway.server import _start_mcp_connectors

    api = request.app
    mycelos = api.state.mycelos
    debug = getattr(api.state, "debug", False)

    # Disconnect existing MCP servers
    try:
        mycelos.mcp_manager.disconnect_all()
    except Exception:
        pass

    # Restart MCP connectors
    _start_mcp_connectors(mycelos, debug=debug)

    # Report what's running now
    mcp_tools = mycelos.mcp_manager.list_tools() if mycelos._mcp_manager else []
    connected = mycelos.mcp_manager.list_connected() if mycelos._mcp_manager else []

    mycelos.audit.log("gateway.reloaded", details={
        "mcp_connectors": connected,
        "mcp_tools": len(mcp_tools),
    })

    return {
        "status": "reloaded",
        "mcp_connectors": connected,
        "mcp_tools": len(mcp_tools),
    }


@router.post("/api/transcribe")
async def transcribe_audio(request: Request, audio: UploadFile) -> dict[str, Any]:
    """Transcribe audio and return text (no chat processing)."""
    mycelos = request.app.state.mycelos

    if not getattr(mycelos, "proxy_client", None):
        return JSONResponse(
            status_code=503,
            content={"error": "Voice transcription not available"},
        )

    audio_bytes = await audio.read()
    if len(audio_bytes) > 25 * 1024 * 1024:
        return JSONResponse(
            status_code=413,
            content={"error": "Audio file too large. Maximum size is 25MB."},
        )

    try:
        result = mycelos.proxy_client.stt_transcribe(
            audio=audio_bytes,
            filename=audio.filename or "audio.ogg",
            user_id=resolve_user_id(request),
        )
    except Exception as exc:
        logger.error("STT transcription error: %s", exc, exc_info=True)
        return JSONResponse(
            status_code=500,
            content={"error": "Transcription failed"},
        )

    text = (result.get("text") or "").strip()
    return {"text": text}


@router.post("/api/audio")
async def handle_audio(
    request: Request,
    audio: UploadFile,
    session_id: str = "",
) -> StreamingResponse:
    """Accept audio upload, transcribe via SecurityProxy, process as chat message."""
    from mycelos.chat.events import session_event, error_event, done_event

    mycelos = request.app.state.mycelos
    service = request.app.state.chat_service
    user_id = resolve_user_id(request)

    # Check proxy client availability
    if not getattr(mycelos, "proxy_client", None):
        async def no_proxy_stream():
            yield session_event("").to_sse()
            yield error_event("Voice transcription not available").to_sse()
            yield done_event().to_sse()
        return StreamingResponse(no_proxy_stream(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache"})

    # Create session if not provided
    if not session_id:
        session_id = service.create_session(user_id=user_id)

    # Read audio bytes and check size (max 25MB)
    audio_bytes = await audio.read()
    max_size = 25 * 1024 * 1024
    if len(audio_bytes) > max_size:
        async def size_error_stream():
            yield session_event(session_id).to_sse()
            yield error_event("Audio file too large. Maximum size is 25MB.").to_sse()
            yield done_event().to_sse()
        return StreamingResponse(size_error_stream(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache"})

    # Transcribe via proxy
    try:
        result = mycelos.proxy_client.stt_transcribe(
            audio=audio_bytes,
            filename=audio.filename or "audio.ogg",
            user_id=user_id,
        )
    except Exception as exc:
        logger.error("STT transcription error: %s", exc, exc_info=True)
        async def stt_error_stream():
            yield session_event(session_id).to_sse()
            yield error_event("Transcription failed. Check server logs for details.").to_sse()
            yield done_event().to_sse()
        return StreamingResponse(stt_error_stream(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache"})

    text = (result.get("text") or "").strip()
    if not text:
        async def empty_stream():
            yield session_event(session_id).to_sse()
            yield error_event("Could not understand the audio.").to_sse()
            yield done_event().to_sse()
        return StreamingResponse(empty_stream(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache"})

    # Process voice message through chat service
    voice_message = f"[Voice] {text}"
    try:
        events = service.handle_message(
            voice_message,
            session_id=session_id,
            user_id=user_id,
        )
    except Exception as exc:
        logger.error("Chat handler error (audio route): %s", exc, exc_info=True)
        events = [error_event("An internal error occurred. Check server logs for details."), done_event()]

    all_events = [session_event(session_id)] + events

    async def audio_stream():
        for event in all_events:
            yield event.to_sse()

    return StreamingResponse(
        audio_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/api/upload")
async def handle_upload(
    request: Request,
    file: UploadFile,
    session_id: str = Form(""),
) -> StreamingResponse:
    """Save an uploaded file to the session's attachment folder and
    emit a file-attached SSE event so the chat UI can render its
    preview card. The file rides along in every subsequent LLM call
    for this session via ChatService Multi-Part build — no marker,
    no auto-ingest.
    """
    from mycelos.chat.events import (
        error_event, done_event, session_event, file_attached_event,
        system_response_event,
    )
    from mycelos.files.session_attachments import (
        SessionAttachmentStore, SIZE_CAPS_BYTES, content_kind,
    )
    from mycelos.i18n import t

    service = request.app.state.chat_service
    mycelos = request.app.state.mycelos
    user_id = resolve_user_id(request)

    if not session_id:
        session_id = service.create_session(user_id=user_id)

    file_bytes = await file.read()
    filename = file.filename or "unnamed"
    kind = content_kind(Path(filename))

    if kind == "unsupported":
        return sse_error(session_id, f"Unsupported file type for chat attachments: {filename}")

    # Map content_kind to SIZE_CAPS_BYTES key ("document" → "pdf").
    _cap_key = {"document": "pdf", "image": "image", "text": "text"}.get(kind, kind)
    cap = SIZE_CAPS_BYTES.get(_cap_key, 0)
    if cap and len(file_bytes) > cap:
        return sse_error(session_id, f"File too large ({len(file_bytes)} bytes > {cap} for {kind})")

    store = SessionAttachmentStore(mycelos.data_dir / "sessions")
    try:
        saved = store.save(session_id, file_bytes, filename)
    except ValueError as e:
        return sse_error(session_id, str(e))

    try:
        mycelos.audit.log(
            "chat.attachment_uploaded",
            details={
                "session_id": session_id,
                "filename": saved.name,
                "kind": kind,
                "size": len(file_bytes),
            },
            user_id=user_id,
        )
    except Exception:
        logger.exception("Failed to audit attachment upload")

    # Map the internal kind to the frontend's preview-card discriminator.
    ui_kind = {"document": "pdf", "image": "image", "text": "other"}.get(kind, "other")
    preview = file_attached_event(
        filename=saved.name,
        url=f"/api/sessions/{session_id}/attachments/{saved.name}",
        kind=ui_kind,
        size=len(file_bytes),
    )

    confirmation = system_response_event(
        t("chat.attachment_ready", filename=saved.name)
    )

    async def stream():
        yield session_event(session_id).to_sse()
        yield preview.to_sse()
        yield confirmation.to_sse()
        yield done_event().to_sse()
    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
