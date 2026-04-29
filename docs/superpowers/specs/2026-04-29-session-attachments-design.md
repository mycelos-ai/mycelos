# Session Attachments — Design

**Date:** 2026-04-29
**Status:** Draft
**Scope:** Replace the marker-based document-upload pipeline with native Anthropic Multi-Part user messages. Files uploaded during a chat live in a per-session folder, ride along in every LLM call as Document/Image content, and only get persisted to the Knowledge Base when the agent decides via the new `note_save_attachment` tool. Cleanup all the marker plumbing accumulated over the last week — it's obsolete.

## Problem

Over the past few sessions we built a `[System: User uploaded ...]` marker pipeline: PDFs auto-ingested into the Knowledge Base, a marker persisted into the chat history, prompt sections explaining how to interpret it, marker-to-system-prompt promotion at LLM-call time. Each fix added more layers because Sonnet kept ignoring the markers as flavor text in user-role messages.

Even after all the patches, the agent still answered "I don't see a document" when a PDF was clearly attached. The fundamental issue: the agent never saw the document — it saw a *description* of the document.

Native Anthropic Multi-Part content fixes this at the root. Documents go directly into the user message; the model reads them with its own eyes. No markers, no prompt engineering, no "REQUIRED ACTION" pleading.

## Goal

User uploads a PDF / image. Same conversation, the file is in the LLM's context — the model can answer questions about it directly, with full text + visual fidelity. The file lives in a per-session folder for the lifetime of the session. If the user wants to keep it longer, the agent calls `note_save_attachment` and the file (with summary) lands in the Knowledge Base.

## Decisions

### D1: Files live in per-session folders

Path: `~/.mycelos/sessions/<session_id>/attachments/<filename>`. Per-session subfolder, mkdir on first save. Path-traversal-safe (filename sanitized via the same approach `InboxManager.sanitize_filename` uses today). Filename collisions get suffixed: `foo.pdf` → `foo-2.pdf`.

When a session is deleted, the folder is removed via `shutil.rmtree`. When a session is archived, the folder stays — agents can still load attachments from archived sessions if the user resumes.

### D2: Files ride along in EVERY LLM call

The ChatService builds the LLM message array per turn. For every active attachment in `~/.mycelos/sessions/<id>/attachments/`, the file is converted to Anthropic Multi-Part content and prepended to the *last* user message:

```python
{"role": "user", "content": [
    {"type": "document", "source": {"type": "base64", "media_type": "application/pdf", "data": "..."}},
    {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "..."}},
    {"type": "text", "text": "[current time: 2026-04-29 14:00]\nWas steht da drin?"},
]}
```

Document content goes first, then image content, then the text turn. Order is deterministic by upload-time (oldest first).

This adds tokens to every turn, but the user pays for what they get — the agent can re-read the document at any time, no prompt engineering needed.

### D3: Token-budget eviction

The model context window is finite. If `estimate_tokens(messages + attachments) > 0.85 * model_window`, evict the oldest attachments first until we fit. Each evicted attachment is replaced in the message stream by a text stub:

```
[Attachment "old.pdf" parked — call attachment_load('old.pdf') to bring it back into context]
```

Eviction order: oldest upload first. The session folder is unchanged — the file is still on disk, just not in the LLM context for this turn.

### D4: Two new agent tools

**`note_save_attachment(filename, summary, tags)`** — promotes a session attachment to the Knowledge Base.

- Reads bytes from `~/.mycelos/sessions/<session_id>/attachments/<filename>`
- Calls `kb.store_document(file_bytes, filename, title, summary, tags)` — same path that `ingest_pdf` uses today, just driven by the agent's already-formed summary instead of a fresh LLM round
- Returns `{path: <note_path>, status: "saved"}`
- The original file stays in the session folder until the session is deleted (so the agent can keep referencing it during the conversation)

**`attachment_load(filename)`** — forces an evicted attachment back into the active context.

