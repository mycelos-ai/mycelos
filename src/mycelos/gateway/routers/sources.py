"""Source endpoints — where a source may file, and under which rule."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from mycelos.gateway.routers._helpers import resolve_user_id
from mycelos.knowledge.connector_ingest import INGEST_SOURCES
from mycelos.knowledge.source_attachment import (
    CHAT_SOURCE_KEY, LOCAL_SOURCE_PREFIX, SourceAttachmentService, permitted_paths,
)

router = APIRouter()


def _service(request: Request) -> SourceAttachmentService:
    mycelos = request.app.state.mycelos
    return SourceAttachmentService(
        mycelos.storage,
        notifier=getattr(mycelos, "config_notifier", None),
        audit=mycelos.audit,
    )


def _unknown_source(source_id: str, request: Request) -> JSONResponse | None:
    """Fail closed: a typo in source_id must never scope silently to nothing.

    One source model: connector ids come from INGEST_SOURCES (the registry
    the scheduler and the manual-ingest endpoint use), local sources are
    ``local:<id>`` and must belong to the requesting user, and ``chat`` is
    the key for notes captured in the chat.
    """
    if source_id in INGEST_SOURCES or source_id == CHAT_SOURCE_KEY:
        return None
    if source_id.startswith(LOCAL_SOURCE_PREFIX):
        local_id = source_id[len(LOCAL_SOURCE_PREFIX):]
        try:
            row = request.app.state.mycelos.storage.fetchone(
                "SELECT id FROM local_sources WHERE id=? AND user_id=?",
                (local_id, resolve_user_id(request)),
            )
        except Exception:
            row = None  # fail closed
        if local_id and row:
            return None
    return JSONResponse({"error": "unknown source"}, status_code=422)


@router.get("/api/sources/{source_id}")
def get_source(source_id: str, request: Request) -> Any:
    # No validation here: a known-but-unconfigured source is a legitimate
    # read (empty attachments, empty rule), not an error.
    mycelos = request.app.state.mycelos
    user_id = resolve_user_id(request)
    svc = _service(request)
    attachments = svc.list_attachments(source_id, user_id)
    all_topics = [t.get("path", "") for t in mycelos.knowledge_base.list_topics(limit=500)]
    return {
        "source_id": source_id,
        "attachments": [
            {
                "topic_path": path,
                # How many folders this attachment opens beneath itself —
                # the UI's "covers N folders beneath".
                "covers": max(0, len(permitted_paths([path], all_topics)) - 1),
            }
            for path in attachments
        ],
        "rule_text": svc.get_rule(source_id, user_id),
    }


@router.post("/api/sources/{source_id}/attachments")
async def attach(source_id: str, request: Request) -> Any:
    if (err := _unknown_source(source_id, request)) is not None:
        return err
    body = await request.json()
    topic_path = (body or {}).get("topic_path")
    if topic_path is None:
        return JSONResponse({"error": "topic_path required"}, status_code=422)
    mycelos = request.app.state.mycelos
    if topic_path != "":
        known = mycelos.storage.fetchone(
            "SELECT path FROM knowledge_notes WHERE path=? AND type='topic'",
            (topic_path,),
        )
        if not known:
            # Fail closed: a typo must never become a silent attachment.
            return JSONResponse({"error": "unknown topic"}, status_code=422)
    _service(request).attach(source_id, topic_path, resolve_user_id(request))
    return {"ok": True, "source_id": source_id, "topic_path": topic_path}


@router.delete("/api/sources/{source_id}/attachments")
async def detach(source_id: str, request: Request) -> Any:
    body = await request.json()
    topic_path = (body or {}).get("topic_path")
    if topic_path is None:
        return JSONResponse({"error": "topic_path required"}, status_code=422)
    _service(request).detach(source_id, topic_path, resolve_user_id(request))
    return {"ok": True}


@router.put("/api/sources/{source_id}/rule")
async def set_rule(source_id: str, request: Request) -> Any:
    if (err := _unknown_source(source_id, request)) is not None:
        return err
    body = await request.json()
    rule_text = (body or {}).get("rule_text")
    if rule_text is None:
        return JSONResponse({"error": "rule_text required"}, status_code=422)
    _service(request).set_rule(source_id, rule_text, resolve_user_id(request))
    return {"ok": True}
