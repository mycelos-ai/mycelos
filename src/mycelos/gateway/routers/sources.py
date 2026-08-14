"""Source endpoints — where a source may file, and under which rule."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from mycelos.gateway.routers._helpers import resolve_user_id
from mycelos.knowledge.connector_ingest import INGEST_SOURCES
from mycelos.knowledge.source_attachment import (
    SourceAttachmentService, permitted_paths,
)

router = APIRouter()


def _service(request: Request) -> SourceAttachmentService:
    mycelos = request.app.state.mycelos
    return SourceAttachmentService(
        mycelos.storage,
        notifier=getattr(mycelos, "config_notifier", None),
        audit=mycelos.audit,
    )


def _unknown_source(source_id: str) -> JSONResponse | None:
    """Fail closed: a typo in source_id must never scope silently to nothing.

    INGEST_SOURCES is the single registry of ingest source ids (used by the
    scheduler and the manual-ingest endpoint alike) — the authority here too.
    """
    if source_id not in INGEST_SOURCES:
        return JSONResponse({"error": "unknown source"}, status_code=422)
    return None


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
    if (err := _unknown_source(source_id)) is not None:
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
    if (err := _unknown_source(source_id)) is not None:
        return err
    body = await request.json()
    rule_text = (body or {}).get("rule_text")
    if rule_text is None:
        return JSONResponse({"error": "rule_text required"}, status_code=422)
    _service(request).set_rule(source_id, rule_text, resolve_user_id(request))
    return {"ok": True}
