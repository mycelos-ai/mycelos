"""Gateway HTTP routes — chat, health, config."""

from __future__ import annotations

import json
import logging
import re
import time
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import StreamingResponse

from mycelos.chat.events import ChatEvent

logger = logging.getLogger("mycelos.gateway")

_LOCALHOST_ADDRS = ("127.0.0.1", "::1")


def _sse_error(session_id: str, message: str) -> StreamingResponse:
    """Return an SSE stream with a single error event — DRY helper."""
    from mycelos.chat.events import session_event, error_event, done_event

    async def stream():
        yield session_event(session_id).to_sse()
        yield error_event(message).to_sse()
        yield done_event().to_sse()

    return StreamingResponse(
        stream(), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


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


class ChatRequest(BaseModel):
    """Request body for POST /api/chat."""
    message: str
    session_id: str | None = None
    user_id: str = "default"
    channel: str = "api"
    workflow_run_id: str | None = None
    target_agent_id: str | None = None


class ConfirmRequest(BaseModel):
    """Request body for POST /api/chat/confirm."""
    session_id: str
    task_id: str


class ConnectorAddRequest(BaseModel):
    """Request body for POST /api/connectors."""
    name: str
    command: str = ""
    secret: str | None = None


class CredentialAddRequest(BaseModel):
    """Request body for POST /api/credentials."""
    service: str
    label: str = "default"
    secret: str
    description: str | None = None


class SessionUpdateRequest(BaseModel):
    """Request body for PATCH /api/sessions/{id}."""
    title: str | None = None
    topic: str | None = None


class RollbackRequest(BaseModel):
    """Request body for POST /api/config/rollback."""
    generation_id: int


def _resolve_user_id(request: Request) -> str:
    """Resolve user ID from X-User-Id header, falling back to default user."""
    header_value = request.headers.get("X-User-Id")
    if header_value:
        return header_value
    return getattr(request.app.state, "default_user_id", "default")


def _parse_frontmatter(text: str) -> tuple[dict, str]:
    """Parse YAML frontmatter from Markdown text. Returns (metadata, content)."""
    if not text.startswith("---"):
        return {}, text
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}, text
    meta = {}
    for line in parts[1].strip().split("\n"):
        if ":" in line:
            key, _, value = line.partition(":")
            value = value.strip()
            if value.isdigit():
                value = int(value)
            meta[key.strip()] = value
    return meta, parts[2].lstrip("\n")


def _list_docs(docs_dir: Path) -> list[dict]:
    """List all doc sections with metadata, sorted by order."""
    results = []
    if not docs_dir.is_dir():
        return results
    for md_file in sorted(docs_dir.glob("*.md")):
        meta, _ = _parse_frontmatter(md_file.read_text(encoding="utf-8"))
        results.append({
            "slug": md_file.stem,
            "title": meta.get("title", md_file.stem),
            "description": meta.get("description", ""),
            "order": meta.get("order", 99),
            "icon": meta.get("icon", ""),
        })
    results.sort(key=lambda x: x["order"])
    return results


def _get_doc(docs_dir: Path, slug: str) -> dict | None:
    """Get a single doc by slug. Returns None if not found or invalid slug."""
    if not re.match(r"^[a-z0-9-]+$", slug):
        return None
    md_file = docs_dir / f"{slug}.md"
    if not md_file.is_file():
        return None
    try:
        md_file.resolve().relative_to(docs_dir.resolve())
    except ValueError:
        return None
    meta, content = _parse_frontmatter(md_file.read_text(encoding="utf-8"))
    return {
        "slug": slug,
        "title": meta.get("title", slug),
        "description": meta.get("description", ""),
        "content": content,
    }