- Sets a session-scoped flag `force_include` for the named file
- Next turn's eviction check skips this attachment even if budget pressure
- Returns `{filename, status: "loaded"}`
- Agent uses this when it remembers it needs an old attachment ("zoom into Tabelle 3 from the report we discussed")

Both tools are `ToolPermission.STANDARD`, `category="core"` (always available, no basis-set discovery delay).

### D5: Telegram parity

Telegram channel handles `F.document` and `F.photo` messages by saving to the same `SessionAttachmentStore`. Two flows depending on whether a caption is present:

- **With caption:** Caption becomes the user message text. Standard `ChatService.handle_message` flow — the Multi-Part-build picks up the new attachment.
- **Without caption:** Set `session_meta.pending_attachment = filename`, reply "Was möchtest du wissen?". Next text-only message from the user clears the flag and runs as normal — the attachment is already in the session folder, so the Multi-Part build naturally includes it.

Telegram session id ↔ telegram chat id mapping uses the existing `_resolve_user_id` / channel-state machinery; no schema changes needed.

### D6: Size limits

Hard limits per Anthropic's caps:

- PDFs (`.pdf`): 32 MB
- Images (`.png`, `.jpg`, `.jpeg`, `.webp`, `.gif`): 5 MB
- Other file types: 10 MB max, but ONLY if textually extractable (`.txt`, `.md`, `.csv`, `.json`, `.yaml`); they get inlined as text content, not as document/image. Other binary types reject with a clear error.

Frontend pre-validates the size before POST to give immediate feedback; backend re-validates as defense-in-depth.

### D7: Cleanup — what flies out

- `/api/upload` no longer ingests PDFs into Knowledge Base, no longer writes the `[System: User uploaded ...]` marker into session history. It just saves the file to the session folder and emits the `file-attached` SSE event. The whole vision-needed/text-extraction-pipeline branch goes away.
- `telegram.py` `handle_document` and `handle_photo` no longer call `ingest_pdf` or extract text. They save to session folder and trigger a chat turn.
- `ChatService`: the marker-detection logic (scanning user messages for `[System:` prefix and promoting them to system role / appending to system prompt) is removed.
- `mycelos.md` prompt: the "## File uploads" section that explains markers is replaced by a new section explaining the Multi-Part flow + the two tools.
- `routes.py` `/api/inbox/<filename>` endpoint is replaced by `/api/sessions/<session_id>/attachments/<filename>` (path-traversal-safe).
- `mycelos/files/inbox.py` `InboxManager` stays for now — it's still used by other code paths (e.g. legacy command-line ingestion). Not in scope to remove.
- `ingest_pdf` function in `mycelos.knowledge.ingest` stays — `note_save_attachment` calls it (or a slimmer variant of its store-document path).

### D8: No frontend session-attachment UI surface beyond the existing preview

The chat page already renders attachment cards in user bubbles (image inline, PDF as card with Open). The only change is the URL these cards point at: `/api/sessions/<id>/attachments/<filename>` instead of `/api/inbox/<filename>`. Backend's `file-attached` event already carries `url`, so the frontend just renders what it gets.

No "list all attachments in this session" UI panel for now. Agent does that via tool calls if needed.

## Architecture

