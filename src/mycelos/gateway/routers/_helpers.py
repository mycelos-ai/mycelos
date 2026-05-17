"""Shared helpers and request models for gateway route modules.

Lives under `routers/` so each per-domain APIRouter module can import the
small bits of glue (SSE error helper, user-id resolution, doc loader,
session-markdown renderer, Pydantic request bodies) without re-importing
the full `routes.py` monolith.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from fastapi import Request
from pydantic import BaseModel
from starlette.responses import StreamingResponse


def sse_error(session_id: str, message: str) -> StreamingResponse:
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
    env_vars: dict[str, str] | None = None  # multi-var path; wins over `secret`


class ConnectorEditRequest(BaseModel):
    """Request body for PATCH /api/connectors/{id}.

    All fields optional — only present keys get applied. Connector ID
    is immutable (it's the lookup key); use the URL path to identify
    the target. Renaming would cascade through tool prefixes, audit
    logs, and agent memory, so it's deliberately not supported.
    """
    name: str | None = None         # display name
    command: str | None = None      # MCP launch command (custom MCPs only)
    secret: str | None = None       # single-secret update (legacy path)
    env_vars: dict[str, str] | None = None  # multi-var update; wins over secret


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


def resolve_user_id(request: Request) -> str:
    """Resolve user ID from X-User-Id header, falling back to default user."""
    header_value = request.headers.get("X-User-Id")
    if header_value:
        return header_value
    return getattr(request.app.state, "default_user_id", "default")


def parse_frontmatter(text: str) -> tuple[dict, str]:
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


def list_docs(docs_dir: Path) -> list[dict]:
    """List all doc sections with metadata, sorted by order."""
    results = []
    if not docs_dir.is_dir():
        return results
    for md_file in sorted(docs_dir.glob("*.md")):
        meta, _ = parse_frontmatter(md_file.read_text(encoding="utf-8"))
        results.append({
            "slug": md_file.stem,
            "title": meta.get("title", md_file.stem),
            "description": meta.get("description", ""),
            "order": meta.get("order", 99),
            "icon": meta.get("icon", ""),
        })
    results.sort(key=lambda x: x["order"])
    return results


def get_doc(docs_dir: Path, slug: str) -> dict | None:
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
    meta, content = parse_frontmatter(md_file.read_text(encoding="utf-8"))
    return {
        "slug": slug,
        "title": meta.get("title", slug),
        "description": meta.get("description", ""),
        "content": content,
    }


def render_session_markdown(session_id: str, events: list[dict], session_store) -> str:
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