def _render_session_markdown(session_id: str, events: list[dict], session_store) -> str:
    """Render a session's events as a chronological Markdown timeline."""
    meta = session_store.get_session_meta(session_id)
    title = meta.get("title") or f"Session {session_id[:8]}"

    lines = [
        f"# Session: {title}",
        f"**Session ID:** `{session_id}`",
        f"**Events:** {len(events)}",
        "",
        "---",
        "",
    ]

    for event in events:
        ts = event.get("timestamp", "")[:19].replace("T", " ")
        etype = event.get("type", "unknown")

        if etype == "message":
            role = event.get("role", "unknown")
            icon = "User" if role == "user" else "Assistant"
            lines.append(f"### {ts} · [{icon}]")
            content = event.get("content", "")
            lines.append(f"> {content}")
        elif etype == "llm_round":
            lines.append(f"### {ts} · LLM Round {event.get('round', '?')}")
            lines.append(f"**Model:** `{event.get('model', '')}`")
            lines.append(f"**Tokens:** {event.get('tokens_in', 0)} in / {event.get('tokens_out', 0)} out")
            lines.append(f"**Stop reason:** `{event.get('stop_reason', '')}`")
        elif etype == "tool_call":
            lines.append(f"### {ts} · Tool: {event.get('name', '')}")
            lines.append("**Args:**")
            lines.append("```json")
            lines.append(json.dumps(event.get("args", {}), indent=2, default=str))
            lines.append("```")
        elif etype == "tool_result":
            lines.append(f"**Result ({event.get('duration_ms', 0)}ms):**")
            lines.append("```json")
            lines.append(json.dumps(event.get("result", {}), indent=2, default=str))
            lines.append("```")
        elif etype == "tool_error":
            lines.append(f"### {ts} · Tool Error: {event.get('name', '')}")
            lines.append(f"**Error:** `{event.get('error', '')}`")
            if event.get("traceback"):
                lines.append("```")
                lines.append(event["traceback"])
                lines.append("```")

        lines.append("")

    return "\n".join(lines)