```
┌──────────────────────────────────────────────────────┐
│ src/mycelos/files/session_attachments.py (NEW)       │
│   class SessionAttachmentStore                       │
│     save(session_id, bytes, filename) -> Path        │
│     list(session_id) -> list[Path]                   │
│     read(session_id, filename) -> bytes              │
│     delete_session(session_id) -> None               │
│     media_type(filename) -> str                      │
└──────────────────────────────────────────────────────┘
                       ▲
                       │
┌──────────────────────┴───────────────────────────────┐
│ Producers                                             │
│   POST /api/upload (Web UI)                          │
│   telegram.py handle_document / handle_photo         │
└──────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────┐
│ Consumer: ChatService.handle_message                 │
│   - load conversation                                │
│   - SessionAttachmentStore.list(session_id)          │
│   - if force_include set, prioritize that file       │
│   - build Multi-Part user message content            │
│   - token-budget check → evict oldest if needed      │
│   - LLM call                                         │
└──────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────┐
│ Agent tools (NEW)                                    │
│ src/mycelos/tools/attachments.py                     │
│   note_save_attachment(filename, summary, tags)      │
│     -> kb.store_document(...)                        │
│   attachment_load(filename)                          │
│     -> set session.force_include flag                │
└──────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────┐
│ Endpoint (NEW): GET /api/sessions/{id}/attachments/  │
│   {filename}                                          │
│   path-traversal-safe FileResponse                   │
└──────────────────────────────────────────────────────┘
```

## Components

### `src/mycelos/files/session_attachments.py` (new, ~80 lines)

```python
"""Per-session attachment storage. Files live as long as the session does."""

from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path

from mycelos.files.inbox import sanitize_filename, MAX_FILE_SIZE


# Per-type size caps — match Anthropic's Multi-Part content limits.
SIZE_CAPS_BYTES: dict[str, int] = {
    "pdf": 32 * 1024 * 1024,
    "image": 5 * 1024 * 1024,
    "text": 10 * 1024 * 1024,
}


class SessionAttachmentStore:
    """File store scoped to a single session.

    Each session gets its own subfolder under sessions/<id>/attachments/.
    Folders are created lazily and removed wholesale when the session is
    deleted.
    """

    def __init__(self, base_dir: Path) -> None:
        # base_dir = ~/.mycelos/sessions/
        self._base_dir = base_dir

    def _session_dir(self, session_id: str) -> Path:
        return self._base_dir / session_id / "attachments"

    def save(self, session_id: str, data: bytes, filename: str) -> Path:
        """Save bytes under filename in this session's attachment folder.

        Raises ValueError on path traversal, filename collision after
        suffix-attempts, or empty data.
        """
        if not data:
            raise ValueError("empty file")
        safe_name = sanitize_filename(filename)
        target_dir = self._session_dir(session_id)
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / safe_name
        # Path containment check
        resolved = target.resolve()
        if not resolved.is_relative_to(target_dir.resolve()):
            raise ValueError("path traversal blocked")
        # Collision handling
        if target.exists():
            stem, suffix = target.stem, target.suffix
            counter = 2
            while target.exists():
                target = target_dir / f"{stem}-{counter}{suffix}"
                counter += 1
        target.write_bytes(data)
        return target

    def list(self, session_id: str) -> list[Path]:
        """Return all attachments in upload order (oldest first)."""
        d = self._session_dir(session_id)
        if not d.exists():
            return []
        # Sort by mtime ascending — that's our upload order.
        return sorted(
            (p for p in d.iterdir() if p.is_file()),
            key=lambda p: p.stat().st_mtime,
        )

    def read(self, session_id: str, filename: str) -> bytes:
        """Read bytes for a known file. Raises FileNotFoundError otherwise."""
        target = self._session_dir(session_id) / sanitize_filename(filename)
        return target.read_bytes()

    def delete_session(self, session_id: str) -> None:
        """Remove the entire attachments folder for this session."""
        d = self._session_dir(session_id).parent  # sessions/<id>/
        if d.exists():
            shutil.rmtree(d, ignore_errors=True)


def media_type(path: Path) -> str:
    """Map file suffix to MIME type for Anthropic Multi-Part content."""
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return "application/pdf"
    if suffix in (".jpg", ".jpeg"):
        return "image/jpeg"
    if suffix == ".png":
        return "image/png"
    if suffix == ".webp":
        return "image/webp"
    if suffix == ".gif":
        return "image/gif"
    if suffix in (".txt", ".md"):
        return "text/plain"
    return "application/octet-stream"


def content_kind(path: Path) -> str:
    """Return 'document', 'image', 'text', or 'unsupported'."""
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return "document"
    if suffix in (".jpg", ".jpeg", ".png", ".webp", ".gif"):
        return "image"
    if suffix in (".txt", ".md", ".csv", ".json", ".yaml", ".yml"):
        return "text"
    return "unsupported"
```

