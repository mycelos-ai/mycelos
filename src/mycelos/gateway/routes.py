"""Gateway HTTP routes — chat, health, config."""

from __future__ import annotations

import json
import logging
import re
import threading
import time
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Form, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import StreamingResponse

from mycelos.chat.events import ChatEvent
from mycelos.gateway.routers._helpers import (
    ChatRequest,
    ConfirmRequest,
    ConnectorAddRequest,
    CredentialAddRequest,
    SessionUpdateRequest,
    get_doc as _get_doc,
    list_docs as _list_docs,
    parse_frontmatter as _parse_frontmatter,
    render_session_markdown as _render_session_markdown,
    resolve_user_id as _resolve_user_id,
    sse_error as _sse_error,
)

logger = logging.getLogger("mycelos.gateway")

_LOCALHOST_ADDRS = ("127.0.0.1", "::1")


class LocalhostMiddleware(BaseHTTPMiddleware):
    """Restrict /api/* routes to localhost unless the server binds to 0.0.0.0."""

    async def dispatch(self, request: Request, call_next):  # type: ignore[override]
        # Only gate /api/* paths
        if request.url.path.startswith("/api/"):
            bind_host = getattr(request.app.state, "bind_host", "127.0.0.1")
            # If bound to localhost only, enforce the check
            if bind_host in _LOCALHOST_ADDRS:
                client_host = request.client.host if request.client else None
                if client_host not in _LOCALHOST_ADDRS:
                    return JSONResponse(
                        status_code=403,
                        content={"error": "API is only accessible from localhost"},
                    )
        return await call_next(request)


# Methods that a malicious cross-origin site could use to mutate state.
# GET/HEAD/OPTIONS are considered safe by the HTTP spec (server must not
# mutate state on them), so we only gate the dangerous verbs. OPTIONS
# specifically must pass through because browsers use it for CORS preflight.
_CSRF_GUARDED_METHODS = {"POST", "PUT", "PATCH", "DELETE"}

# Allowed Origin values compared against the Origin / Referer header.
# Built from bind host + env override at app startup and stashed on
# `app.state.csrf_allowed_origins` — this set can stay empty at import
# time and still match correctly at request time.
_LOCAL_ORIGIN_PREFIXES = ("http://localhost:", "http://127.0.0.1:", "http://[::1]:")


class CSRFMiddleware(BaseHTTPMiddleware):
    """Reject cross-origin browser requests that change state.

    Threat: a user has Mycelos running on localhost (or a LAN IP) and
    opens a malicious website in the same browser. Without this
    middleware, that page's JavaScript can POST to /api/connectors/gmail/
    tools/search_threads/call and exfiltrate email through the user's
    own open session — classic CSRF.

    Defense: for POST / PUT / PATCH / DELETE requests we require either
    - no `Origin` or `Referer` header at all (curl, mycelos CLI, server-
      to-server scripts — not browser-initiated), or
    - an `Origin` / `Referer` whose scheme+host+port matches one of the
      allowed origins (the gateway's own bind host + anything in
      MYCELOS_ALLOWED_ORIGINS).

    GET / HEAD / OPTIONS are passed through unchanged. OPTIONS
    specifically MUST pass so CORS preflight works.
    """

    def __init__(self, app, allowed_origins: set[str] | None = None) -> None:
        super().__init__(app)
        # Normalize to scheme://host[:port] with no trailing slash.
        self._static_allowed = {o.rstrip("/") for o in (allowed_origins or set())}

    async def dispatch(self, request: Request, call_next):  # type: ignore[override]
        if request.method not in _CSRF_GUARDED_METHODS:
            return await call_next(request)
        if not request.url.path.startswith("/api/"):
            return await call_next(request)

        origin = request.headers.get("origin") or ""
        referer = request.headers.get("referer") or ""

        # No Origin AND no Referer → not a browser-initiated request.
        # curl, httpx, the Mycelos CLI, etc. — allow through. This is
        # the intentional escape hatch for CLI / scripting; attackers
        # can't strip Origin from a real browser fetch.
        if not origin and not referer:
            return await call_next(request)

        allowed = set(self._static_allowed)
        allowed.update(getattr(request.app.state, "csrf_allowed_origins", set()) or set())

        # The Host header tells us the URL the browser is actually
        # talking to. If Origin matches that host exactly, the request
        # is same-origin by definition — no matter what we bind to or
        # what hostname our container thinks it has. This is the only
        # reliable check inside Docker / behind a reverse proxy where
        # `socket.gethostname()` returns a synthetic id.
        host_header = (request.headers.get("host") or "").strip().lower()

        def _origin_ok(value: str) -> bool:
            if not value:
                return False
            # Normalize: take scheme://host:port, drop path.
            try:
                from urllib.parse import urlparse
                p = urlparse(value)
                if not p.scheme or not p.netloc:
                    return False
                normalized = f"{p.scheme}://{p.netloc}"
                netloc_lower = p.netloc.lower()
            except Exception:
                return False
            if normalized in allowed:
                return True
            # Same-origin: Origin's host:port matches the request's Host
            # header. Catches `http://pi5.local:9100` posts to itself.
            if host_header and netloc_lower == host_header:
                return True
            # Always-accept localhost regardless of port — single-process
            # dev setup cycles ports a lot.
            return any(normalized.startswith(p) for p in _LOCAL_ORIGIN_PREFIXES)

        # Origin wins over Referer (Origin is set by the browser on
        # cross-origin POST/fetch and is harder for a page to forge).
        if origin:
            if not _origin_ok(origin):
                return JSONResponse(
                    {"error": "Cross-origin request blocked (CSRF)"},
                    status_code=403,
                )
        elif referer:
            if not _origin_ok(referer):
                return JSONResponse(
                    {"error": "Cross-origin request blocked (CSRF)"},
                    status_code=403,
                )

        return await call_next(request)