def setup_routes(api: FastAPI) -> None:
    """Register all gateway routes."""

    @api.post("/api/chat")
    async def chat(http_request: Request, request: ChatRequest) -> StreamingResponse:
        """Process a chat message and stream SSE response."""
        service = api.state.chat_service
        debug = getattr(api.state, "debug", False)

        # Security: resolve user_id from auth context, NOT from request body
        user_id = _resolve_user_id(http_request)

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
            mycelos = api.state.mycelos
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

            mycelos = api.state.mycelos

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

    @api.get("/api/health")
    async def health(request: Request) -> dict[str, Any]:
        """Health check endpoint — also exposes security status."""
        mycelos = api.state.mycelos
        uptime = time.time() - api.state.start_time
        bind_host = getattr(api.state, "bind_host", "127.0.0.1")
        password_protected = getattr(api.state, "password_protected", False)
        network_exposed = bind_host not in _LOCALHOST_ADDRS
        return {
            "status": "ok",
            "uptime_seconds": round(uptime, 1),
            "generation_id": mycelos.config.get_active_generation_id(),
            "scheduler": getattr(api.state, "scheduler_running", False),
            "user": getattr(api.state, "default_user", {"id": "default", "name": "Default User"}),
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

    @api.get("/api/config")
    async def config() -> dict[str, Any]:
        """Return current config state snapshot."""
        mycelos = api.state.mycelos
        return mycelos.state_manager.snapshot()

    @api.get("/api/i18n")
    async def i18n() -> dict[str, Any]:
        """Return web UI translations for the active language."""
        from mycelos.i18n import get_language, get_web_translations

        lang = get_language()
        translations = get_web_translations(lang)
        return {"lang": lang, "translations": translations}

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

    @api.get("/api/admin/doctor")
    async def admin_doctor() -> list[dict[str, Any]]:
        """Run the read-only health-check suite and return structured results.

        This is the web-UI equivalent of ``mycelos doctor`` without --fix or
        --why — no state mutation, no LLM, no subprocess execution. The
        server-reachability check is skipped because we *are* the server.
        """
        from mycelos.doctor.checks import run_health_checks
        mycelos = api.state.mycelos
        return run_health_checks(mycelos, gateway_url=None)

    @api.post("/api/admin/inbox/dismiss")
    async def admin_inbox_dismiss(request: Request) -> Any:
        """Mark a reminder as handled without firing it.

        Body: ``{"path": "tasks/..."}``. Used when the user clicks an
        inbox entry — we stamp ``reminder_fired_at = now`` so both the
        bell and the scheduler stop showing it, and emit a
        ``reminder.dismissed`` audit event so history can distinguish
        user-dismissed from scheduler-fired.
        """
        from mycelos.knowledge.reminder import ReminderService
        body = await request.json()
        path = (body or {}).get("path")
        if not path:
            return JSONResponse({"error": "path is required"}, status_code=422)
        mycelos = api.state.mycelos
        ok = ReminderService(mycelos).mark_dismissed(path, trigger="user")
        if not ok:
            return JSONResponse({"error": "not found or already dismissed"}, status_code=404)
        return {"status": "dismissed", "path": path}

    @api.get("/api/admin/inbox")
    async def admin_inbox() -> dict[str, Any]:
        """Aggregate "needs attention" items for the header bell dropdown.

        Returns three lists:

        * ``reminders``: active knowledge-base tasks with ``reminder=1``
        * ``waiting_workflows``: workflow runs in ``waiting_input``
        * ``failed_workflows``: workflow runs that failed in the last 24h

        Plus a ``total`` convenience counter so the bell badge knows
        whether to show the red indicator. Purely read-only — no state
        mutation, safe to poll.
        """
        mycelos = api.state.mycelos

        from mycelos.knowledge.reminder import ReminderService
        reminders = ReminderService(mycelos).get_due_reminders_now()[:20]

        waiting_rows = mycelos.storage.fetchall(
            """SELECT wr.id, wr.workflow_id, wr.status, wr.clarification,
                      wr.updated_at, w.name AS workflow_name
               FROM workflow_runs wr
               LEFT JOIN workflows w ON wr.workflow_id = w.id
               WHERE wr.status = 'waiting_input'
               ORDER BY wr.updated_at DESC
               LIMIT 20""",
        )
        waiting_workflows = [dict(r) for r in waiting_rows]

        failed_rows = mycelos.storage.fetchall(
            """SELECT wr.id, wr.workflow_id, wr.status, wr.error,
                      wr.updated_at, w.name AS workflow_name
               FROM workflow_runs wr
               LEFT JOIN workflows w ON wr.workflow_id = w.id
               WHERE wr.status = 'failed'
                 AND wr.updated_at >= datetime('now', '-1 day')
               ORDER BY wr.updated_at DESC
               LIMIT 20""",
        )
        failed_workflows = [dict(r) for r in failed_rows]

        return {
            "reminders": reminders,
            "waiting_workflows": waiting_workflows,
            "failed_workflows": failed_workflows,
            "total": len(reminders) + len(waiting_workflows) + len(failed_workflows),
        }

    @api.get("/api/notifications/pending")
    async def notifications_pending() -> dict[str, Any]:
        """Return and clear any pending in-browser notifications.

        The Reminder service writes reminders to memory under
        system/pending_reminder. The chat page polls this endpoint every
        ~20s; when something is there we return it and delete it in the
        same call so it's delivered exactly once.
        """
        mycelos = api.state.mycelos
        try:
            msg = mycelos.memory.get("default", "system", "pending_reminder")
        except Exception:
            msg = None
        if not msg:
            return {"reminder": None}
        try:
            mycelos.memory.delete("default", "system", "pending_reminder")
        except Exception:
            pass
        return {"reminder": msg}

    @api.get("/api/reminders/upcoming")
    async def reminders_upcoming(limit: int = 10) -> list[dict[str, Any]]:
        """Active reminder-notes with a due date, earliest first.

        Used by the sidebar "Reminders" block. Notes without a due date
        are excluded (they can't be scheduled). Done/cancelled notes are
        excluded via status filter.
        """
        rows = api.state.mycelos.storage.fetchall(
            """SELECT path, title, due, remind_via
               FROM knowledge_notes
               WHERE reminder = 1
                 AND status = 'active'
                 AND due IS NOT NULL
               ORDER BY due ASC
               LIMIT ?""",
            (limit,),
        )
        return [dict(r) for r in rows]

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
        """Serve an original document file (PDF, DOCX, etc.)."""
        from starlette.responses import FileResponse
        kb = api.state.mycelos.knowledge_base
        doc_path = kb.get_document_path(path)
        if not doc_path:
            return JSONResponse({"error": "not found"}, status_code=404)
        return FileResponse(str(doc_path), filename=doc_path.name)

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
        session_id: str = "",
    ) -> StreamingResponse:
        """Accept file upload, save to inbox, extract and analyze."""
        from mycelos.chat.events import system_response_event, error_event, done_event, session_event

        service = api.state.chat_service
        mycelos = api.state.mycelos
        user_id = _resolve_user_id(request)

        if not session_id:
            session_id = service.create_session(user_id=user_id)

        file_bytes = await file.read()

        # Size check
        if len(file_bytes) > 50 * 1024 * 1024:
            async def too_large():
                yield error_event("File too large (max 50MB)").to_sse()
                yield done_event().to_sse()
            return StreamingResponse(too_large(), media_type="text/event-stream")

        # Save to inbox
        from mycelos.files.inbox import InboxManager
        inbox = InboxManager(mycelos.data_dir / "inbox")
        try:
            inbox_path = inbox.save(file_bytes, file.filename or "unnamed")
        except ValueError as e:
            async def save_error():
                yield error_event(str(e)).to_sse()
                yield done_event().to_sse()
            return StreamingResponse(save_error(), media_type="text/event-stream")

        # PDF/DOCX → Knowledge ingest
        suffix = inbox_path.suffix.lower()
        if suffix in ('.pdf', '.docx', '.doc'):
            from mycelos.knowledge.ingest import ingest_pdf
            result = ingest_pdf(mycelos, inbox_path)

            async def doc_stream():
                yield session_event(session_id).to_sse()
                if result["vision_needed"]:
                    yield system_response_event(
                        f"📄 Document saved to Knowledge Base: {file.filename}\n"
                        f"({result['page_count']} pages, no text layer)\n\n"
                        f"Shall I analyze it with Vision? (~${result['page_count'] * 0.02:.2f})"
                    ).to_sse()
                else:
                    yield system_response_event(
                        f"📄 Document saved to Knowledge Base: {file.filename}\n"
                        "Summary note created. The organizer will classify it into a topic."
                    ).to_sse()
                yield done_event().to_sse()
            return StreamingResponse(doc_stream(), media_type="text/event-stream")

        # Extract text
        from mycelos.files.extractor import extract_text
        text, method = extract_text(inbox_path)

        if method == "vision_needed":
            async def vision_prompt():
                yield session_event(session_id).to_sse()
                yield system_response_event(
                    f"File saved: {inbox_path.name}\nShall I analyze the image? (~$0.01)"
                ).to_sse()
                yield done_event().to_sse()
            return StreamingResponse(vision_prompt(), media_type="text/event-stream")

        if text:
            message_text = f"[File: {file.filename or inbox_path.name}] Analyze this document:\n\n{text[:2000]}"
            try:
                events = service.handle_message(message_text, session_id, user_id)
            except Exception:
                events = [error_event("Analysis failed."), done_event()]

            async def stream():
                yield session_event(session_id).to_sse()
                for event in events:
                    yield event.to_sse()
            return StreamingResponse(stream(), media_type="text/event-stream",
                                     headers={"Cache-Control": "no-cache"})

        # No text extracted
        async def no_text():
            yield session_event(session_id).to_sse()
            yield system_response_event(f"File saved to inbox: {inbox_path.name}").to_sse()
            yield done_event().to_sse()
        return StreamingResponse(no_text(), media_type="text/event-stream")

    # ── Connectors ────────────────────────────────────────────

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
        logger.info(
            "add_connector: name=%s has_secret=%s secret_len=%d",
            body.name,
            bool(body.secret),
            len(body.secret) if body.secret else 0,
        )
        if body.secret:
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
                logger.info("add_connector: store_credential returned OK for %s", body.name)
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
            logger.info("add_connector: no secret provided for %s — skipping store", body.name)

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
            return _fail(
                "No tools discovered. The MCP session may not be running — "
                "check 'mycelos logs gateway' for startup errors."
            )

        return {
            "ok": None,
            "connector": connector_id,
            "detail": "No test available for this connector type.",
        }

    # ── Channels ───────────────────────────────────────────────

    @api.post("/api/channels")
    async def setup_channel(request: Request) -> dict[str, Any]:
        """Register a channel (Telegram, Slack) in the channels table + connector registry.

        This mirrors what the CLI does in connector_cmd.py _setup_telegram().
        Body: { "id": "telegram", "mode": "polling", "allowed_users": ["123"], "config": {} }
        """
        import json as _json

        mycelos = api.state.mycelos
        body = await request.json()
        channel_id = body.get("id", "")
        channel_type = body.get("type", channel_id)
        mode = body.get("mode", "polling")
        status = body.get("status", "active")
        allowed_users = body.get("allowed_users", [])
        config = body.get("config", {})

        if not channel_id:
            return JSONResponse({"error": "Channel ID required"}, status_code=400)

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
        }, user_id=_resolve_user_id(request))

        # New config generation
        mycelos.config.apply_from_state(
            state_manager=mycelos.state_manager,
            description=f"{channel_id.title()} channel configured (mode={mode})",
            trigger="channel_setup",
        )

        return {"status": "configured", "channel": channel_id}

    # ── Agents ─────────────────────────────────────────────────

    # Single source of truth: an agent is "conversational" iff the user
    # can actually chat with it. Internal handlers are excluded. Both
    # /api/agents (tagging) and /api/agents/conversational (listing) use
    # this set so the admin page, sidebar, and chat picker cannot drift.
    _INTERNAL_HANDLERS = frozenset({
        "mycelos", "builder", "workflow-agent",
        "evaluator-agent", "auditor-agent",
    })

    def _is_conversational(agent: dict[str, Any]) -> bool:
        return (
            agent.get("status") == "active"
            and bool(agent.get("user_facing"))
            and agent.get("id") not in _INTERNAL_HANDLERS
        )

    @api.get("/api/agents")
    async def list_agents() -> list[dict[str, Any]]:
        """List all agents with status, capabilities, type.

        Each entry is tagged with ``conversational`` so the admin page can
        decide whether to show a "Chat with" button without duplicating
        the conversational-agent rules.
        """
        mycelos = api.state.mycelos
        agents = mycelos.agent_registry.list_agents()
        for agent in agents:
            agent["conversational"] = _is_conversational(agent)
        return agents

    @api.get("/api/agents/conversational")
    async def list_conversational_agents() -> list[dict[str, Any]]:
        """List agents the user can actually chat with.

        Used by the sidebar and the chat agent picker.
        """
        mycelos = api.state.mycelos
        agents = mycelos.agent_registry.list_agents()
        return [a for a in agents if _is_conversational(a)]

    @api.patch("/api/agents/{agent_id}")
    async def update_agent(agent_id: str, body: dict[str, Any]) -> dict[str, Any]:
        """Edit an agent: display_name (always safe) or persona (advanced).

        Body fields (all optional):
          - display_name: str
          - system_prompt: str
          - model: str
          - allowed_tools: list[str]
        """
        mycelos = api.state.mycelos
        agent = mycelos.agent_registry.get(agent_id)
        if not agent:
            raise HTTPException(status_code=404, detail="Agent not found")

        result: dict[str, Any] = {"ok": True, "id": agent_id}

        if "display_name" in body:
            new_name = (body.get("display_name") or "").strip()
            if not new_name:
                raise HTTPException(status_code=400, detail="display_name must not be empty")
            mycelos.agent_registry.rename(agent_id, new_name)
            mycelos.audit.log("agent.renamed", details={"agent_id": agent_id, "display_name": new_name})
            result["display_name"] = new_name

        persona_fields = {}
        if "system_prompt" in body:
            persona_fields["system_prompt"] = body["system_prompt"]
        if "model" in body:
            persona_fields["model"] = body["model"]
        if "allowed_tools" in body:
            tools = body["allowed_tools"]
            if not isinstance(tools, list):
                raise HTTPException(status_code=400, detail="allowed_tools must be a list")
            persona_fields["allowed_tools"] = tools

        if persona_fields:
            info = mycelos.agent_registry.update_persona_fields(
                agent_id,
                audit=mycelos.audit,
                actor="web-ui",
                **persona_fields,
            )
            result["changed"] = info["changed"]

        return result

    @api.get("/api/agents/{agent_id}/history")
    async def agent_history(agent_id: str, limit: int = 10) -> list[dict[str, Any]]:
        """Return the persona change history (for Advanced → History tab)."""
        mycelos = api.state.mycelos
        return mycelos.agent_registry.persona_history(agent_id, limit=limit)

    @api.get("/api/agents/{agent_id}")
    async def get_agent(agent_id: str) -> dict[str, Any]:
        """Agent detail with code, tests, gherkin from ObjectStore."""
        mycelos = api.state.mycelos
        agent = mycelos.agent_registry.get(agent_id)
        if not agent:
            return JSONResponse({"error": "Agent not found"}, status_code=404)
        # Load code from object store
        from mycelos.storage.object_store import ObjectStore
        obj_store = ObjectStore(mycelos.data_dir)
        code_data = mycelos.agent_registry.get_code(agent_id, obj_store)
        return {**dict(agent), "code": code_data}

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

    # ── Cost ───────────────────────────────────────────────────

    @api.get("/api/cost")
    async def get_cost(period: str = "today") -> dict[str, Any]:
        """Token usage aggregated by period (today, week, month, all)."""
        mycelos = api.state.mycelos
        from datetime import datetime, timedelta, timezone
        now = datetime.now(timezone.utc)
        if period == "today":
            since = now.replace(hour=0, minute=0, second=0, microsecond=0)
        elif period == "week":
            since = now - timedelta(days=7)
        elif period == "month":
            since = now - timedelta(days=30)
        else:
            since = datetime(2020, 1, 1, tzinfo=timezone.utc)

        rows = mycelos.storage.fetchall(
            "SELECT model, SUM(input_tokens) as input_tokens, SUM(output_tokens) as output_tokens, "
            "SUM(total_tokens) as total_tokens, SUM(cost) as total_cost, COUNT(*) as calls "
            "FROM llm_usage WHERE created_at >= ? GROUP BY model ORDER BY total_cost DESC",
            (since.isoformat(),),
        )
        return {
            "period": period,
            "since": since.isoformat(),
            "models": [dict(r) for r in rows],
            "total_cost": sum(r["total_cost"] or 0 for r in rows),
            "total_tokens": sum(r["total_tokens"] or 0 for r in rows),
        }

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

    # ── Config rollback ────────────────────────────────────────

    @api.post("/api/config/rollback")
    async def config_rollback(request: Request, body: RollbackRequest) -> dict[str, Any]:
        """Rollback to a specific config generation."""
        mycelos = api.state.mycelos
        try:
            new_gen = mycelos.config.rollback(
                to_generation=body.generation_id,
                state_manager=mycelos.state_manager,
            )
            mycelos.audit.log("config.rollback", details={
                "target_generation": body.generation_id,
                "active_generation": new_gen,
            }, user_id=_resolve_user_id(request))
            return {"status": "rolled_back", "active_generation": new_gen}
        except Exception as e:
            return JSONResponse({"error": str(e)}, status_code=400)

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

    @api.get("/api/config/generations")
    async def config_generations() -> dict[str, Any]:
        """List config generations with active marker."""
        mycelos = api.state.mycelos
        generations = mycelos.storage.fetchall(
            "SELECT id, description, trigger, created_at FROM config_generations ORDER BY id DESC LIMIT 50"
        )
        active_id = None
        active_row = mycelos.storage.fetchone("SELECT generation_id FROM active_generation")
        if active_row:
            active_id = active_row["generation_id"]
        return {
            "active_id": active_id,
            "generations": [
                {**dict(g), "is_active": g["id"] == active_id}
                for g in generations
            ],
        }

    @api.get("/api/schedules")
    async def list_schedules() -> list[dict[str, Any]]:
        """List all scheduled tasks."""
        mycelos = api.state.mycelos
        rows = mycelos.storage.fetchall(
            "SELECT id, workflow_id, schedule, status, last_run, next_run, run_count, budget_per_run, created_at "
            "FROM scheduled_tasks ORDER BY status, next_run"
        )
        return [dict(r) for r in rows]

    # ── End of API endpoints ───────────────────────────────────

    @api.post("/telegram/webhook")
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

    @api.get("/api/docs")
    async def list_docs():
        docs_dir = Path(__file__).parent.parent.parent.parent / "docs" / "website"
        docs_dir = docs_dir.resolve()
        return _list_docs(docs_dir)

    @api.get("/api/docs/{slug}")
    async def get_doc(slug: str):
        docs_dir = Path(__file__).parent.parent.parent.parent / "docs" / "website"
        docs_dir = docs_dir.resolve()
        result = _get_doc(docs_dir, slug)
        if result is None:
            return JSONResponse(status_code=404, content={"error": "Not found"})
        return result
