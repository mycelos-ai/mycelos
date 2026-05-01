"""Sessions endpoints — list, create, messages, update, download, attachments."""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from mycelos.gateway.routers._helpers import (
    SessionUpdateRequest,
    render_session_markdown,
    resolve_user_id,
)

router = APIRouter()


@router.get("/api/sessions")
async def sessions(request: Request) -> list[dict[str, Any]]:
    """List recent sessions."""
    mycelos = request.app.state.mycelos
    return mycelos.session_store.list_sessions()


@router.post("/api/sessions")
async def create_session(request: Request) -> dict[str, Any]:
    """Create a new chat session."""
    mycelos = request.app.state.mycelos
    user_id = resolve_user_id(request)
    session_id = mycelos.session_store.create_session(user_id=user_id)
    return {"session_id": session_id}


@router.get("/api/sessions/{session_id}/messages")
async def session_messages(request: Request, session_id: str) -> dict[str, Any]:
    """Load messages for a specific session."""
    mycelos = request.app.state.mycelos
    if not mycelos.session_store.session_exists(session_id):
        return JSONResponse({"error": "Session not found"}, status_code=404)
    messages = mycelos.session_store.load_messages(session_id)
    return {"session_id": session_id, "messages": messages}


@router.patch("/api/sessions/{session_id}")
async def update_session(
    request: Request, session_id: str, body: SessionUpdateRequest,
) -> dict[str, Any]:
    """Update session title/topic."""
    mycelos = request.app.state.mycelos
    ok = mycelos.session_store.update_session(
        session_id, title=body.title, topic=body.topic,
    )
    if not ok:
        return JSONResponse({"error": "Session not found"}, status_code=404)
    result: dict[str, Any] = {"session_id": session_id}
    if body.title is not None:
        result["title"] = body.title
    if body.topic is not None:
        result["topic"] = body.topic
    return result


@router.get("/api/sessions/{session_id}/download")
async def download_session(
    request: Request, session_id: str, format: str = "markdown",
) -> Any:
    """Download a session in jsonl, json, or markdown format."""
    from starlette.responses import Response as StarletteResponse
    mycelos = request.app.state.mycelos
    events = mycelos.session_store.load_all_events(session_id)

    if format == "jsonl":
        body = "\n".join(json.dumps(e, default=str) for e in events)
        return StarletteResponse(
            content=body,
            media_type="application/x-ndjson",
            headers={"Content-Disposition": f'attachment; filename="{session_id}.jsonl"'},
        )
    elif format == "json":
        body = json.dumps(events, indent=2, default=str)
        return StarletteResponse(
            content=body,
            media_type="application/json",
            headers={"Content-Disposition": f'attachment; filename="{session_id}.json"'},
        )
    elif format == "markdown":
        body = render_session_markdown(session_id, events, mycelos.session_store)
        return StarletteResponse(
            content=body,
            media_type="text/markdown",
            headers={"Content-Disposition": f'attachment; filename="{session_id}.md"'},
        )
    else:
        return StarletteResponse(
            content=json.dumps({"error": "invalid format"}),
            status_code=400,
            media_type="application/json",
        )


@router.get("/api/sessions/{session_id}/attachments/{filename:path}")
async def serve_session_attachment(
    request: Request, session_id: str, filename: str,
) -> Any:
    """Serve a file from the session's attachment folder.

    Path-traversal-safe: session_id is sanitized and the resolved
    filename must live inside the session's attachments folder. Used
    by the chat preview card to render images / link to PDFs.
    """
    from starlette.responses import FileResponse
    from mycelos.files.inbox import sanitize_filename
    mycelos = request.app.state.mycelos
    sessions_root = (mycelos.data_dir / "sessions").resolve()
    safe_session_id = sanitize_filename(session_id)
    base = (sessions_root / safe_session_id / "attachments").resolve()
    # Defense in depth: verify base is under sessions_root.
    if not base.is_relative_to(sessions_root):
        return JSONResponse({"error": "path traversal blocked"}, status_code=400)
    target = (base / filename).resolve()
    try:
        target.relative_to(base)
    except ValueError:
        return JSONResponse({"error": "path traversal blocked"}, status_code=400)
    if not target.is_file():
        return JSONResponse({"error": "not found"}, status_code=404)
    return FileResponse(str(target), filename=target.name)