### `src/mycelos/gateway/routes.py`

`POST /api/upload` becomes radically simpler:

```python
@api.post("/api/upload")
async def handle_upload(
    request: Request,
    file: UploadFile,
    session_id: str = "",
) -> StreamingResponse:
    from mycelos.chat.events import (
        error_event, done_event, session_event, file_attached_event,
    )
    from mycelos.files.session_attachments import (
        SessionAttachmentStore, content_kind, SIZE_CAPS_BYTES,
    )

    service = api.state.chat_service
    mycelos = api.state.mycelos
    user_id = _resolve_user_id(request)

    if not session_id:
        session_id = service.create_session(user_id=user_id)

    file_bytes = await file.read()
    inbox_path_repr = file.filename or "unnamed"

    # Per-type size validation
    from pathlib import Path as _Path
    kind = content_kind(_Path(inbox_path_repr))
    if kind == "unsupported":
        async def err():
            yield error_event(
                f"Unsupported file type for chat attachments: {inbox_path_repr}"
            ).to_sse()
            yield done_event().to_sse()
        return StreamingResponse(err(), media_type="text/event-stream")

    cap = SIZE_CAPS_BYTES.get(kind, 0)
    if cap and len(file_bytes) > cap:
        async def too_large():
            yield error_event(
                f"File too large ({len(file_bytes)} bytes > {cap} for {kind})"
            ).to_sse()
            yield done_event().to_sse()
        return StreamingResponse(too_large(), media_type="text/event-stream")

    store = SessionAttachmentStore(mycelos.data_dir / "sessions")
    saved = store.save(session_id, file_bytes, inbox_path_repr)

    preview = file_attached_event(
        filename=saved.name,
        url=f"/api/sessions/{session_id}/attachments/{saved.name}",
        kind={"document": "pdf", "image": "image", "text": "other"}.get(kind, "other"),
        size=len(file_bytes),
    )

    async def stream():
        yield session_event(session_id).to_sse()
        yield preview.to_sse()
        yield done_event().to_sse()
    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache"})
```

The big block of `if pdf: ingest, marker, vision_needed, ...` is gone.

New endpoint `GET /api/sessions/{session_id}/attachments/{filename:path}`:

```python
@api.get("/api/sessions/{session_id}/attachments/{filename:path}")
async def serve_session_attachment(session_id: str, filename: str) -> Any:
    from starlette.responses import FileResponse
    mycelos = api.state.mycelos
    base = (mycelos.data_dir / "sessions" / session_id / "attachments").resolve()
    target = (base / filename).resolve()
    try:
        target.relative_to(base)
    except ValueError:
        return JSONResponse({"error": "path traversal"}, status_code=400)
    if not target.is_file():
        return JSONResponse({"error": "not found"}, status_code=404)
    return FileResponse(str(target), filename=target.name)
```

The old `/api/inbox/<filename>` endpoint is removed.

### `src/mycelos/chat/service.py`

In `handle_message`, after the conversation is hydrated:

```python
# Build attachments content for THIS turn.
from mycelos.files.session_attachments import (
    SessionAttachmentStore, content_kind, media_type,
)
import base64

store = SessionAttachmentStore(self._app.data_dir / "sessions")
attachments = store.list(session_id)

# force_include flag set by attachment_load tool
force_include: set[str] = self._session_force_include.pop(session_id, set())

# Token budget check — keep approx 15% headroom for the model's reply
# plus tool calls. Eviction priority: oldest first, except force_include
# files always stay.
attachment_blocks: list[dict] = []
for path in attachments:
    kind = content_kind(path)
    if kind == "unsupported":
        continue
    data_b64 = base64.b64encode(path.read_bytes()).decode("ascii")
    if kind == "document":
        attachment_blocks.append({
            "type": "document",
            "source": {"type": "base64", "media_type": media_type(path), "data": data_b64},
        })
    elif kind == "image":
        attachment_blocks.append({
            "type": "image",
            "source": {"type": "base64", "media_type": media_type(path), "data": data_b64},
        })
    elif kind == "text":
        attachment_blocks.append({
            "type": "text",
            "text": f"[Attachment: {path.name}]\n{path.read_text(errors='replace')[:50_000]}",
        })

# Evict oldest until we fit budget (approximate token count via
# len(content) // 4; rough but cheap)
def estimate_tokens(blocks: list[dict]) -> int:
    total = 0
    for b in blocks:
        if b.get("type") == "text":
            total += len(b.get("text", "")) // 4
        else:
            # base64 expands ~4/3, plus the model needs page-render budget;
            # use a conservative 1k tokens / 100KB of source data.
            data = b.get("source", {}).get("data", "")
            total += len(data) * 3 // 4 // 100  # bytes / 100 ≈ tokens
    return total

evicted: list[str] = []
budget = int(self._budget_for_current_model() * 0.85)
while attachment_blocks and estimate_tokens(attachment_blocks) > budget:
    # Find oldest non-force-included
    for i, b in enumerate(list(attachment_blocks)):
        # We need to know which file this block is from; track via parallel list
        path = attachments[i] if i < len(attachments) else None
        if path and path.name in force_include:
            continue
        evicted.append(path.name if path else "?")
        attachment_blocks.pop(i)
        break
    else:
        break  # all force-included; can't evict further

# Build the final user message
user_text_parts: list[str] = []
for name in evicted:
    user_text_parts.append(
        f"[Attachment '{name}' parked — call attachment_load('{name}') "
        f"to bring it back into context.]"
    )
user_text_parts.append(time_prefix + message)

if attachment_blocks:
    final_user_content = attachment_blocks + [
        {"type": "text", "text": "\n\n".join(user_text_parts)}
    ]
    conversation.append({"role": "user", "content": final_user_content})
else:
    conversation.append({"role": "user", "content": "\n\n".join(user_text_parts)})
```

The marker-detection / system-prompt-injection blocks are removed entirely.

`_session_force_include: dict[str, set[str]]` is added as a ChatService instance field, populated by the `attachment_load` tool.

`_budget_for_current_model()` returns the model context window (Sonnet ≈ 200k, Opus ≈ 200k, etc.) — already exists or trivially derivable from the model config.

### `src/mycelos/tools/attachments.py` (new)