def setup_routes(api: FastAPI) -> None:
    """Register all gateway routes."""

    from mycelos.gateway.routers.chat import router as chat_router
    api.include_router(chat_router)

    from mycelos.gateway.routers.config import router as config_router
    api.include_router(config_router)

    from mycelos.gateway.routers.docs import router as docs_router
    api.include_router(docs_router)

    from mycelos.gateway.routers.cost import router as cost_router
    api.include_router(cost_router)

    from mycelos.gateway.routers.telegram_webhook import router as telegram_webhook_router
    api.include_router(telegram_webhook_router)

    from mycelos.gateway.routers.schedules import router as schedules_router
    api.include_router(schedules_router)

    from mycelos.gateway.routers.channels import router as channels_router
    api.include_router(channels_router)

    from mycelos.gateway.routers.agents import router as agents_router
    api.include_router(agents_router)

    from mycelos.gateway.routers.admin import router as admin_router
    api.include_router(admin_router)

    @api.get("/api/audit/activity")
    async def audit_activity(
        level: str = "noteworthy",
        since: str | None = "24h",
        limit: int = 100,
    ) -> dict[str, Any]:
        """Recent audit events classified for the Doctor Activity panel.

        level:
            "suspicious"  — only security-relevant events (tamper, blocks, denies, …)
            "noteworthy"  — everything except high-volume noise (default)
            "all"         — raw feed, includes reminder.tick etc.

        since: shorthand like 30m, 1h, 24h, 7d (default 24h). `None` or "all"
        disables the time filter.

        Returns {events: [...], counts: {suspicious, noteworthy, all}}.
        The counts let the UI render tab badges without a second roundtrip.
        """
        import json as _json
        from datetime import datetime, timedelta, timezone
        import re as _re

        mycelos = api.state.mycelos
        limit = max(1, min(limit, 500))

        cutoff: str | None = None
        if since and since != "all":
            match = _re.match(r"^(\d+)([smhd])$", since.strip().lower())
            if not match:
                return JSONResponse(
                    {"error": "since must look like 30m, 1h, 24h, 7d or 'all'"},
                    status_code=400,
                )
            amount = int(match.group(1))
            unit = match.group(2)
            delta = {
                "s": timedelta(seconds=amount),
                "m": timedelta(minutes=amount),
                "h": timedelta(hours=amount),
                "d": timedelta(days=amount),
            }[unit]
            cutoff = (datetime.now(timezone.utc) - delta).strftime("%Y-%m-%dT%H:%M:%fZ")

        # Fetch a larger window and classify in Python — the event_type list is
        # small and this keeps the SQL simple.
        from mycelos.audit_patterns import (
            NOISY_EVENT_TYPES,
            SUSPICIOUS_EVENT_SUFFIXES,
            SUSPICIOUS_EVENT_TYPES,
            is_noisy,
            is_suspicious,
        )

        if cutoff:
            rows = mycelos.storage.fetchall(
                "SELECT * FROM audit_events WHERE created_at >= ? ORDER BY created_at DESC LIMIT ?",
                (cutoff, 2000),
            )
        else:
            rows = mycelos.storage.fetchall(
                "SELECT * FROM audit_events ORDER BY created_at DESC LIMIT ?",
                (2000,),
            )

        events_all: list[dict[str, Any]] = []
        count_suspicious = 0
        count_noteworthy = 0
        for r in rows:
            event = dict(r)
            if event.get("details") and isinstance(event["details"], str):
                try:
                    event["details"] = _json.loads(event["details"])
                except Exception:
                    pass
            etype = event["event_type"]
            event["suspicious"] = is_suspicious(etype)
            event["noisy"] = is_noisy(etype)
            if event["suspicious"]:
                count_suspicious += 1
            if not event["noisy"]:
                count_noteworthy += 1
            events_all.append(event)

        if level == "suspicious":
            filtered = [e for e in events_all if e["suspicious"]]
        elif level == "all":
            filtered = events_all
        else:  # "noteworthy" — default
            filtered = [e for e in events_all if not e["noisy"]]

        return {
            "events": filtered[:limit],
            "counts": {
                "suspicious": count_suspicious,
                "noteworthy": count_noteworthy,
                "all": len(events_all),
            },
            "level": level,
            "since": since,
        }

    @api.get("/api/audit")
    async def audit_events(limit: int = 10, event_type: str | None = None) -> list[dict[str, Any]]:
        """Return recent audit events, newest first."""
        import json as _json
        mycelos = api.state.mycelos
        limit = min(limit, 100)

        if event_type:
            rows = mycelos.storage.fetchall(
                "SELECT * FROM audit_events WHERE event_type LIKE ? ORDER BY created_at DESC LIMIT ?",
                (event_type + "%", limit),
            )
        else:
            rows = mycelos.storage.fetchall(
                "SELECT * FROM audit_events ORDER BY created_at DESC LIMIT ?",
                (limit,),
            )

        result = []
        for row in rows:
            entry = dict(row)
            if entry.get("details") and isinstance(entry["details"], str):
                try:
                    entry["details"] = _json.loads(entry["details"])
                except (ValueError, TypeError):
                    pass
            result.append(entry)
        return result

    @api.get("/api/sessions")
    async def sessions() -> list[dict[str, Any]]:
        """List recent sessions."""
        mycelos = api.state.mycelos
        return mycelos.session_store.list_sessions()

    @api.post("/api/sessions")
    async def create_session(http_request: Request) -> dict[str, Any]:
        """Create a new chat session."""
        mycelos = api.state.mycelos
        user_id = _resolve_user_id(http_request)
        session_id = mycelos.session_store.create_session(user_id=user_id)
        return {"session_id": session_id}

    @api.get("/api/sessions/{session_id}/messages")
    async def session_messages(session_id: str) -> dict[str, Any]:
        """Load messages for a specific session."""
        mycelos = api.state.mycelos
        if not mycelos.session_store.session_exists(session_id):
            return JSONResponse({"error": "Session not found"}, status_code=404)
        messages = mycelos.session_store.load_messages(session_id)
        return {"session_id": session_id, "messages": messages}

    @api.patch("/api/sessions/{session_id}")
    async def update_session(session_id: str, body: SessionUpdateRequest) -> dict[str, Any]:
        """Update session title/topic."""
        mycelos = api.state.mycelos
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

    @api.get("/api/sessions/{session_id}/download")
    async def download_session(
        session_id: str, format: str = "markdown"
    ) -> Any:
        """Download a session in jsonl, json, or markdown format."""
        from starlette.responses import Response as StarletteResponse
        mycelos = api.state.mycelos
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
            body = _render_session_markdown(session_id, events, mycelos.session_store)
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

    @api.get("/api/knowledge/notes")
    async def knowledge_notes(
        query: str | None = None,
        type: str | None = None,
        status: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """List/search notes for the web knowledge view."""
        kb = api.state.mycelos.knowledge_base
        if query:
            return kb.search(query=query, type=type, limit=limit)
        return kb.list_notes(type=type, status=status, limit=limit)

    @api.post("/api/knowledge/notes")
    async def knowledge_create_note(request: Request) -> Any:
        """Create a note via Quick Capture.

        Runs the deterministic DE+EN parser over the payload, applies
        deterministic bucketing, and delegates to KnowledgeService.write.
        Caller-supplied fields always win over parser defaults.
        """
        from mycelos.knowledge.parse_note import parse_note_text
        from mycelos.knowledge.service import bucket_note

        mycelos = api.state.mycelos
        kb = mycelos.knowledge_base
        body = await request.json()

        title = body.get("title")
        if not title or not isinstance(title, str):
            return JSONResponse({"error": "title is required"}, status_code=422)

        content = body.get("content") or ""
        parsed = parse_note_text(f"{title}\n{content}")

        due = body.get("due") if "due" in body else parsed["due"]
        reminder = bool(body.get("reminder") if "reminder" in body else parsed["reminder"])
        tags = body.get("tags") if "tags" in body else parsed["tags"]
        note_type = body.get("type") or parsed["type"]
        # If a due date or reminder is set but no explicit type was given,
        # promote to "task" so the file lands under tasks/ (path is derived
        # from note.type, not parent_path).
        if not body.get("type") and note_type == "note" and (due or reminder):
            note_type = "task"

        # Legacy callers pass `topic`; new callers pass `parent_path`.
        parent = body.get("parent_path") or body.get("topic") or bucket_note(
            {"parent_path": "", "reminder": reminder, "due": due}
        )

        path = kb.write(
            title=title,
            content=content,
            type=note_type,
            tags=tags or [],
            due=due,
            reminder=reminder,
            topic=parent,
        )

        try:
            mycelos.audit.log(
                "knowledge.note.created",
                user_id=_resolve_user_id(request),
                details={"path": path, "source": "quick_capture"},
            )
        except Exception:
            # Audit must never break the write path.
            pass

        return {
            "path": path,
            "parent_path": parent,
            "type": note_type,
            "due": due,
            "reminder": reminder,
            "tags": tags or [],
            "organizer_state": "pending",
        }

    @api.post("/api/knowledge/enhance")
    async def knowledge_enhance(request: Request) -> dict[str, Any]:
        """AI-enhance a note — expand, improve, or organize content using a cheap model."""
        mycelos = api.state.mycelos
        body = await request.json()
        content = body.get("content", "")
        action = body.get("action", "improve")  # improve, expand, summarize, organize

        prompts = {
            "improve": "Improve this note: fix grammar, clarify unclear parts, keep the same language. Return only the improved text.",
            "expand": "Expand this note with more detail and examples. Keep the same language and style. Return only the expanded text.",
            "summarize": "Summarize this note concisely. Keep the same language. Return only the summary.",
            "organize": "Organize this note with clear headings, bullet points, and structure. Keep the same language. Return only the organized text.",
        }
        prompt = prompts.get(action, prompts["improve"])

        try:
            response = mycelos.llm.complete(
                [
                    {"role": "system", "content": prompt},
                    {"role": "user", "content": content[:4000]},
                ],
                model=mycelos.resolve_cheapest_model(),
            )
            return {"content": response.content, "tokens": response.total_tokens, "cost": response.cost}
        except Exception as e:
            return JSONResponse({"error": str(e)}, status_code=500)

    @api.put("/api/knowledge/notes/{path:path}")
    async def knowledge_update_note(path: str, request: Request) -> dict[str, Any]:
        """Update an existing note (content, status, tags, priority, parent_path, organizer_state, archive)."""
        mycelos = api.state.mycelos
        kb = mycelos.knowledge_base
        body = await request.json()

        # Content update (on disk + DB)
        if "content" in body:
            result = kb.update(path, content=body["content"])

        # Move to a different topic
        if "parent_path" in body:
            new_parent = body["parent_path"]
            kb.move_to_topic(path, new_parent)
            try:
                mycelos.audit.log(
                    "knowledge.note.moved",
                    user_id=_resolve_user_id(request),
                    details={"path": path, "target": new_parent},
                )
            except Exception:
                pass

        # Organizer state override (for reclassify)
        if "organizer_state" in body:
            mycelos.storage.execute(
                "UPDATE knowledge_notes SET organizer_state=? WHERE path=?",
                (body["organizer_state"], path),
            )

        # Status update (open/done/in-progress)
        if "status" in body:
            kb.update(path, status=body["status"])

        # Tags update
        if "tags" in body:
            kb.update(path, tags=body["tags"])

        # Priority update
        if "priority" in body:
            kb.update(path, priority=int(body["priority"]))

        # Archive shortcut
        if body.get("archive"):
            kb.archive_note(path)
            try:
                mycelos.audit.log(
                    "knowledge.note.archived",
                    user_id=_resolve_user_id(request),
                    details={"path": path},
                )
            except Exception:
                pass

        return {"status": "updated", "path": path}

    @api.get("/api/knowledge/notes/{path:path}")
    async def knowledge_note(path: str) -> dict[str, Any]:
        """Fetch a single note by path."""
        kb = api.state.mycelos.knowledge_base
        note = kb.read(path)
        if not note:
            return JSONResponse({"error": "not_found", "path": path}, status_code=404)
        return note

    @api.get("/api/knowledge/graph")
    async def knowledge_graph() -> dict[str, Any]:
        """Return note graph (nodes + links) for web visualization."""
        kb = api.state.mycelos.knowledge_base
        return kb.get_graph_data()

    @api.get("/api/knowledge/topics")
    async def knowledge_topics() -> list[dict[str, Any]]:
        """List top-level topic notes with child counts."""
        kb = api.state.mycelos.knowledge_base
        topics = kb.list_topics(top_level_only=True)
        for t in topics:
            children = kb.list_children(t["path"])
            t["child_count"] = len(children)
            t["open_tasks"] = sum(1 for c in children if c.get("type") == "task" and c.get("status") in ("open", "in-progress"))
        return topics

    @api.post("/api/knowledge/topics")
    async def knowledge_create_topic(request: Request) -> dict[str, Any]:
        """Create a new topic. Body: {name, tags?, parent?}."""
        mycelos = api.state.mycelos
        kb = mycelos.knowledge_base
        body = await request.json()
        name = body.get("name")
        if not name or not isinstance(name, str):
            return JSONResponse({"error": "name is required"}, status_code=422)
        tags = body.get("tags") or []
        parent = body.get("parent") or None
        path = kb.create_topic(
            name,
            tags=tags if isinstance(tags, list) else [],
            parent=parent,
        )
        try:
            mycelos.audit.log(
                "knowledge.topic.created",
                user_id=_resolve_user_id(request),
                details={"path": path, "name": name},
            )
        except Exception:
            pass
        return {"path": path, "name": name}

    @api.post("/api/knowledge/topics/{path:path}/rename")
    async def knowledge_rename_topic(path: str, request: Request) -> dict[str, Any]:
        """Rename a topic. Body: {name: "New Name"}."""
        mycelos = api.state.mycelos
        body = await request.json()
        name = body.get("name", "").strip()
        if not name:
            return JSONResponse({"error": "name is required"}, status_code=422)
        kb = mycelos.knowledge_base
        new_path = kb.rename_topic(path, name)
        try:
            mycelos.audit.log(
                "knowledge.topic.renamed",
                user_id=_resolve_user_id(request),
                details={"old_path": path, "new_path": new_path, "name": name},
            )
        except Exception:
            pass
        return {"status": "renamed", "old_path": path, "new_path": new_path, "name": name}

    @api.get("/api/knowledge/topics/{path:path}/children")
    async def knowledge_topic_children(path: str) -> list[dict[str, Any]]:
        """List notes belonging to a topic."""
        kb = api.state.mycelos.knowledge_base
        return kb.list_children(path)

    @api.post("/api/knowledge/notes/{path:path}/done")
    async def knowledge_note_done(path: str) -> dict[str, Any]:
        """Mark a task as done."""
        kb = api.state.mycelos.knowledge_base
        success = kb.mark_done(path)
        if not success:
            return JSONResponse({"error": "not_found", "path": path}, status_code=404)
        return {"status": "done"}

    @api.post("/api/knowledge/notes/{path:path}/remind")
    async def knowledge_note_remind(path: str, request: Request) -> dict[str, Any]:
        """Set a reminder on a note.

        Body: ``{"when": "<due date>", "remind_at": "<ISO datetime>"}``.
        ``remind_at`` is optional — omit it to fire "sometime on due day".
        """
        body = await request.json()
        kb = api.state.mycelos.knowledge_base
        success = kb.set_reminder(
            path,
            due=body.get("when", ""),
            remind_at=body.get("remind_at") or None,
        )
        if not success:
            return JSONResponse({"error": "not_found", "path": path}, status_code=404)
        return {"status": "reminder_set"}

    @api.post("/api/knowledge/notes/{path:path}/move")
    async def knowledge_note_move(path: str, request: Request) -> dict[str, Any]:
        """Move a note to a different topic."""
        body = await request.json()
        kb = api.state.mycelos.knowledge_base
        success = kb.move_to_topic(path, body.get("topic", ""))
        if not success:
            return JSONResponse({"error": "not_found", "path": path}, status_code=404)
        return {"status": "moved"}

    @api.get("/api/knowledge/documents/{path:path}")
    async def knowledge_document_serve(path: str) -> Any:
        """Serve an original document file (PDF, DOCX, etc.) for a Knowledge note.

        `path` is the note path (e.g. `notes/2026-04-29-foo`). We look up
        the linked source_file via `knowledge_notes.source_file` and
        serve that.
        """
        from starlette.responses import FileResponse
        mycelos = api.state.mycelos
        meta = mycelos.storage.fetchone(
            "SELECT source_file FROM knowledge_notes WHERE path = ?", (path,),
        )
        source_file = (meta or {}).get("source_file") or ""
        if not source_file:
            return JSONResponse({"error": "not found"}, status_code=404)
        doc_path = mycelos.knowledge_base.get_document_path(source_file)
        if not doc_path:
            return JSONResponse({"error": "not found"}, status_code=404)
        return FileResponse(str(doc_path), filename=doc_path.name)

    @api.get("/api/sessions/{session_id}/attachments/{filename:path}")
    async def serve_session_attachment(session_id: str, filename: str) -> Any:
        """Serve a file from the session's attachment folder.

        Path-traversal-safe: session_id is sanitized and the resolved
        filename must live inside the session's attachments folder. Used
        by the chat preview card to render images / link to PDFs.
        """
        from starlette.responses import FileResponse
        from mycelos.files.inbox import sanitize_filename
        mycelos = api.state.mycelos
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

    @api.post("/api/knowledge/notes/{path:path}/vision")
    async def knowledge_note_vision(path: str, request: Request) -> dict[str, Any]:
        """Trigger Vision analysis for a scanned document note."""
        from mycelos.knowledge.ingest import vision_analyze
        mycelos = api.state.mycelos
        result = vision_analyze(mycelos, path)
        if result["status"] == "error":
            return JSONResponse({"error": result["message"]}, status_code=400)
        return result

    @api.post("/api/knowledge/notes/{path:path}/split")
    async def knowledge_note_split(path: str, request: Request) -> dict[str, Any]:
        """Split a note into multiple sub-notes via LLM analysis."""
        mycelos = api.state.mycelos
        body = {}
        content_type = request.headers.get("content-type", "")
        if content_type.startswith("application/json"):
            body = await request.json()

        from mycelos.tools.knowledge import execute_note_split
        result = execute_note_split(
            {"path": path, "confirm": body.get("confirm", False), "sections": body.get("sections")},
            {"app": mycelos, "user_id": _resolve_user_id(request)},
        )
        if result.get("status") == "error":
            return JSONResponse({"error": result["message"]}, status_code=400)
        return result

    @api.get("/api/organizer/suggestions")
    async def organizer_list(request: Request) -> Any:
        from mycelos.knowledge.inbox import InboxService
        mycelos = api.state.mycelos
        inbox = InboxService(mycelos.storage)
        return inbox.list_pending_by_topic()

    @api.post("/api/organizer/accept-all")
    async def organizer_accept_all(request: Request) -> dict[str, Any]:
        """Accept every pending suggestion: create new topics, move notes."""
        from mycelos.knowledge.inbox import InboxService
        mycelos = api.state.mycelos
        kb = mycelos.knowledge_base
        inbox = InboxService(mycelos.storage)
        user_id = _resolve_user_id(request)

        groups = inbox.list_pending_by_topic()
        accepted = 0
        topics_created = 0

        for group in groups:
            if group.get("topic") is None:
                # Link suggestions — just accept them
                for s in group["notes"]:
                    if s.get("kind") == "link":
                        try:
                            dst = s["payload"].get("to")
                            if dst:
                                kb.append_related_link(
                                    s["payload"].get("from") or s["note_path"], dst
                                )
                        except Exception:
                            pass
                    if not s.get("_synthetic"):
                        inbox.accept(s["id"])
                        accepted += 1
                continue

            topic_path = group["topic"]
            is_new = group.get("is_new", False)

            if is_new and topic_path:
                try:
                    kb.create_topic(group["topic_name"])
                    topics_created += 1
                except Exception:
                    pass

            for s in group["notes"]:
                if s.get("_synthetic"):
                    continue
                try:
                    if s["kind"] in ("move", "new_topic"):
                        target = topic_path
                        if target:
                            kb.move_to_topic(s["note_path"], target)
                except Exception:
                    pass
                inbox.accept(s["id"])
                accepted += 1

        # Flip all remaining to accepted (safety net)
        inbox.accept_all_pending()

        try:
            mycelos.audit.log(
                "organizer.accept_all",
                user_id=user_id,
                details={"accepted": accepted, "topics_created": topics_created},
            )
        except Exception:
            pass

        return {"accepted": accepted, "topics_created": topics_created}

    @api.post("/api/organizer/suggestions/{sid}/accept")
    async def organizer_accept(sid: int, request: Request) -> Any:
        from mycelos.knowledge.inbox import InboxService
        mycelos = api.state.mycelos
        inbox = InboxService(mycelos.storage)
        sug = inbox.get(sid)
        if not sug:
            return JSONResponse({"error": "not found"}, status_code=404)

        kb = mycelos.knowledge_base
        kind = sug["kind"]
        payload = sug["payload"]

        try:
            if kind == "move":
                target = payload.get("target")
                if target:
                    kb.move_to_topic(sug["note_path"], target)
            elif kind == "new_topic":
                name = payload.get("name")
                members = payload.get("members", [])
                if name:
                    new_path = kb.create_topic(name)
                    for member in members:
                        kb.move_to_topic(member, new_path)
            elif kind == "link":
                src = payload.get("from") or sug["note_path"]
                dst = payload.get("to")
                if dst:
                    kb.append_related_link(src, dst)
            elif kind == "merge":
                duplicate_path = payload.get("duplicate_path")
                if duplicate_path:
                    handler = mycelos.knowledge_organizer
                    handler._execute_merge(
                        kb, mycelos.storage, sug["note_path"], duplicate_path,
                        payload.get("similarity", 0.0),
                        _resolve_user_id(request),
                    )
            elif kind == "refine_type":
                pass
        except Exception as exc:
            return JSONResponse(
                {"error": f"apply failed: {exc}"}, status_code=500
            )

        inbox.accept(sid)
        try:
            mycelos.audit.log(
                "organizer.suggestion.accepted",
                user_id=_resolve_user_id(request),
                details={"id": sid, "kind": kind},
            )
        except Exception:
            pass
        return {"ok": True, "id": sid, "kind": kind}

    @api.post("/api/organizer/suggestions/{sid}/dismiss")
    async def organizer_dismiss(sid: int, request: Request) -> Any:
        from mycelos.knowledge.inbox import InboxService
        mycelos = api.state.mycelos
        inbox = InboxService(mycelos.storage)
        if not inbox.get(sid):
            return JSONResponse({"error": "not found"}, status_code=404)
        inbox.dismiss(sid)
        try:
            mycelos.audit.log(
                "organizer.suggestion.dismissed",
                user_id=_resolve_user_id(request),
                details={"id": sid},
            )
        except Exception:
            pass
        return {"ok": True, "id": sid}

    @api.post("/api/organizer/run")
    async def organizer_run(request: Request) -> dict[str, Any]:
        mycelos = api.state.mycelos
        user_id = _resolve_user_id(request)
        return mycelos.knowledge_organizer.run(user_id)

    @api.post("/api/organizer/sweep-duplicates")
    async def organizer_sweep_duplicates(request: Request) -> dict[str, Any]:
        """Scan all notes for duplicates and create merge suggestions."""
        mycelos = api.state.mycelos
        handler = mycelos.knowledge_organizer
        count = handler.sweep_duplicates(_resolve_user_id(request))
        return {"duplicates_found": count}

    @api.post("/api/knowledge/sync-relations")
    async def knowledge_sync_relations() -> dict[str, Any]:
        """Rebuild relation links from note content and frontmatter."""
        kb = api.state.mycelos.knowledge_base
        return kb.sync_relations()

    @api.post("/api/knowledge/import")
    async def knowledge_import(request: Request) -> dict[str, Any]:
        """Smart Import: accept a zip of .md/.txt files and import them.

        Body is multipart: field `file` is the zip, optional `mode` is one
        of 'auto' (default), 'preserve', 'suggest'. Returns the import result.
        """
        import io
        import zipfile

        from mycelos.knowledge.import_pipeline import (
            FileEntry,
            detect_import_mode,
            run_preserve_import,
            run_suggest_import,
        )

        mycelos = api.state.mycelos
        form = await request.form()
        mode_arg = form.get("mode") or "auto"
        upload = form.get("file")
        if upload is None:
            return JSONResponse(
                {"error": "file is required"}, status_code=422
            )

        blob = await upload.read()

        try:
            zf = zipfile.ZipFile(io.BytesIO(blob))
        except zipfile.BadZipFile:
            return JSONResponse(
                {"error": "file must be a zip archive"}, status_code=422
            )

        entries: list[FileEntry] = []
        with zf:
            for name in zf.namelist():
                if name.endswith("/"):
                    continue
                entries.append(FileEntry(relpath=name, content=zf.read(name)))

        mode = mode_arg if mode_arg in ("preserve", "suggest") else detect_import_mode(entries)

        kb = mycelos.knowledge_base
        if mode == "preserve":
            result = run_preserve_import(entries, kb)
        else:
            result = run_suggest_import(entries, kb)
            organizer = getattr(mycelos, "knowledge_organizer", None)
            if organizer is not None:
                try:
                    organizer.run(_resolve_user_id(request))
                except Exception:
                    pass

        try:
            mycelos.audit.log(
                "knowledge.import",
                user_id=_resolve_user_id(request),
                details={"mode": mode, "count": len(result.get("created", []))},
            )
        except Exception:
            pass

        return result

    @api.post("/api/reload")
    async def reload(request: Request) -> dict[str, Any]:
        """Reload MCP connectors and channel config.

        Call this after adding/removing connectors or changing channel config.
        Re-discovers MCP tools without full gateway restart.
        Only accessible from localhost (enforced by LocalhostMiddleware).
        """
        from mycelos.gateway.server import _start_mcp_connectors

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

    @api.post("/api/transcribe")
    async def transcribe_audio(request: Request, audio: UploadFile) -> dict[str, Any]:
        """Transcribe audio and return text (no chat processing)."""
        mycelos = api.state.mycelos

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
                user_id=_resolve_user_id(request),
            )
        except Exception as exc:
            logger.error("STT transcription error: %s", exc, exc_info=True)
            return JSONResponse(
                status_code=500,
                content={"error": "Transcription failed"},
            )

        text = (result.get("text") or "").strip()
        return {"text": text}

    @api.post("/api/audio")
    async def handle_audio(
        request: Request,
        audio: UploadFile,
        session_id: str = "",
    ) -> StreamingResponse:
        """Accept audio upload, transcribe via SecurityProxy, process as chat message."""
        from mycelos.chat.events import session_event, error_event, done_event

        mycelos = api.state.mycelos
        service = api.state.chat_service
        user_id = _resolve_user_id(request)

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

    @api.post("/api/upload")
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

        service = api.state.chat_service
        mycelos = api.state.mycelos
        user_id = _resolve_user_id(request)

        if not session_id:
            session_id = service.create_session(user_id=user_id)

        file_bytes = await file.read()
        filename = file.filename or "unnamed"
        kind = content_kind(Path(filename))

        if kind == "unsupported":
            return _sse_error(session_id, f"Unsupported file type for chat attachments: {filename}")

        # Map content_kind to SIZE_CAPS_BYTES key ("document" → "pdf").
        _cap_key = {"document": "pdf", "image": "image", "text": "text"}.get(kind, kind)
        cap = SIZE_CAPS_BYTES.get(_cap_key, 0)
        if cap and len(file_bytes) > cap:
            return _sse_error(session_id, f"File too large ({len(file_bytes)} bytes > {cap} for {kind})")

        store = SessionAttachmentStore(mycelos.data_dir / "sessions")
        try:
            saved = store.save(session_id, file_bytes, filename)
        except ValueError as e:
            return _sse_error(session_id, str(e))

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

    # ── Connectors ────────────────────────────────────────────

    def _recipe_payload(recipe) -> dict[str, Any]:
        """Serialize a recipe for the HTTP API (without the resolved setup guide).

        The single-recipe endpoint extends this with `setup_guide` resolved
        via `get_setup_guide(oauth_setup_guide_id)`; the list endpoint
        returns just the metadata.
        """
        return {
            "id": recipe.id,
            "name": recipe.name,
            "description": recipe.description,
            "kind": recipe.kind,
            "command": recipe.command,
            "transport": recipe.transport,
            "category": recipe.category,
            "credentials": list(recipe.credentials),
            "capabilities_preview": list(recipe.capabilities_preview),
            "setup_flow": recipe.setup_flow,
            "oauth_setup_guide_id": recipe.oauth_setup_guide_id,
            "oauth_client_credential_service": recipe.oauth_client_credential_service,
            "oauth_token_credential_service": recipe.oauth_token_credential_service,
            "http_endpoint": recipe.http_endpoint,
            "requires_node": recipe.requires_node,
        }

    @api.get("/api/connectors/lookup-env-vars")
    async def lookup_connector_env_vars(package: str) -> dict:
        """Return env-var hints for a known MCP package.

        Wraps mcp_search.lookup_env_vars so the Custom-MCP setup form
        can prefill its fields when the user types a known package.
        Failures are silenced — registry availability is not the user's
        problem; an empty list lets the user enter vars manually.
        """
        from mycelos.connectors.mcp_search import lookup_env_vars
        try:
            env_vars = lookup_env_vars(package) or []
        except Exception:
            env_vars = []
        return {"env_vars": env_vars}

    @api.get("/api/connectors/recipes")
    async def list_recipes_grouped() -> dict[str, list[dict[str, Any]]]:
        """List all connector recipes grouped by `kind`.

        Returns `{"channels": [...], "mcp": [...]}`. The frontend uses
        this split to render chat channels (Telegram, Slack, ...) and
        MCP connectors (GitHub, Gmail, ...) as separate sections.
        """
        from mycelos.connectors.mcp_recipes import RECIPES

        channels: list[dict[str, Any]] = []
        mcp: list[dict[str, Any]] = []
        for recipe in RECIPES.values():
            payload = _recipe_payload(recipe)
            if recipe.kind == "channel":
                channels.append(payload)
            else:
                mcp.append(payload)
        return {"channels": channels, "mcp": mcp}

    @api.get("/api/connectors/recipes/{recipe_id}")
    async def get_recipe(recipe_id: str) -> dict[str, Any]:
        """Recipe metadata + resolved setup guide in one roundtrip.

        Used by the frontend setup dialog to decide which flow to render
        (plain 'secret' vs. 'oauth_http' wizard) and to show the
        platform-specific preparation steps inline.
        """
        from mycelos.connectors.mcp_recipes import get_recipe as get_r
        from mycelos.connectors.oauth_setup_guides import get_setup_guide

        recipe = get_r(recipe_id)
        if recipe is None:
            raise HTTPException(status_code=404, detail=f"Unknown recipe: {recipe_id}")

        guide = (
            get_setup_guide(recipe.oauth_setup_guide_id)
            if recipe.oauth_setup_guide_id
            else None
        )
        return {**_recipe_payload(recipe), "setup_guide": guide}

    @api.post("/api/connectors/oauth/start")
    async def oauth_start_passthrough(
        request: Request, payload: dict[str, Any],
    ) -> dict[str, Any]:
        """Generate an OAuth 2.0 Authorization Code Flow URL.

        Body: {recipe_id, origin}. Origin is the browser's
        window.location.origin — used to build the redirect_uri that
        must match what the user registered in Cloud Console.
        """
        import hashlib
        import base64
        import secrets as _secrets
        from datetime import datetime, timedelta, timezone
        import json as _json
        from urllib.parse import urlencode

        from mycelos.connectors.mcp_recipes import get_recipe

        recipe_id = payload.get("recipe_id", "")
        origin = (payload.get("origin") or "").rstrip("/")
        if not origin:
            raise HTTPException(status_code=400, detail="origin is required")

        recipe = get_recipe(recipe_id)
        if recipe is None:
            raise HTTPException(status_code=404, detail=f"Unknown recipe: {recipe_id}")
        if recipe.setup_flow != "oauth_http":
            raise HTTPException(
                status_code=400,
                detail=f"Recipe '{recipe_id}' setup_flow is '{recipe.setup_flow}', not 'oauth_http'",
            )

        mycelos = api.state.mycelos

        # Read ONLY the public `client_id` — never the client_secret.
        # In single-process mode we read the encrypted credential locally
        # and extract just client_id. In two-container mode the gateway
        # can't decrypt, so it asks the proxy via /oauth/public_fields
        # which returns only {client_id}. Either way, client_secret
        # never crosses into the gateway process.
        client_id = ""
        try:
            local_cred = mycelos.credentials.get_credential(
                recipe.oauth_client_credential_service, user_id="default",
            )
            if isinstance(local_cred, dict) and isinstance(local_cred.get("api_key"), str):
                blob = _json.loads(local_cred["api_key"])
                installed = blob.get("installed") or blob.get("web") or {}
                client_id = installed.get("client_id", "") or ""
        except NotImplementedError:
            client_id = ""
        except Exception:
            client_id = ""

        if not client_id:
            proxy_client = getattr(mycelos, "proxy_client", None)
            if proxy_client is not None:
                try:
                    got = proxy_client.oauth_public_fields(
                        recipe.oauth_client_credential_service, user_id="default",
                    )
                    if isinstance(got, dict):
                        client_id = got.get("client_id", "") or ""
                except Exception:
                    pass

        if not client_id:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"OAuth client credential '{recipe.oauth_client_credential_service}' "
                    "not uploaded. Paste client_secret_*.json first."
                ),
            )

        # Build PKCE pair and state.
        code_verifier = _secrets.token_urlsafe(64)
        challenge = base64.urlsafe_b64encode(
            hashlib.sha256(code_verifier.encode()).digest()
        ).decode().rstrip("=")
        state = _secrets.token_urlsafe(32)

        redirect_uri = f"{origin}/api/connectors/oauth/callback"

        # Store state (TTL-protected; sweep expired on every call).
        states = getattr(api.state, "oauth_pending_states", None)
        if states is None:
            states = {}
            api.state.oauth_pending_states = states
        now = datetime.now(timezone.utc)
        expiry = (now + timedelta(minutes=10)).isoformat()
        for k in list(states.keys()):
            exp = states[k].get("expires_at", "")
            try:
                if datetime.fromisoformat(exp) < now:
                    states.pop(k, None)
            except Exception:
                states.pop(k, None)

        states[state] = {
            "recipe_id": recipe_id,
            "code_verifier": code_verifier,
            "user_id": "default",
            "origin": origin,
            "expires_at": expiry,
        }

        params = {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": " ".join(recipe.oauth_scopes),
            "state": state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "access_type": "offline",
            "prompt": "consent",
        }
        auth_url = f"{recipe.oauth_authorize_url}?{urlencode(params)}"

        return {"auth_url": auth_url, "redirect_uri": redirect_uri}


    @api.get("/api/connectors/oauth/callback")
    async def oauth_callback_passthrough(
        code: str = "",
        state: str = "",
        error: str = "",
    ):
        """Browser lands here after OAuth consent. Validate state,
        exchange the code through the proxy, redirect to the
        connectors page."""
        from fastapi.responses import RedirectResponse

        if error:
            return RedirectResponse(
                url=f"/connectors.html?oauth_error={error}",
                status_code=302,
            )

        mycelos = api.state.mycelos
        states = getattr(api.state, "oauth_pending_states", None) or {}
        entry = states.pop(state, None)
        if entry is None:
            return RedirectResponse(
                url="/connectors.html?oauth_error=invalid_state",
                status_code=302,
            )

        proxy_client = getattr(mycelos, "proxy_client", None)
        if proxy_client is None:
            return RedirectResponse(
                url="/connectors.html?oauth_error=proxy_unavailable",
                status_code=302,
            )

        redirect_uri = f"{entry['origin']}/api/connectors/oauth/callback"
        try:
            result = proxy_client.oauth_callback(
                recipe_id=entry["recipe_id"],
                code=code,
                code_verifier=entry["code_verifier"],
                redirect_uri=redirect_uri,
                user_id=entry["user_id"],
            )
        except Exception as e:
            return RedirectResponse(
                url=f"/connectors.html?oauth_error={str(e)[:120]}",
                status_code=302,
            )

        if result.get("status") != "connected":
            err = (result.get("error") or "exchange_failed")[:120]
            return RedirectResponse(
                url=f"/connectors.html?oauth_error={err}",
                status_code=302,
            )

        # Token stored — now register the connector so it shows up in
        # the UI and becomes usable by agents. Idempotent: if the
        # connector already exists we skip (e.g. user re-consented to
        # refresh scopes).
        try:
            from mycelos.connectors.mcp_recipes import get_recipe
            recipe = get_recipe(entry["recipe_id"])
            existing = mycelos.connector_registry.get(entry["recipe_id"])
            if recipe is not None and existing is None:
                mycelos.connector_registry.register(
                    entry["recipe_id"],
                    recipe.name,
                    "mcp",
                    list(recipe.capabilities_preview or []),
                    description=recipe.description,
                    setup_type="oauth_http",
                )
                mycelos.audit.log(
                    "connector.registered",
                    details={
                        "connector": entry["recipe_id"],
                        "setup_type": "oauth_http",
                    },
                    user_id=entry["user_id"],
                )
            # Connect the live MCP session immediately so the user can
            # start using it without a gateway restart. Token resolution
            # happens inside MycelosMCPClient via oauth_token_manager.
            if recipe is not None and recipe.setup_flow == "oauth_http":
                try:
                    mycelos.mcp_manager.connect(
                        connector_id=entry["recipe_id"],
                        command="",
                        env_vars={},
                        transport="http",
                    )
                    logger.info("MCP session started after OAuth for %s", entry["recipe_id"])
                except Exception as e:
                    # Non-fatal: the startup-path connect will retry on
                    # the next gateway restart.
                    logger.warning(
                        "Post-OAuth MCP connect for '%s' failed: %s",
                        entry["recipe_id"], e,
                    )
        except Exception:
            # Connector-registry failure shouldn't undo the successful
            # token exchange; log and keep going. The user can retry
            # via the UI (which is idempotent).
            logger.exception("connector registration failed for %s", entry["recipe_id"])

        return RedirectResponse(
            url=f"/connectors.html?connected={entry['recipe_id']}",
            status_code=302,
        )

    @api.get("/api/connectors")
    async def list_connectors() -> list[dict[str, Any]]:
        """List all connectors with MCP tool count."""
        mycelos = api.state.mycelos
        connectors = mycelos.connector_registry.list_connectors()
        mcp_mgr = getattr(mycelos, "_mcp_manager", None)
        result = []
        for c in connectors:
            tool_count = 0
            if mcp_mgr:
                prefix = f"{c['id']}."
                tool_count = len([t for t in mcp_mgr.list_tools() if t["name"].startswith(prefix)])
            result.append({**dict(c), "tool_count": tool_count})
        return result

    @api.get("/api/connectors/{connector_id}")
    async def get_connector(connector_id: str) -> dict[str, Any]:
        """Look up a single connector by id. 404 if not registered.

        Used by the frontend's OAuth-dialog polling loop: the dialog
        hits this every ~2.5 seconds after showing the auth URL; a 200
        means the callback handler has registered the connector (i.e.
        the user completed consent) and the dialog flips to Stage 3.
        """
        mycelos = api.state.mycelos
        c = mycelos.connector_registry.get(connector_id)
        if c is None:
            raise HTTPException(status_code=404, detail=f"Unknown connector: {connector_id}")
        return dict(c)

    @api.post("/api/connectors")
    async def add_connector(request: Request, body: ConnectorAddRequest) -> dict[str, Any]:
        """Add a connector (same logic as /connector add slash command)."""
        mycelos = api.state.mycelos

        # Validate command if provided
        if body.command:
            from mycelos.chat.slash_commands import _validate_mcp_command
            validation_error = _validate_mcp_command(body.command)
            if validation_error:
                return JSONResponse({"error": f"Invalid command: {validation_error}"}, status_code=400)

        # Check if connector already exists
        existing = mycelos.connector_registry.get(body.name)
        if existing:
            return JSONResponse({"error": f"Connector '{body.name}' already exists"}, status_code=409)

        # Detect builtin connectors (email, etc.) — these don't need MCP commands
        from mycelos.connectors.mcp_recipes import get_recipe
        recipe = get_recipe(body.name)
        is_builtin = recipe and recipe.transport == "builtin"
        is_channel = recipe and recipe.transport == "channel"
        if is_builtin:
            connector_type = "builtin"
            setup_type = "builtin"
        elif is_channel:
            connector_type = "channel"
            setup_type = "channel"
        else:
            connector_type = "mcp"
            setup_type = "mcp"
        description = recipe.description if recipe else (
            f"MCP: {body.command}" if body.command else f"Connector: {body.name}"
        )

        try:
            mycelos.connector_registry.register(
                body.name, body.name, connector_type, [],
                description=description,
                setup_type=setup_type,
            )
        except Exception as e:
            return JSONResponse({"error": f"Failed to register connector: {e}"}, status_code=500)

        # Store secret if provided. Key stored under the bare connector
        # name — both builtins (telegram, email) and MCP connectors share
        # one namespace. The MCP subsystem substitutes `credential:<id>`
        # in env_vars and the SecurityProxy resolves that via the bare
        # name.
        # env_vars (multi-var) wins over legacy single `secret`. We support
        # both shapes so recipe-setup code (which sends `secret`) keeps working.
        cleaned_env_vars: dict[str, str] | None = None
        if body.env_vars:
            cleaned_env_vars = {
                k: v for k, v in body.env_vars.items() if k.strip()
            }
            if not cleaned_env_vars:
                cleaned_env_vars = None  # all keys were blank — fall through

        logger.info(
            "add_connector: name=%s mode=%s",
            body.name,
            "multi" if cleaned_env_vars else ("secret" if body.secret else "none"),
        )

        if cleaned_env_vars:
            try:
                logger.info(
                    "add_connector: storing multi-var credential service=%s vars=%s",
                    body.name, list(cleaned_env_vars.keys()),
                )
                mycelos.credentials.store_credential(
                    body.name,
                    {
                        "api_key": json.dumps(cleaned_env_vars),
                        "env_var": "__multi__",
                        "connector": body.name,
                    },
                    description=f"Credentials for {body.name}",
                )
                mycelos.audit.log(
                    "credential.stored",
                    details={"connector": body.name, "env_var": "__multi__",
                             "var_names": list(cleaned_env_vars.keys())},
                    user_id=_resolve_user_id(request),
                )
            except Exception as e:
                logger.exception("Credential storage failed for connector %s: %s", body.name, e)
                mycelos.audit.log(
                    "credential.store_failed",
                    details={"connector": body.name, "error": str(e)},
                    user_id=_resolve_user_id(request),
                )
        elif body.secret:
            try:
                # Recipe-declared env_var name (e.g. BRAVE_API_KEY) if the
                # connector is a known MCP recipe; otherwise derive from
                # the name.
                if recipe and recipe.credentials:
                    env_var_name = recipe.credentials[0].get("env_var", "")
                else:
                    env_var_name = f"{body.name.upper().replace('-', '_')}_API_KEY"

                logger.info(
                    "add_connector: storing credential service=%s env_var=%s",
                    body.name, env_var_name,
                )
                mycelos.credentials.store_credential(
                    body.name,
                    {"api_key": body.secret, "env_var": env_var_name},
                    description=f"Credentials for {body.name}",
                )
                mycelos.audit.log(
                    "credential.stored",
                    details={"connector": body.name, "env_var": env_var_name},
                    user_id=_resolve_user_id(request),
                )
            except Exception as e:
                logger.exception("Credential storage failed for connector %s: %s", body.name, e)
                mycelos.audit.log(
                    "credential.store_failed",
                    details={"connector": body.name, "error": str(e)},
                    user_id=_resolve_user_id(request),
                )
        else:
            logger.info("add_connector: no creds provided for %s — skipping store", body.name)

        # Channel connectors also need a row in `channels` so the channel
        # layer (Telegram polling, Slack socket, ...) actually picks them up.
        if is_channel:
            import json as _json
            try:
                mycelos.storage.execute("DELETE FROM channels WHERE id = ?", (body.name,))
                mycelos.storage.execute(
                    """INSERT INTO channels (id, channel_type, mode, status, config, allowed_users)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (body.name, body.name, "polling", "active", "{}", "[]"),
                )
                mycelos.audit.log("channel.configured", details={"channel": body.name})
            except Exception as e:
                logger.exception("channel row insert failed for %s: %s", body.name, e)

        mycelos.audit.log("connector.added", details={"connector": body.name, "command": body.command}, user_id=_resolve_user_id(request))

        # Auto-start MCP server for recipe-based connectors (so no restart
        # needed). In two-container mode the subprocess belongs to the
        # proxy container — it's the only process that can decrypt the
        # credentials the MCP server needs. Route the mcp_start RPC
        # through proxy_client instead of spawning locally.
        if not is_builtin and recipe and recipe.command and recipe.transport == "stdio":
            def _auto_start_recipe() -> None:
                try:
                    env_vars: dict[str, str] = dict(recipe.static_env)
                    for cred_spec in recipe.credentials:
                        env_var = cred_spec["env_var"]
                        env_vars[env_var] = f"credential:{body.name}"

                    from mycelos.connectors import http_tools as _http_tools
                    proxy_client = getattr(_http_tools, "_proxy_client", None)
                    if proxy_client is not None:
                        import shlex
                        argv = shlex.split(recipe.command)
                        resp = proxy_client.mcp_start(
                            connector_id=body.name,
                            command=argv,
                            env_vars=env_vars,
                            transport=recipe.transport,
                        )
                        if resp.get("error"):
                            raise RuntimeError(resp["error"])
                        tools = resp.get("tools", [])
                        mycelos.mcp_manager.register_remote_session(
                            connector_id=body.name,
                            session_id=resp.get("session_id", ""),
                            tools=tools,
                        )
                        tool_count = len(tools)
                    else:
                        tools = mycelos.mcp_manager.connect(
                            connector_id=body.name,
                            command=recipe.command,
                            env_vars=env_vars,
                            transport=recipe.transport,
                        )
                        tool_count = len(tools)
                    logger.info("MCP server '%s' auto-started: %d tools", body.name, tool_count)
                except Exception as e:
                    logger.warning("MCP auto-start failed for '%s': %s", body.name, e)

            threading.Thread(
                target=_auto_start_recipe,
                name=f"mcp-autostart-{body.name}",
                daemon=True,
            ).start()

        if not is_builtin and not recipe and body.command:
            def _auto_start_custom() -> None:
                try:
                    stored = mycelos.credentials.get_credential(body.name)
                    env_vars: dict[str, str] = {}
                    if stored:
                        if stored.get("env_var") == "__multi__":
                            env_vars["__multi__"] = f"credential:{body.name}"
                        elif stored.get("env_var"):
                            env_vars[stored["env_var"]] = f"credential:{body.name}"

                    from mycelos.connectors import http_tools as _http_tools
                    proxy_client = getattr(_http_tools, "_proxy_client", None)
                    import shlex
                    argv = shlex.split(body.command)
                    if proxy_client is not None:
                        resp = proxy_client.mcp_start(
                            connector_id=body.name,
                            command=argv,
                            env_vars=env_vars,
                            transport="stdio",
                        )
                        if resp.get("error"):
                            raise RuntimeError(resp["error"])
                        tools = resp.get("tools", [])
                        mycelos.mcp_manager.register_remote_session(
                            connector_id=body.name,
                            session_id=resp.get("session_id", ""),
                            tools=tools,
                        )
                        tool_count = len(tools)
                    else:
                        tools = mycelos.mcp_manager.connect(
                            connector_id=body.name,
                            command=body.command,
                            env_vars=env_vars,
                            transport="stdio",
                        )
                        tool_count = len(tools)
                    logger.info(
                        "Custom MCP server '%s' auto-started: %d tools",
                        body.name, tool_count,
                    )
                except Exception as e:
                    logger.warning("Custom MCP auto-start failed for '%s': %s", body.name, e)

            threading.Thread(
                target=_auto_start_custom,
                name=f"mcp-autostart-{body.name}",
                daemon=True,
            ).start()

        return {"status": "registered", "connector": body.name}

    @api.delete("/api/connectors/{connector_id}")
    async def remove_connector(request: Request, connector_id: str) -> dict[str, Any]:
        """Remove a connector."""
        mycelos = api.state.mycelos
        existing = mycelos.connector_registry.get(connector_id)
        if not existing:
            return JSONResponse({"error": f"Connector '{connector_id}' not found"}, status_code=404)

        mycelos.connector_registry.remove(connector_id)
        mycelos.audit.log("connector.removed", details={"connector": connector_id}, user_id=_resolve_user_id(request))
        return {"status": "removed", "connector": connector_id}

    @api.get("/api/connectors/{connector_id}/tools")
    async def connector_tools(request: Request, connector_id: str) -> dict[str, Any]:
        """List the MCP tools exposed by one connector, with their
        descriptions and current policy status. Powers the Tool
        Transparency panel in the Connectors page.

        Returns { tools: [{name, description, policy, blocked_reason}], ... }.
        A tool is ``blocked`` when the PolicyEngine would return
        ``"never"`` for it — that's the canonical reason an agent can
        see but not use a tool.
        """
        mycelos = api.state.mycelos
        existing = mycelos.connector_registry.get(connector_id)
        if not existing:
            return JSONResponse({"error": f"Connector '{connector_id}' not found"}, status_code=404)

        user_id = _resolve_user_id(request)
        prefix = f"{connector_id}."
        mcp_mgr = getattr(mycelos, "_mcp_manager", None)
        raw_tools: list[dict[str, Any]] = []
        if mcp_mgr is not None:
            try:
                raw_tools = [t for t in mcp_mgr.list_tools() if t["name"].startswith(prefix)]
            except Exception as e:
                return {"connector": connector_id, "tools": [], "error": str(e)}

        policy = mycelos.policy_engine
        tools_out: list[dict[str, Any]] = []
        for t in raw_tools:
            decision = None
            try:
                decision = policy.evaluate(user_id, None, t["name"])
            except Exception:
                decision = None
            blocked = decision == "never"
            tools_out.append({
                "name": t["name"][len(prefix):],
                "full_name": t["name"],
                "description": t.get("description", ""),
                "input_schema": t.get("input_schema") or {},
                "policy": decision or "default",
                "blocked": blocked,
            })

        return {
            "connector": connector_id,
            "operational_state": existing.get("operational_state"),
            "last_success_at": existing.get("last_success_at"),
            "last_error": existing.get("last_error"),
            "last_error_at": existing.get("last_error_at"),
            "tools": tools_out,
        }

    @api.post("/api/connectors/{connector_id}/tools/{tool_name}/call")
    async def connector_tool_call(
        request: Request,
        connector_id: str,
        tool_name: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        """Invoke one MCP tool on a connector. Powers `mycelos
        connector call` and any future UI 'try this tool' button.

        Body: {arguments: {...}}. Returns the raw MCP tool result.
        Failures from the underlying MCP call surface as 502 with the
        error message; a totally missing tool is 404.
        """
        mycelos = api.state.mycelos
        existing = mycelos.connector_registry.get(connector_id)
        if not existing:
            raise HTTPException(status_code=404, detail=f"Connector '{connector_id}' not found")

        mcp_mgr = getattr(mycelos, "_mcp_manager", None)
        if mcp_mgr is None:
            raise HTTPException(status_code=503, detail="MCP manager not available")

        full_name = f"{connector_id}.{tool_name}"
        arguments = payload.get("arguments") or {}
        if not isinstance(arguments, dict):
            raise HTTPException(status_code=400, detail="arguments must be an object")

        result = mcp_mgr.call_tool(full_name, arguments)
        # call_tool returns either a dict-with-content (success) or a
        # {error: "..."} dict (manager-level failure). Treat the error
        # shape as 502 so curl/CLI users see a non-2xx; otherwise pass
        # through verbatim so the caller can inspect the MCP payload.
        if isinstance(result, dict) and "error" in result and len(result) == 1:
            return JSONResponse({"error": result["error"]}, status_code=502)
        return {"connector": connector_id, "tool": tool_name, "result": result}

    @api.post("/api/connectors/{connector_id}/test")
    async def test_connector(request: Request, connector_id: str) -> dict[str, Any]:
        """Run a live connectivity check on a connector.

        Uses the shape of the connector to pick the right probe:
          * telegram → ``getMe`` via the proxy
          * MCP-backed connectors → ``tools/list`` on the running session
          * everything else → a 'not testable' hint

        Every outcome flows through connector_registry.record_* so the
        panel and Doctor see fresh telemetry immediately.
        """
        mycelos = api.state.mycelos
        existing = mycelos.connector_registry.get(connector_id)
        if not existing:
            return JSONResponse({"error": f"Connector '{connector_id}' not found"}, status_code=404)

        ctype = (existing.get("connector_type") or "").lower()
        user_id = _resolve_user_id(request)

        def _ok(detail: str, **extra) -> dict[str, Any]:
            mycelos.connector_registry.record_success(connector_id)
            mycelos.audit.log(
                "connector.test_ok",
                details={"connector": connector_id, **extra},
                user_id=user_id,
            )
            return {"ok": True, "connector": connector_id, "detail": detail, **extra}

        def _fail(detail: str, **extra) -> dict[str, Any]:
            mycelos.connector_registry.record_failure(connector_id, detail)
            mycelos.audit.log(
                "connector.test_failed",
                details={"connector": connector_id, "error": detail[:200], **extra},
                user_id=user_id,
            )
            return {"ok": False, "connector": connector_id, "detail": detail, **extra}

        # ── Telegram ────────────────────────────────────────────
        if connector_id == "telegram" or ctype in ("telegram", "channel"):
            from mycelos.channels.telegram import call_telegram_api
            data = call_telegram_api(mycelos, "getMe", http_method="GET", timeout=5)
            if data.get("ok"):
                bot = data.get("result", {}) or {}
                return _ok(
                    f"Bot '{bot.get('first_name', '?')}' (@{bot.get('username', '?')}) reachable",
                    bot_username=bot.get("username"),
                    bot_name=bot.get("first_name"),
                )
            return _fail(data.get("description", "unknown error"))

        # ── Built-in connectors (http, search, etc.) ──────────────
        # These are always-on in-process helpers, not MCP sessions.
        # 'Testing' them by walking the MCP-tool list is meaningless —
        # their tools live in the ToolRegistry under their own names
        # (http_get, search_web, ...). Report healthy when registered.
        if ctype in ("http", "search", "builtin"):
            return _ok(f"Built-in {ctype} connector is active")

        # ── MCP-backed ─────────────────────────────────────────
        mcp_mgr = getattr(mycelos, "_mcp_manager", None)
        if mcp_mgr is not None:
            prefix = f"{connector_id}."
            try:
                tools = [t for t in mcp_mgr.list_tools() if t["name"].startswith(prefix)]
            except Exception as e:
                return _fail(f"tools/list failed: {e}")
            if tools:
                return _ok(f"{len(tools)} tool(s) loaded", tool_count=len(tools))

            # No tools loaded — session may have died or never started.
            # Try one reconnect from the recipe before surfacing the
            # "No tools discovered" error. Test-connection now actually
            # heals a dead subprocess instead of just reading stale state.
            #
            # In two-container mode (proxy_client set) the subprocess
            # must live in the proxy container — route through
            # proxy_client.mcp_start. In single-process mode, let the
            # local mcp_manager spawn it directly via its recipe.
            try:
                from mycelos.connectors import http_tools as _http_tools
                from mycelos.connectors.mcp_recipes import get_recipe
                import shlex as _shlex

                proxy_client = getattr(_http_tools, "_proxy_client", None)
                recipe = get_recipe(connector_id)

                if proxy_client is not None and recipe is not None and recipe.setup_flow != "oauth_http":
                    # Subprocess-based recipe → spawn in proxy.
                    env_vars = dict(recipe.static_env or {})
                    for cred_spec in recipe.credentials or []:
                        env_vars[cred_spec["env_var"]] = f"credential:{connector_id}"
                    argv = _shlex.split(recipe.command) if recipe.command else []
                    resp = proxy_client.mcp_start(
                        connector_id=connector_id,
                        command=argv,
                        env_vars=env_vars,
                        transport=recipe.transport,
                    )
                    if resp.get("error"):
                        raise RuntimeError(resp["error"])
                    new_tools = resp.get("tools", [])
                    mycelos.mcp_manager.register_remote_session(
                        connector_id=connector_id,
                        session_id=resp.get("session_id", ""),
                        tools=new_tools,
                    )
                else:
                    # Single-process mode OR oauth_http (no subprocess):
                    # let the local manager handle it.
                    mcp_mgr.reconnect(connector_id)

                tools = [t for t in mcp_mgr.list_tools() if t["name"].startswith(prefix)]
                if tools:
                    return _ok(
                        f"Reconnected; {len(tools)} tool(s) loaded",
                        tool_count=len(tools),
                    )
            except Exception as e:
                return _fail(f"reconnect failed: {e}")
            return _fail(
                "No tools discovered after reconnect. "
                "Check credentials and recipe configuration."
            )

        return {
            "ok": None,
            "connector": connector_id,
            "detail": "No test available for this connector type.",
        }

    # ── Models ─────────────────────────────────────────────────

    @api.get("/api/models")
    async def list_models() -> dict[str, Any]:
        """All models, registered agents, and agent assignments.

        `agents` lists every registered agent (name + id) so the UI can show
        an explicit row for agents that currently inherit system defaults.
        `assignments` rows carry `agent_name` for labeling.
        """
        mycelos = api.state.mycelos
        models = mycelos.storage.fetchall("SELECT * FROM llm_models ORDER BY provider, tier")
        agents = mycelos.storage.fetchall(
            "SELECT id, name FROM agents ORDER BY id"
        )
        assignments = mycelos.storage.fetchall(
            """
            SELECT a.agent_id, a.model_id, a.priority, a.purpose,
                   COALESCE(g.name, a.agent_id) AS agent_name
            FROM agent_llm_models a
            LEFT JOIN agents g ON g.id = a.agent_id
            ORDER BY COALESCE(a.agent_id, 'zzz'), a.priority
            """
        )
        return {
            "models": [dict(m) for m in models],
            "agents": [dict(r) for r in agents],
            "assignments": [dict(a) for a in assignments],
        }

    @api.get("/api/tools")
    async def list_tools() -> dict[str, Any]:
        """Return all registered built-in tools with category + permission.

        Used by the Agents detail page to render tool checkboxes grouped by
        category. Custom/persona agents see a writable matrix; system agents
        see the same list as a read-only reference.

        Does NOT expose dynamic MCP tools — those are reached via the
        ``connector_call`` meta-tool.
        """
        from mycelos.tools.registry import ToolRegistry

        ToolRegistry._ensure_initialized()
        tools: list[dict[str, Any]] = []
        for name, entry in sorted(ToolRegistry._tools.items()):
            schema = entry.get("schema", {})
            func = schema.get("function", {}) if isinstance(schema, dict) else {}
            tools.append({
                "name": name,
                "category": entry.get("category") or "uncategorized",
                "permission": entry["permission"].value,
                "description": func.get("description", ""),
            })
        return {"tools": tools}

    @api.get("/api/system/update-status")
    async def system_update_status() -> dict[str, Any]:
        """Return the cached Mycelos release-check state.

        Cheap read: never hits GitHub. The background ModelUpdaterHandler
        refreshes the cache once a day; this endpoint serves whatever is
        stored in memory so the Doctor banner and Settings toggle can
        render without an extra network call.
        """
        import json as _json
        mycelos = api.state.mycelos
        try:
            raw = mycelos.memory.get(
                user_id="default", scope="system", key="system.update.latest"
            )
        except Exception:
            raw = None
        state: dict[str, Any] = {}
        if raw:
            if isinstance(raw, dict):
                state = raw
            else:
                try:
                    state = _json.loads(raw)
                except Exception:
                    state = {}
        try:
            opt = mycelos.memory.get(
                user_id="default", scope="system", key="system.check_for_updates"
            )
        except Exception:
            opt = None
        checks_enabled = True
        if opt is not None:
            checks_enabled = str(opt).lower() not in {"0", "false", "off", "no"}
        state["checks_enabled"] = checks_enabled
        return state

    @api.put("/api/system/update-check-enabled")
    async def set_update_check_enabled(payload: dict[str, Any]) -> dict[str, Any]:
        """Enable/disable the daily GitHub release check."""
        mycelos = api.state.mycelos
        enabled = bool(payload.get("enabled", True))
        mycelos.memory.set(
            user_id="default",
            scope="system",
            key="system.check_for_updates",
            value="true" if enabled else "false",
        )
        return {"ok": True, "enabled": enabled}

    @api.post("/api/models/refresh")
    async def refresh_models() -> dict[str, Any]:
        """Trigger an on-demand refresh of the LLM model registry.

        Delegates to the ModelUpdaterHandler (deterministic — no LLM call).
        Returns ``{"added": [...], "updated_count": N, "total": N}``.
        """
        mycelos = api.state.mycelos
        result = mycelos.model_updater.run("default")
        return result

    @api.get("/api/models/winners")
    async def model_winners() -> dict[str, Any]:
        """Top-3-per-provider 'winners' that the auto-setup picks.

        Reuses register_provider_models's logic: filters out legacy
        models, sorts newest-version-first within each tier, and
        returns the same one-per-tier set the onboarding flow would
        pick on a fresh install. Used by Settings → Models to render
        the prominent recipes-style cards before the full table.

        Shape: ``{provider_id: [{id, tier, ...}]}`` per provider that
        has any winner. Providers with no current-generation models
        (e.g. ollama before discovery) return an empty list.
        """
        from mycelos.llm.providers import PROVIDERS, get_provider_models

        result: dict[str, list[dict[str, Any]]] = {}
        for provider_id in PROVIDERS:
            try:
                catalog = get_provider_models(provider_id) or []
            except Exception:
                catalog = []
            picked: list[dict[str, Any]] = []
            seen_tiers: set[str] = set()
            for m in catalog:
                if m.tier and m.tier not in seen_tiers:
                    picked.append({
                        "id": m.id,
                        "name": m.name,
                        "provider": m.provider,
                        "tier": m.tier,
                        "input_cost_per_1k": m.input_cost_per_1k,
                        "output_cost_per_1k": m.output_cost_per_1k,
                        "max_context": m.max_context,
                    })
                    seen_tiers.add(m.tier)
            if picked:
                result[provider_id] = picked
        return {"providers": result}

    def _is_date_only_bump(old_id: str, new_id: str) -> bool:
        """True when old_id and new_id only differ by a trailing date.

        Matches patterns like ``gpt-5.4-2026-03-05`` vs. ``gpt-5.4-2026-04-15``
        (or single bare ``...-20260305`` variants). Same base, different
        date-stamp → weekly spam rather than a real upgrade, so we skip
        surfacing it in the migration banner.
        """
        date_suffix = re.compile(r"[-_](\d{4}-\d{2}-\d{2}|\d{8})$")
        old_base = date_suffix.sub("", old_id)
        new_base = date_suffix.sub("", new_id)
        if old_base == new_base and old_base != old_id:
            return True
        return False

    @api.get("/api/models/upgrades")
    async def model_upgrades() -> dict[str, Any]:
        """Detect which currently-registered models have a newer version
        in the same (provider, tier) bucket, and which agent / system /
        workflow assignments use the old one.

        For each old model that has a newer counterpart we return:
            {
              "old_id": "anthropic/claude-opus-4-5",
              "new_id": "anthropic/claude-opus-4-7",
              "tier":   "opus",
              "provider": "anthropic",
              "assignments": [
                  {"key": "agent:mycelos:execution", "label": "Mycelos · execution",
                   "agent_id": "mycelos", "purpose": "execution", "priority": 1},
                  {"key": "system::execution",       "label": "System default · execution", ...},
              ],
            }

        Sorted by 'most assignments first' so the UI prioritizes the
        upgrade with the broadest impact. Date-suffix-only bumps
        (e.g. gpt-5.4-2026-03-05 → gpt-5.4-2026-04-15) are excluded —
        only major / minor version jumps qualify, otherwise users get
        spammed weekly.
        """
        from mycelos.llm.providers import (
            get_provider_models,
            _version_key,
        )

        mycelos = api.state.mycelos
        registered_rows = mycelos.storage.fetchall(
            "SELECT id, provider, tier FROM llm_models"
        )
        registered_ids = {r["id"] for r in registered_rows}

        # Group registered models by (provider, tier)
        by_bucket: dict[tuple[str, str], list[dict[str, Any]]] = {}
        for r in registered_rows:
            if not r.get("provider") or not r.get("tier"):
                continue
            by_bucket.setdefault((r["provider"], r["tier"]), []).append(dict(r))

        # Find candidate upgrades by inspecting providers we have.
        upgrades: list[dict[str, Any]] = []
        seen_provs: set[str] = set()
        for prov, tier in by_bucket:
            seen_provs.add(prov)

        for prov in seen_provs:
            try:
                catalog = get_provider_models(prov) or []
            except Exception:
                continue
            # Latest per tier from catalog.
            latest_per_tier: dict[str, str] = {}
            for m in catalog:
                if m.tier and m.tier not in latest_per_tier:
                    latest_per_tier[m.tier] = m.id

            for tier, latest_id in latest_per_tier.items():
                bucket = by_bucket.get((prov, tier), [])
                for row in bucket:
                    if row["id"] == latest_id:
                        continue
                    # Old version — check if 'latest' is genuinely newer
                    # by version key, not just a date-suffix sibling.
                    if _version_key(latest_id) >= _version_key(row["id"]):
                        # latest_id sorts later (= older with our negated
                        # version key) than the row → not an upgrade.
                        continue
                    if _is_date_only_bump(row["id"], latest_id):
                        continue
                    # Find which assignments still pin this old model.
                    rows = mycelos.storage.fetchall(
                        """SELECT a.agent_id, a.purpose, a.priority,
                                  COALESCE(g.name, a.agent_id) AS agent_name
                             FROM agent_llm_models a
                             LEFT JOIN agents g ON g.id = a.agent_id
                            WHERE a.model_id = ?""",
                        (row["id"],),
                    )
                    assignments = []
                    for slot in rows:
                        agent_id = slot["agent_id"]
                        purpose = slot.get("purpose") or "execution"
                        if agent_id is None:
                            label = f"System default · {purpose}"
                            key = f"system::{purpose}"
                        else:
                            label = f"{slot.get('agent_name') or agent_id} · {purpose}"
                            key = f"agent:{agent_id}:{purpose}"
                        assignments.append({
                            "key": key,
                            "label": label,
                            "agent_id": agent_id,
                            "purpose": purpose,
                            "priority": slot.get("priority", 1),
                        })
                    if not assignments:
                        # No live use of the old model — nothing to migrate.
                        continue
                    upgrades.append({
                        "old_id": row["id"],
                        "new_id": latest_id,
                        "tier": tier,
                        "provider": prov,
                        "new_already_registered": latest_id in registered_ids,
                        "assignments": assignments,
                    })

        upgrades.sort(key=lambda u: -len(u["assignments"]))
        return {"upgrades": upgrades}

    @api.post("/api/models/migrate")
    async def migrate_model(payload: dict[str, Any]) -> dict[str, Any]:
        """Replace one model with another across the selected assignment slots.

        Body: ``{"old_id": "...", "new_id": "...", "keys": [
            "system::execution", "agent:mycelos:execution", ...
        ]}``

        Atomic per-slot: if the new model isn't in the registry yet,
        register it first using the catalog metadata. Selected slots
        get re-pointed; unselected ones are left alone — that's the
        explicit-opt-out the user picked in the UI.
        """
        from mycelos.llm.providers import get_provider_models

        mycelos = api.state.mycelos
        old_id = payload.get("old_id") or ""
        new_id = payload.get("new_id") or ""
        keys = payload.get("keys") or []
        if not old_id or not new_id or not isinstance(keys, list):
            return JSONResponse(
                {"error": "old_id, new_id, and keys[] required"}, status_code=400
            )

        # Ensure the new model exists in llm_models — if the registry hasn't
        # synced it yet, pick the metadata from the catalog and register on
        # the fly so the assignment FK is satisfiable.
        if not mycelos.model_registry.get_model(new_id):
            provider = new_id.split("/", 1)[0] if "/" in new_id else ""
            target = None
            if provider:
                for m in get_provider_models(provider) or []:
                    if m.id == new_id:
                        target = m
                        break
            if target is None:
                return JSONResponse(
                    {"error": f"Cannot register unknown model '{new_id}'"},
                    status_code=400,
                )
            mycelos.model_registry.add_model(
                model_id=target.id,
                provider=target.provider,
                tier=target.tier,
                input_cost_per_1k=target.input_cost_per_1k,
                output_cost_per_1k=target.output_cost_per_1k,
                max_context=target.max_context,
            )

        # Apply the migration slot-by-slot.
        migrated: list[str] = []
        for key in keys:
            parts = key.split(":")
            if len(parts) != 3:
                continue
            kind, agent_id_raw, purpose = parts
            if kind not in ("agent", "system"):
                continue
            if kind == "system":
                # Replace any system-default row (agent_id IS NULL) that
                # currently points at old_id, preserving priority.
                rows = mycelos.storage.fetchall(
                    """SELECT priority FROM agent_llm_models
                        WHERE agent_id IS NULL AND purpose = ? AND model_id = ?""",
                    (purpose, old_id),
                )
                for r in rows:
                    mycelos.storage.execute(
                        """UPDATE agent_llm_models
                              SET model_id = ?
                            WHERE agent_id IS NULL
                              AND purpose = ?
                              AND model_id = ?
                              AND priority = ?""",
                        (new_id, purpose, old_id, r["priority"]),
                    )
                migrated.append(key)
            else:
                rows = mycelos.storage.fetchall(
                    """SELECT priority FROM agent_llm_models
                        WHERE agent_id = ? AND purpose = ? AND model_id = ?""",
                    (agent_id_raw, purpose, old_id),
                )
                for r in rows:
                    mycelos.storage.execute(
                        """UPDATE agent_llm_models
                              SET model_id = ?
                            WHERE agent_id = ? AND purpose = ?
                              AND model_id = ? AND priority = ?""",
                        (new_id, agent_id_raw, purpose, old_id, r["priority"]),
                    )
                migrated.append(key)

        mycelos.audit.log("models.migrated", details={
            "old_id": old_id,
            "new_id": new_id,
            "keys": migrated,
        })
        return {"status": "migrated", "old_id": old_id, "new_id": new_id, "keys": migrated}

    @api.put("/api/models/system-defaults")
    async def update_system_defaults(payload: dict[str, Any]) -> dict[str, Any]:
        """Replace the system-wide default model chain for a given purpose.

        Body: {"purpose": "execution" | "classification", "model_ids": [...]}
        System defaults are used when an agent has no explicit assignment
        (execution) or for background/cheapest-model calls (classification).
        """
        mycelos = api.state.mycelos
        purpose = payload.get("purpose")
        if purpose not in ("execution", "classification"):
            return JSONResponse(
                {"error": "purpose must be 'execution' or 'classification'"},
                status_code=400,
            )
        model_ids = payload.get("model_ids") or []
        if not isinstance(model_ids, list) or not all(isinstance(m, str) for m in model_ids):
            return JSONResponse({"error": "model_ids must be a list of strings"}, status_code=400)
        for model_id in model_ids:
            if not mycelos.model_registry.get_model(model_id):
                return JSONResponse(
                    {"error": f"Model '{model_id}' is not registered"}, status_code=400
                )
        # set_system_defaults rewrites ALL system-default purposes at once, so
        # we need to preserve the other purpose's chain alongside this update.
        other = "classification" if purpose == "execution" else "execution"
        other_chain = mycelos.model_registry.resolve_models(None, other)
        by_purpose = {purpose: model_ids}
        if other_chain:
            by_purpose[other] = other_chain
        mycelos.model_registry.set_system_defaults(by_purpose)
        return {"ok": True, "purpose": purpose, "model_ids": model_ids}

    @api.put("/api/models/assignments/{agent_id}")
    async def update_agent_assignments(agent_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Replace the model assignment list for one agent+purpose.

        Body: {"purpose": "execution", "model_ids": ["provider/model-a", "provider/model-b"]}
        Order is priority (first = highest).
        """
        mycelos = api.state.mycelos
        if not mycelos.agent_registry.get(agent_id):
            return JSONResponse({"error": f"Agent '{agent_id}' not found"}, status_code=404)
        purpose = payload.get("purpose", "execution")
        model_ids = payload.get("model_ids") or []
        if not isinstance(model_ids, list) or not all(isinstance(m, str) for m in model_ids):
            return JSONResponse({"error": "model_ids must be a list of strings"}, status_code=400)
        # Validate every model exists in the registry (fail-closed).
        for model_id in model_ids:
            if not mycelos.model_registry.get_model(model_id):
                return JSONResponse(
                    {"error": f"Model '{model_id}' is not registered"}, status_code=400
                )
        mycelos.model_registry.set_agent_models(agent_id, model_ids, purpose=purpose)
        return {"ok": True, "agent_id": agent_id, "purpose": purpose, "model_ids": model_ids}

    # ── Setup / Onboarding ─────────────────────────────────────

    @api.get("/api/setup/status")
    async def setup_status() -> dict[str, Any]:
        """Tell the frontend whether onboarding is still required."""
        from mycelos.setup import is_initialized
        mycelos = api.state.mycelos
        return {"initialized": is_initialized(mycelos)}

    @api.post("/api/setup")
    async def run_setup(body: dict[str, Any]) -> dict[str, Any]:
        """Run the web onboarding flow: credential + provider + models + agents."""
        from mycelos.setup import SetupError, web_init
        mycelos = api.state.mycelos
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

    @api.get("/api/credentials")
    async def list_credentials() -> list[dict[str, Any]]:
        """List credentials (service + label only, NO keys)."""
        mycelos = api.state.mycelos
        try:
            creds = mycelos.credentials.list_credentials()
            return creds
        except Exception:
            # Gateway mode — credentials managed by proxy
            services = mycelos.storage.fetchall(
                "SELECT service, label, description, created_at FROM credentials ORDER BY service"
            )
            return [dict(s) for s in services]

    @api.post("/api/credentials")
    async def add_credential(request: Request, body: CredentialAddRequest) -> dict[str, Any]:
        """Store a credential (encrypted)."""
        mycelos = api.state.mycelos
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

    @api.delete("/api/credentials/{service}")
    async def delete_credential(request: Request, service: str, label: str = "default") -> dict[str, Any]:
        """Delete a credential."""
        mycelos = api.state.mycelos
        try:
            mycelos.credentials.delete_credential(service, label=label)
            mycelos.audit.log("credential.deleted", details={"service": service, "label": label}, user_id=_resolve_user_id(request))
            return {"status": "deleted", "service": service, "label": label}
        except RuntimeError as e:
            return JSONResponse({"error": str(e)}, status_code=500)

    @api.post("/api/credentials/oauth-keys/validate")
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

    @api.post("/api/telegram/check")
    async def telegram_check(request: Request) -> dict[str, Any]:
        """Check for Telegram bot messages to detect chat ID.

        Validates the token via getMe, then tries getUpdates to find
        the user's chat ID. Handles conflict with running long-polling.
        Routed through the SecurityProxy in two-container mode — the
        gateway never opens a direct socket to api.telegram.org.
        """
        from mycelos.channels.telegram import call_telegram_api_with_token
        mycelos = api.state.mycelos
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
            tg_channel = getattr(api.state, "_telegram_channel", None)
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

    @api.post("/api/telegram/verify")
    async def telegram_verify(request: Request) -> dict[str, Any]:
        """Send a test message to verify the chat ID works.

        Routed through the SecurityProxy in two-container mode so the
        gateway never opens a direct socket to api.telegram.org.
        """
        from mycelos.channels.telegram import call_telegram_api_with_token
        mycelos = api.state.mycelos
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
                return {"error": "Chat ID not found. Make sure you sent /start to the bot first."}
            mycelos.audit.log(
                "telegram.setup.verify_failed", user_id="default", details={},
            )
            return {"error": _scrub_token(desc, token)}

        mycelos.audit.log("telegram.setup.verify_succeeded", user_id="default", details={})
        return {"ok": True, "message_id": data.get("result", {}).get("message_id")}

    # ── Memory (key-value) ──────────────────────────────────────

    @api.post("/api/memory")
    async def set_memory(request: Request) -> dict[str, Any]:
        """Set a memory entry."""
        mycelos = api.state.mycelos
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

    # ── Workflows ──────────────────────────────────────────────

    @api.get("/api/workflows")
    async def list_workflows() -> list[dict[str, Any]]:
        """List all workflows."""
        mycelos = api.state.mycelos
        return mycelos.workflow_registry.list_workflows()

    @api.get("/api/workflows/{workflow_id}/runs")
    async def list_workflow_runs(workflow_id: str) -> list[dict[str, Any]]:
        """List recent runs for a specific workflow."""
        mycelos = api.state.mycelos
        return mycelos.workflow_run_manager.list_runs(workflow_id=workflow_id, limit=20)

    @api.get("/api/workflow-runs/scheduled")
    async def list_scheduled_workflow_runs() -> list[dict[str, Any]]:
        """List active scheduled cron-triggered workflows for the sidebar."""
        mycelos = api.state.mycelos
        return mycelos.workflow_run_manager.list_scheduled()

    @api.get("/api/workflow-runs/{run_id}")
    async def get_workflow_run(run_id: str) -> dict[str, Any]:
        """Get a single workflow run with full details including parsed conversation."""
        mycelos = api.state.mycelos
        run = mycelos.workflow_run_manager.get(run_id)
        if not run:
            return JSONResponse({"error": "Run not found"}, status_code=404)
        # Parse conversation JSON for the detail view
        if run.get("conversation") and isinstance(run["conversation"], str):
            try:
                run["conversation"] = json.loads(run["conversation"])
            except (json.JSONDecodeError, TypeError):
                run["conversation"] = []
        # Sum tokens from conversation usage metadata if present
        total_tokens = 0
        for msg in (run.get("conversation") or []):
            usage = msg.get("usage") or {}
            total_tokens += usage.get("total_tokens", 0)
        run["total_tokens"] = total_tokens or None
        return run

    @api.get("/api/workflow-runs")
    async def list_all_workflow_runs(
        limit: int = 50,
        status: str | None = None,
    ) -> list[dict[str, Any]]:
        """List workflow runs across all workflows.

        Args:
            limit: Max rows (capped at 100).
            status: "active" returns running+paused+waiting_input runs with
                workflow_name joined (sidebar). Any other value is passed
                through as exact match. None returns all.
        """
        mycelos = api.state.mycelos
        if status == "active":
            rows = mycelos.storage.fetchall(
                """SELECT wr.*, w.name as workflow_name
                   FROM workflow_runs wr
                   LEFT JOIN workflows w ON wr.workflow_id = w.id
                   WHERE wr.status IN ('running', 'paused', 'waiting_input')
                   ORDER BY wr.updated_at DESC
                   LIMIT ?""",
                (min(limit, 100),),
            )
            return [dict(r) for r in rows]
        return mycelos.workflow_run_manager.list_runs(
            status=status, limit=min(limit, 100)
        )

    # ── End of API endpoints ───────────────────────────────────