```python
"""Tools for attachment management — save to KB, load from session storage."""

from __future__ import annotations

import logging
from typing import Any

from mycelos.tools.registry import ToolPermission

logger = logging.getLogger("mycelos.tools.attachments")


NOTE_SAVE_ATTACHMENT_SCHEMA = {
    "type": "function",
    "function": {
        "name": "note_save_attachment",
        "description": (
            "Promote a session attachment (file the user uploaded in this "
            "conversation) to the permanent Knowledge Base. The file plus "
            "your summary become a Knowledge note the user can find later. "
            "Use when the user asks to remember / save / persist the file."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "filename": {"type": "string", "description": "Attachment filename as it appears in the session."},
                "summary": {"type": "string", "description": "Short summary you generated for the document."},
                "tags": {"type": "array", "items": {"type": "string"}, "description": "Optional tags."},
            },
            "required": ["filename", "summary"],
        },
    },
}


ATTACHMENT_LOAD_SCHEMA = {
    "type": "function",
    "function": {
        "name": "attachment_load",
        "description": (
            "Force a session attachment that was evicted from the LLM "
            "context (due to token-budget pressure) back into the active "
            "context for the next turn. Use when you need to re-read an "
            "attachment that's no longer visible."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "filename": {"type": "string", "description": "Attachment filename to reload."},
            },
            "required": ["filename"],
        },
    },
}


def execute_note_save_attachment(args: dict, context: dict) -> Any:
    from mycelos.files.session_attachments import SessionAttachmentStore
    app = context["app"]
    session_id = context.get("session_id", "")
    filename = args.get("filename", "")
    summary = args.get("summary", "")
    tags = args.get("tags", []) or []

    store = SessionAttachmentStore(app.data_dir / "sessions")
    try:
        data = store.read(session_id, filename)
    except FileNotFoundError:
        return {"status": "error", "message": f"Attachment {filename!r} not found in this session"}

    note_path = app.knowledge_base.store_document(
        file_bytes=data,
        filename=filename,
        title=filename.rsplit(".", 1)[0].replace("-", " ").replace("_", " ").title(),
        summary=summary,
        tags=tags,
    )
    app.audit.log(
        "knowledge.attachment_saved",
        details={"path": note_path, "filename": filename},
    )
    return {"status": "saved", "path": note_path}


def execute_attachment_load(args: dict, context: dict) -> Any:
    service = context.get("chat_service")
    session_id = context.get("session_id", "")
    filename = args.get("filename", "")
    if service is None or not session_id or not filename:
        return {"status": "error", "message": "missing context"}
    service.mark_force_include(session_id, filename)
    return {"status": "loaded", "filename": filename}


def register(registry: type) -> None:
    registry.register(
        "note_save_attachment",
        NOTE_SAVE_ATTACHMENT_SCHEMA,
        execute_note_save_attachment,
        ToolPermission.STANDARD,
        category="core",
    )
    registry.register(
        "attachment_load",
        ATTACHMENT_LOAD_SCHEMA,
        execute_attachment_load,
        ToolPermission.STANDARD,
        category="core",
    )
```

`ChatService.mark_force_include(session_id, filename)` is a new instance method that adds to `self._session_force_include`.

### `src/mycelos/channels/telegram.py`

`handle_document` and `handle_photo` are simplified:

```python
@dp.message(F.document)
async def handle_document(message: types.Message) -> None:
    doc = message.document
    file = await _bot.get_file(doc.file_id)
    file_bytes = await _download_telegram_file(file)

    session_id = _ensure_session_for_user(message.from_user.id)

    from mycelos.files.session_attachments import SessionAttachmentStore
    store = SessionAttachmentStore(_app.data_dir / "sessions")
    saved = store.save(session_id, file_bytes, doc.file_name or f"doc-{doc.file_unique_id}")

    if message.caption:
        # Caption is the user's question — process it normally;
        # ChatService picks up the new attachment via store.list().
        await _process_user_message(message, message.caption, session_id)
    else:
        # No caption — wait for the next text turn.
        _app.session_store.set_meta(session_id, "pending_attachment", saved.name)
        await _safe_answer(message, "Was möchtest du wissen?")


@dp.message(F.photo)
async def handle_photo(message: types.Message) -> None:
    photo = message.photo[-1]
    file = await _bot.get_file(photo.file_id)
    data = await _download_telegram_file(file)

    session_id = _ensure_session_for_user(message.from_user.id)

    from mycelos.files.session_attachments import SessionAttachmentStore
    store = SessionAttachmentStore(_app.data_dir / "sessions")
    saved = store.save(session_id, data, f"photo-{photo.file_unique_id}.jpg")

    if message.caption:
        await _process_user_message(message, message.caption, session_id)
    else:
        _app.session_store.set_meta(session_id, "pending_attachment", saved.name)
        await _safe_answer(message, "Was möchtest du wissen?")
```

Existing text-message handler clears `pending_attachment` after first read.

### `src/mycelos/prompts/mycelos.md`

The "## File uploads" section is replaced by:

```markdown
## File uploads

The chat UI lets the user upload files (PDF, DOCX, images, plain text). When a user uploads, the file is automatically attached to every subsequent LLM call in this session — you have direct access to it via Anthropic's Multi-Part content. Read it like you'd read any other text or image; you don't need to call a tool to "open" the file.

If the user wants to keep the file longer than the session: call `note_save_attachment(filename, summary, tags)`. The file is copied to the Knowledge Base permanently, with the summary you generated. Use this when the user says "merk dir das" / "speicher das" / "save this for later".

If a previous attachment got evicted due to token budget pressure (you'll see a "[Attachment '...' parked]" stub instead of the file), call `attachment_load(filename)` to bring it back for the next turn.
```

### Frontend

`chat.html` `handleFileUpload` already builds preview cards from the `file-attached` event. The only change is the URL: backend already returns `/api/sessions/<id>/attachments/<filename>` in the event payload — no frontend code change needed.

### Database / state

Per-session `pending_attachment` lives in the existing session metadata (key/value store on `session_meta`). No schema changes.

`_session_force_include` is in-memory only; it doesn't need to survive restart (rare race: user calls `attachment_load`, server restarts before next turn, attachment is evicted again — agent re-calls. Acceptable.)

## Data Flow

### Upload + first question (Web)

```
User clicks Attach + selects foo.pdf
  ↓
POST /api/upload (file=foo.pdf, session_id=abc)
  ↓
Backend:
  - validate size (32MB cap for PDFs)
  - SessionAttachmentStore.save("abc", bytes, "foo.pdf") → sessions/abc/attachments/foo.pdf
  - SSE: session(abc), file-attached(url=/api/sessions/abc/attachments/foo.pdf, kind=pdf), done
  ↓
Frontend renders preview card with PDF icon
User types "Was steht da drin?" + Enter
  ↓
POST /api/chat
  ↓
ChatService.handle_message:
  - hydrate conversation from session_store
  - SessionAttachmentStore.list("abc") → [foo.pdf]
  - estimate_tokens([Document(foo.pdf)] + history) → 4500 tokens (well under 170k budget)
  - Build user content:
      [{type: document, source: base64(foo.pdf)}, {type: text, text: "[time]\nWas steht da drin?"}]
  - LLM call
  ↓
Sonnet reads PDF natively, replies with content summary
```

### "Speicher das in Knowledge"

```
User: "Das war wichtig — speicher das bitte"
  ↓
LLM emits tool_use: note_save_attachment(filename="foo.pdf", summary="...", tags=["finance"])
  ↓
execute_note_save_attachment:
  - SessionAttachmentStore.read("abc", "foo.pdf") → bytes
  - kb.store_document(bytes, "foo.pdf", "Foo (Q1 Report)", summary, tags) → notes/2026-04-29-foo
  - audit knowledge.attachment_saved
  ↓
Tool returns {status: "saved", path: "notes/2026-04-29-foo"}
Agent answers: "Hab's unter Finance abgelegt — du findest es jederzeit unter notes/2026-04-29-foo"
```

### Token-budget eviction

```
Session has [foo.pdf (4k tokens), bar.pdf (8k), baz.pdf (180k)]
User asks something, chat history is 40k tokens
  ↓
estimate_tokens(attachments + history) = 232k > 0.85 * 200k = 170k
Evict oldest non-force-included:
  - foo.pdf (oldest) → text stub: "[Attachment 'foo.pdf' parked — call attachment_load('foo.pdf') to bring it back]"
  - re-estimate: 228k > 170k still
  - bar.pdf → stub
  - re-estimate: 220k > 170k still
  - baz.pdf is force_included? no → stub
  - re-estimate: 40k history + stubs (~200 tokens) → fits
  ↓
Hard fail if STILL over budget after evicting all → "Conversation too large"
```

### Telegram attachment with no caption

```
User sends photo.jpg via Telegram (no caption)
  ↓
@F.photo handler:
  - download bytes
  - SessionAttachmentStore.save(tg_session_id, bytes, "photo-xyz.jpg")
  - session_store.set_meta(tg_session_id, "pending_attachment", "photo-xyz.jpg")
  - _safe_answer: "Was möchtest du wissen?"
  ↓
User sends "Was ist auf dem Bild?"
  ↓
text-message handler:
  - clear pending_attachment
  - normal handle_message — Multi-Part-build picks up photo via store.list()
  ↓
Sonnet sees image, answers about contents
```

### Session deletion

```
User clicks "Delete session" or DELETE /api/sessions/<id>
  ↓
session_store.delete_session(id)
SessionAttachmentStore.delete_session(id)
  → shutil.rmtree("~/.mycelos/sessions/<id>/")
  ↓
Folder + all attachments gone in one transaction.
```

## Error Handling

- **Oversized file**: 4xx error event in SSE, no save, no marker. Frontend shows error toast.
- **Unsupported file type**: same as above.
- **Anthropic rejects file** (corrupt PDF, encrypted): LLM call surfaces a 400 — ChatService renders an error event. File stays in session folder; user can re-upload or ignore.
- **Token budget reached even after evicting all attachments**: Hard error. "Conversation too large. Start a new session or delete old messages."
- **`note_save_attachment` for missing file**: Tool returns `{status: "error", message: "Attachment X not found"}`.
- **Path traversal attempt** in `/api/sessions/<id>/attachments/<filename>`: 400.
- **Session deleted but folder lingers** (race): defensive cleanup on next boot — iterate `~/.mycelos/sessions/` and remove orphan folders that have no DB entry.

## Testing

**Unit:**

- `tests/test_session_attachments.py`: `save` / `list` / `read` / `delete_session`, path traversal, collision handling, ordering by mtime
- `tests/test_attachment_tools.py`: `note_save_attachment` happy path + missing file, `attachment_load` semantics
- `tests/test_chat_multipart.py`: ChatService produces correct Multi-Part content, eviction order is oldest-first, force_include skips eviction

**Integration:**

- `tests/test_upload_flow.py`: `POST /api/upload` saves to session folder, no marker in session history, no auto-ingest in KB
- Update `tests/security/test_constitution_rule_2.py`: upload no longer creates a config generation (was via marker-store), so the upload endpoint is removed from / not present in the rule-2 test set

**Manual (Web + Telegram):**

- PDF upload → first question references content directly
- "Save to Knowledge" → KB has note + original file
- Multiple PDFs in one session → eviction kicks oldest cleanly
- Telegram: PDF with caption (immediate answer), PDF without caption (pending state, then text)

## Success Criteria

1. File upload saves to `~/.mycelos/sessions/<id>/attachments/<filename>`.
2. File is automatically embedded as Multi-Part content in every LLM call for that session.
3. `note_save_attachment` tool copies file + summary into Knowledge Base; original stays in session.
4. `attachment_load` tool forces an evicted attachment back into the next turn.
5. Session deletion removes the attachments folder.
6. Telegram channel uses the same flow with pending-attachment state for caption-less uploads.
7. Auto-ingest logic in `/api/upload` and `telegram.py` is removed.
8. Marker-system code removed: backend persistence (the `[System: …]` `append_message` calls), marker-detection in `chat/service.py`, prompt section in `mycelos.md` rewritten.
9. CHANGELOG entry under the current week.

## Non-Goals

- Audio / video file types — Anthropic doesn't accept them as Multi-Part content. Out of scope.
- Cross-session attachment sharing.
- Attachment versioning (user-edits an uploaded file).
- Embedding-based search over attachments — Knowledge Base does that already, only when explicitly persisted.
- CLI channel — no file-upload UI in the CLI today, out of scope.
- A "list all my attachments" UI panel — agent handles that via tool calls if asked.
- Auto-cleanup of orphan session folders without a DB entry — mentioned defensively, but a separate maintenance script.
