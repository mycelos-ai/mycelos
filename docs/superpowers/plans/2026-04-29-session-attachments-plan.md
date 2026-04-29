# Session Attachments Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the marker-based document-upload pipeline with native Anthropic Multi-Part user messages. Files live per-session, ride along in every LLM call, get persisted to Knowledge Base only when the agent calls `note_save_attachment`.

**Architecture:** New `SessionAttachmentStore` per-session folder. `/api/upload` saves and emits a preview event — no marker, no auto-ingest. ChatService builds Multi-Part user content for every turn from the session's attachment folder, with token-budget eviction. Two new tools (`note_save_attachment`, `attachment_load`). Telegram channel uses the same store via in-memory `pending_attachment` state. All marker plumbing is removed.

**Tech Stack:** Python 3.12+, FastAPI, Starlette SSE, pytest, Anthropic via litellm.

**Spec:** `docs/superpowers/specs/2026-04-29-session-attachments-design.md`

**Baseline rule:** After every task, `PYTHONPATH=src pytest tests/ --ignore=tests/e2e --ignore=tests/integration -q` must pass with zero failures (modulo the known Hypothesis flake on `test_policy_engine.py::test_invalid_decision_rejected_property` — re-run that one file alone to confirm).

**Verified codebase facts (so the plan uses real names):**

- `SessionStore` lives at `src/mycelos/sessions/store.py`. API: `append_message`, `load_messages`, `update_session(session_id, title=None, topic=None)`, `get_session_meta`, `session_exists`. **No** `set_meta` — pending_attachment will be tracked in-memory in the Telegram channel module.
- `App.data_dir` is the canonical data root (e.g. `~/.mycelos`). `App.session_store = SessionStore(data_dir / "conversations")`.
- `App.knowledge_base.store_document(file_bytes, filename, title, summary, tags) -> note_path` already exists (used by `ingest_pdf`).
- `ChatService` lives at `src/mycelos/chat/service.py`. The conversation cache is `self._conversations: dict[str, list[dict]]`.
- The marker-detection logic to remove is around `chat/service.py` lines 482-509 (the "scan for `[System:` markers, append to system prompt" block from the previous fix).
- `/api/upload` lives at `routes.py:1597`. Today it ingests PDFs, writes markers to session_store, branches on vision_needed. All of that is replaced by a tiny save+emit handler.
- Telegram handlers live at `channels/telegram.py:297` (`handle_document`) and `:398` (`handle_photo`). They call `InboxManager` + `ingest_pdf` + extract text. All replaced.
- Tool registry uses `ToolPermission.STANDARD` enum + `category="core"` for always-loaded tools.
- `chat/events.py` has `file_attached_event(filename, url, kind, size)` already.
- The chat-page renderer in `frontend/pages/chat.html` already renders `file-attached` events into preview cards with `attachment` data on the user bubble.
- `_resolve_user_id(request)` returns the user id from headers/state.
- Hard-coded `context_window=200_000` for chat agent (see `chat/service.py:118`). Use that as the budget.

---

## File Structure

Files this plan touches:

- `src/mycelos/files/session_attachments.py` — NEW. `SessionAttachmentStore` + helpers `media_type`, `content_kind`, `SIZE_CAPS_BYTES`.
- `src/mycelos/tools/attachments.py` — NEW. `note_save_attachment` + `attachment_load` tools.
- `src/mycelos/tools/registry.py` — wire the new tool module into `_ensure_initialized()`.
- `src/mycelos/gateway/routes.py`:
  - Replace `POST /api/upload` body with the lean save+emit version.
  - Add `GET /api/sessions/{session_id}/attachments/{filename:path}` endpoint.
  - Remove `GET /api/inbox/{filename:path}` endpoint (replaced).
- `src/mycelos/chat/service.py`:
  - Add `_session_force_include: dict[str, set[str]]` instance field + `mark_force_include` method.
  - In `handle_message`, after building the conversation, build Multi-Part user content from `SessionAttachmentStore.list(session_id)`. Token-budget eviction loop. The user-text turn is appended on top of the attachment blocks.
  - **Delete** the marker-extraction block (the "scan user history for `[System:` markers, append to system prompt" code from the previous fix). Hydration from disk stays.
  - In session-delete path: also call `SessionAttachmentStore.delete_session(session_id)`.
- `src/mycelos/channels/telegram.py`:
  - Replace `handle_document` and `handle_photo` with the new flow (save to session store, then either process caption or set pending_attachment).
  - Add module-level `_pending_attachments: dict[str, str]` (session_id → filename).
  - Hook into the text-message handler so it clears pending_attachment after the next turn.
- `src/mycelos/prompts/mycelos.md` — replace the "## File uploads" section.
- `src/mycelos/chat/events.py` — `file_attached_event` already exists; no change needed.
- `src/mycelos/frontend/pages/chat.html` — no change needed; the `file-attached` event already carries `url`, the backend just emits a different URL now.
- `tests/test_session_attachments.py` — NEW.
- `tests/test_attachment_tools.py` — NEW.
- `tests/test_chat_multipart.py` — NEW.
- `tests/test_upload_flow.py` — NEW (replaces parts of any existing upload-flow test).
- `tests/security/test_constitution_rule_2.py` — drop or update tests that asserted upload created a marker / generation.
- `CHANGELOG.md` — Week 18 entry.

What stays unchanged:
- `mycelos/files/inbox.py` — still used by other code paths; out of scope to remove.
- `mycelos/knowledge/ingest.py` — `store_document` is what `note_save_attachment` reuses; `ingest_pdf` itself becomes orphaned for the upload flow but stays callable.

---

## Task 1: `SessionAttachmentStore`

**Files:**
- Create: `src/mycelos/files/session_attachments.py`
- Test: `tests/test_session_attachments.py`

This is the foundation everything else builds on. Pure file-IO + helpers; no DB, no other Mycelos services.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_session_attachments.py`:

```python
"""Tests for SessionAttachmentStore — per-session file storage."""

from __future__ import annotations

from pathlib import Path

import pytest

from mycelos.files.session_attachments import (
    SessionAttachmentStore,
    SIZE_CAPS_BYTES,
    content_kind,
    media_type,
)


def test_save_and_read(tmp_path: Path) -> None:
    store = SessionAttachmentStore(tmp_path)
    saved = store.save("sess-1", b"hello world", "greeting.txt")
    assert saved.exists()
    assert saved.read_bytes() == b"hello world"
    assert store.read("sess-1", "greeting.txt") == b"hello world"


def test_save_creates_per_session_folder(tmp_path: Path) -> None:
    store = SessionAttachmentStore(tmp_path)
    store.save("sess-A", b"a", "x.txt")
    store.save("sess-B", b"b", "x.txt")
    assert (tmp_path / "sess-A" / "attachments" / "x.txt").exists()
    assert (tmp_path / "sess-B" / "attachments" / "x.txt").exists()


def test_filename_collision_gets_suffix(tmp_path: Path) -> None:
    store = SessionAttachmentStore(tmp_path)
    a = store.save("s", b"first", "report.pdf")
    b = store.save("s", b"second", "report.pdf")
    assert a.name == "report.pdf"
    assert b.name == "report-2.pdf"
    assert a.read_bytes() == b"first"
    assert b.read_bytes() == b"second"


def test_path_traversal_blocked(tmp_path: Path) -> None:
    store = SessionAttachmentStore(tmp_path)
    # The sanitize_filename helper strips path separators, so this
    # ends up as a flat name inside the session folder.
    saved = store.save("s", b"x", "../../etc/passwd")
    assert saved.parent == tmp_path / "s" / "attachments"
    assert ".." not in saved.name


def test_empty_data_rejected(tmp_path: Path) -> None:
    store = SessionAttachmentStore(tmp_path)
    with pytest.raises(ValueError):
        store.save("s", b"", "empty.txt")


def test_list_returns_attachments_oldest_first(tmp_path: Path) -> None:
    import time
    store = SessionAttachmentStore(tmp_path)
    store.save("s", b"first", "a.txt")
    time.sleep(0.01)
    store.save("s", b"second", "b.txt")
    items = store.list("s")
    assert [p.name for p in items] == ["a.txt", "b.txt"]


def test_list_empty_for_unknown_session(tmp_path: Path) -> None:
    store = SessionAttachmentStore(tmp_path)
    assert store.list("never-saved") == []


def test_read_missing_raises(tmp_path: Path) -> None:
    store = SessionAttachmentStore(tmp_path)
    with pytest.raises(FileNotFoundError):
        store.read("s", "nope.pdf")


def test_delete_session_removes_folder(tmp_path: Path) -> None:
    store = SessionAttachmentStore(tmp_path)
    store.save("doomed", b"x", "f.txt")
    assert (tmp_path / "doomed").exists()
    store.delete_session("doomed")
    assert not (tmp_path / "doomed").exists()


def test_delete_session_idempotent(tmp_path: Path) -> None:
    store = SessionAttachmentStore(tmp_path)
    # No raise even when nothing was ever saved
    store.delete_session("never-existed")


def test_content_kind_classifies() -> None:
    assert content_kind(Path("x.pdf")) == "document"
    assert content_kind(Path("x.PNG")) == "image"
    assert content_kind(Path("x.jpg")) == "image"
    assert content_kind(Path("x.webp")) == "image"
    assert content_kind(Path("x.txt")) == "text"
    assert content_kind(Path("x.md")) == "text"
    assert content_kind(Path("x.csv")) == "text"
    assert content_kind(Path("x.exe")) == "unsupported"


def test_media_type() -> None:
    assert media_type(Path("x.pdf")) == "application/pdf"
    assert media_type(Path("x.png")) == "image/png"
    assert media_type(Path("x.jpg")) == "image/jpeg"
    assert media_type(Path("x.txt")) == "text/plain"


def test_size_caps_present() -> None:
    assert SIZE_CAPS_BYTES["pdf"] == 32 * 1024 * 1024
    assert SIZE_CAPS_BYTES["image"] == 5 * 1024 * 1024
    assert SIZE_CAPS_BYTES["text"] == 10 * 1024 * 1024
```

- [ ] **Step 2: Run, confirm fails**

```
PYTHONPATH=src pytest tests/test_session_attachments.py -v
```

Expected: ImportError — module doesn't exist.

- [ ] **Step 3: Create the module**

Create `src/mycelos/files/session_attachments.py`:

```python
"""Per-session attachment storage. Files live as long as the session does.

Each session gets its own folder under sessions/<id>/attachments/.
Folders are created on first save and removed wholesale when the
session is deleted. Path-traversal-safe via sanitize_filename.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from mycelos.files.inbox import sanitize_filename


# Per-type size caps — match Anthropic's Multi-Part content limits.
SIZE_CAPS_BYTES: dict[str, int] = {
    "pdf": 32 * 1024 * 1024,
    "image": 5 * 1024 * 1024,
    "text": 10 * 1024 * 1024,
}


class SessionAttachmentStore:
    """File store scoped to a single session.

    base_dir is the parent directory (e.g. ~/.mycelos/sessions/);
    each session gets a subfolder created on first save.
    """

    def __init__(self, base_dir: Path) -> None:
        self._base_dir = base_dir

    def _session_dir(self, session_id: str) -> Path:
        return self._base_dir / session_id / "attachments"

    def save(self, session_id: str, data: bytes, filename: str) -> Path:
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
        # Collision handling — foo.pdf, foo-2.pdf, foo-3.pdf, ...
        if target.exists():
            stem, suffix = target.stem, target.suffix
            counter = 2
            while target.exists():
                target = target_dir / f"{stem}-{counter}{suffix}"
                counter += 1
        target.write_bytes(data)
        return target

    def list(self, session_id: str) -> list[Path]:
        """All attachments for the session, oldest first (by mtime)."""
        d = self._session_dir(session_id)
        if not d.exists():
            return []
        return sorted(
            (p for p in d.iterdir() if p.is_file()),
            key=lambda p: p.stat().st_mtime,
        )

    def read(self, session_id: str, filename: str) -> bytes:
        target = self._session_dir(session_id) / sanitize_filename(filename)
        return target.read_bytes()

    def delete_session(self, session_id: str) -> None:
        # Remove the entire sessions/<id>/ folder (parent of attachments/).
        # Idempotent — no error if it doesn't exist.
        d = self._session_dir(session_id).parent
        if d.exists():
            shutil.rmtree(d, ignore_errors=True)


def media_type(path: Path) -> str:
    """Map file suffix to a MIME type for Anthropic Multi-Part content."""
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
    """Return 'document', 'image', 'text', or 'unsupported'.

    Only types Anthropic accepts as Multi-Part content (or that we
    inline as plain text) are supported. Anything else is rejected
    at the upload boundary.
    """
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return "document"
    if suffix in (".jpg", ".jpeg", ".png", ".webp", ".gif"):
        return "image"
    if suffix in (".txt", ".md", ".csv", ".json", ".yaml", ".yml"):
        return "text"
    return "unsupported"
```

- [ ] **Step 4: Run tests, confirm pass**

```
PYTHONPATH=src pytest tests/test_session_attachments.py -v
```

Expected: 13 passing.

- [ ] **Step 5: Run baseline**

```
PYTHONPATH=src pytest tests/ --ignore=tests/e2e --ignore=tests/integration -q
```

Expected: zero failures.

- [ ] **Step 6: Commit**

```bash
git add src/mycelos/files/session_attachments.py tests/test_session_attachments.py
git commit -m "feat(files): SessionAttachmentStore for per-session file storage"
```

**Rules (CLAUDE.md):**
- No `Co-Authored-By` / Claude footer
- English commit message
- Do NOT push (Task 9 pushes everything together)
- Do NOT touch other files
- Do NOT modify `inbox.py`'s `sanitize_filename` — reuse it as-is

---

## Task 2: New attachment tools — `note_save_attachment` + `attachment_load`

**Files:**
- Create: `src/mycelos/tools/attachments.py`
- Modify: `src/mycelos/tools/registry.py` (`_ensure_initialized`)
- Modify: `src/mycelos/chat/service.py` (add `mark_force_include` method + `_session_force_include` field)
- Test: `tests/test_attachment_tools.py`

The two tools the agent uses to manage attachments. `note_save_attachment` is one-shot agent action ("save this to KB"). `attachment_load` is a session-state tweak that the chat-build picks up next turn.

- [ ] **Step 1: Add `_session_force_include` and `mark_force_include` to ChatService**

Open `src/mycelos/chat/service.py`. Find `class ChatService:`. Find its `__init__`. Add a new instance field after the existing `self._conversations` initialization:

```python
        # Per-session set of attachment filenames the agent has explicitly
        # asked to keep in the next turn's context, even if budget pressure
        # would normally evict them. Populated by attachment_load tool;
        # consumed (and cleared) by the next handle_message call.
        self._session_force_include: dict[str, set[str]] = {}
```

Add a method on `ChatService` (place near the existing `resume_session` method for readability):

```python
    def mark_force_include(self, session_id: str, filename: str) -> None:
        """Force a specific attachment to stay in the LLM context next turn.

        Called by the attachment_load tool. The flag is consumed (popped)
        on the next handle_message call.
        """
        self._session_force_include.setdefault(session_id, set()).add(filename)
```

- [ ] **Step 2: Write the failing tests**

Create `tests/test_attachment_tools.py`:

```python
"""Tests for note_save_attachment + attachment_load tools."""

from __future__ import annotations

import os
from pathlib import Path

import pytest


def test_note_save_attachment_persists_to_kb(tmp_data_dir: Path) -> None:
    from mycelos.app import App
    from mycelos.files.session_attachments import SessionAttachmentStore
    from mycelos.tools.attachments import execute_note_save_attachment

    os.environ["MYCELOS_MASTER_KEY"] = "attach-tools-test-1"
    app = App(tmp_data_dir)
    app.initialize()

    store = SessionAttachmentStore(app.data_dir / "sessions")
    saved = store.save("test-session", b"hello pdf", "report.pdf")

    result = execute_note_save_attachment(
        {"filename": "report.pdf", "summary": "A test report.", "tags": ["x"]},
        context={"app": app, "session_id": "test-session"},
    )
    assert result["status"] == "saved"
    assert result["path"].startswith("notes/")

    # Note exists in KB
    note = app.knowledge_base.get(result["path"])
    assert note is not None


def test_note_save_attachment_missing_file(tmp_data_dir: Path) -> None:
    from mycelos.app import App
    from mycelos.tools.attachments import execute_note_save_attachment

    os.environ["MYCELOS_MASTER_KEY"] = "attach-tools-test-2"
    app = App(tmp_data_dir)
    app.initialize()

    result = execute_note_save_attachment(
        {"filename": "nope.pdf", "summary": "x", "tags": []},
        context={"app": app, "session_id": "no-such-session"},
    )
    assert result["status"] == "error"
    assert "not found" in result["message"].lower()


def test_attachment_load_sets_force_include_flag() -> None:
    from mycelos.tools.attachments import execute_attachment_load

    class FakeService:
        def __init__(self) -> None:
            self.calls: list[tuple[str, str]] = []

        def mark_force_include(self, session_id: str, filename: str) -> None:
            self.calls.append((session_id, filename))

    svc = FakeService()
    result = execute_attachment_load(
        {"filename": "foo.pdf"},
        context={"chat_service": svc, "session_id": "s1"},
    )
    assert result["status"] == "loaded"
    assert result["filename"] == "foo.pdf"
    assert svc.calls == [("s1", "foo.pdf")]


def test_attachment_load_missing_context() -> None:
    from mycelos.tools.attachments import execute_attachment_load

    result = execute_attachment_load(
        {"filename": "x"},
        context={},  # no chat_service / session_id
    )
    assert result["status"] == "error"
```

- [ ] **Step 3: Run, confirm fails**

```
PYTHONPATH=src pytest tests/test_attachment_tools.py -v
```

Expected: ImportError.

- [ ] **Step 4: Create the tools module**

Create `src/mycelos/tools/attachments.py`:

```python
"""Agent tools for managing session attachments.

note_save_attachment — promote a file from session storage to the
   permanent Knowledge Base, with the agent's summary attached.
attachment_load — force an evicted attachment back into LLM context.
"""

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
            "Promote a session attachment (a file the user uploaded "
            "during this conversation) to the permanent Knowledge Base. "
            "The original file plus your summary become a Knowledge note "
            "the user can find later. Use when the user asks to remember, "
            "save, or persist the file."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "filename": {
                    "type": "string",
                    "description": "Attachment filename as it appears in the conversation.",
                },
                "summary": {
                    "type": "string",
                    "description": "Concise summary of the file content (you generate this).",
                },
                "tags": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional tags. Empty list is fine.",
                },
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
            "Force a session attachment that was previously evicted from "
            "the LLM context (due to token-budget pressure) back into the "
            "active context for the next turn. Use when you need to re-read "
            "a file that's no longer visible to you (you'll see a "
            "'[Attachment ... parked]' stub instead of the file content)."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "filename": {
                    "type": "string",
                    "description": "Attachment filename to reload.",
                },
            },
            "required": ["filename"],
        },
    },
}


def execute_note_save_attachment(args: dict, context: dict) -> Any:
    from mycelos.files.session_attachments import SessionAttachmentStore

    app = context.get("app")
    session_id = context.get("session_id", "")
    filename = (args.get("filename") or "").strip()
    summary = args.get("summary", "")
    tags = args.get("tags") or []

    if not app or not session_id or not filename:
        return {"status": "error", "message": "missing context"}

    store = SessionAttachmentStore(app.data_dir / "sessions")
    try:
        data = store.read(session_id, filename)
    except FileNotFoundError:
        return {
            "status": "error",
            "message": f"Attachment {filename!r} not found in this session",
        }

    title = filename.rsplit(".", 1)[0].replace("-", " ").replace("_", " ").title()
    try:
        note_path = app.knowledge_base.store_document(
            file_bytes=data,
            filename=filename,
            title=title,
            summary=summary,
            tags=list(tags),
        )
    except Exception as e:
        logger.exception("note_save_attachment failed for %s", filename)
        return {"status": "error", "message": str(e)}

    try:
        app.audit.log(
            "knowledge.attachment_saved",
            details={"path": note_path, "filename": filename},
        )
    except Exception:
        pass

    return {"status": "saved", "path": note_path}


def execute_attachment_load(args: dict, context: dict) -> Any:
    service = context.get("chat_service")
    session_id = context.get("session_id", "")
    filename = (args.get("filename") or "").strip()
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

- [ ] **Step 5: Wire into the registry**

Open `src/mycelos/tools/registry.py`. Find `_ensure_initialized` (around line 205) — it's the method that imports each tool module and calls `register(cls)`. Add (alphabetical placement near other tool imports):

```python
        from mycelos.tools import attachments as _attach_tools
        _attach_tools.register(cls)
```

- [ ] **Step 6: Run new tests**

```
PYTHONPATH=src pytest tests/test_attachment_tools.py -v
```

Expected: 4 passing.

- [ ] **Step 7: Smoke that the tools are registered**

```
PYTHONPATH=src python3 -c "
from mycelos.tools.registry import ToolRegistry
ToolRegistry._ensure_initialized()
print('save:', ToolRegistry.get_schema('note_save_attachment') is not None)
print('load:', ToolRegistry.get_schema('attachment_load') is not None)
"
```

Expected: `save: True`, `load: True`.

- [ ] **Step 8: Run baseline**

```
PYTHONPATH=src pytest tests/ --ignore=tests/e2e --ignore=tests/integration -q
```

Expected: zero failures.

- [ ] **Step 9: Commit**

```bash
git add src/mycelos/tools/attachments.py src/mycelos/tools/registry.py src/mycelos/chat/service.py tests/test_attachment_tools.py
git commit -m "feat(tools): note_save_attachment + attachment_load for session files"
```

---

## Task 3: ChatService Multi-Part build + token-budget eviction

**Files:**
- Modify: `src/mycelos/chat/service.py`
- Test: `tests/test_chat_multipart.py`

The heart of the spec. Every LLM call now ships the session's attachments as Multi-Part content. The marker-detection logic is removed.

- [ ] **Step 1: Read the current handle_message — locate the marker-extraction block**

Open `src/mycelos/chat/service.py`. Find the block (around lines 482-509) that scans user messages for `[System:` markers and appends them to the system prompt. It looks like:

```python
        if not conversation or conversation[0].get("role") != "system":
            user_name = self._app.memory.get("default", "system", "user.name")
            base_prompt = self.get_system_prompt(user_name, channel=channel)
            markers = [
                m["content"]
                for m in conversation
                if m.get("role") == "user"
                and m.get("content", "").lstrip().startswith("[System:")
            ]
            if markers:
                base_prompt = (
                    base_prompt
                    + "\n\n## Active session markers\n"
                    + "\n".join(markers)
                )
            conversation.insert(0, {
                "role": "system",
                "content": base_prompt,
            })
```

This whole block gets simplified — markers are gone now. Replace with:

```python
        if not conversation or conversation[0].get("role") != "system":
            user_name = self._app.memory.get("default", "system", "user.name")
            conversation.insert(0, {
                "role": "system",
                "content": self.get_system_prompt(user_name, channel=channel),
            })
```

- [ ] **Step 2: Write the failing tests**

Create `tests/test_chat_multipart.py`:

```python
"""Tests for the ChatService Multi-Part attachment build.

We exercise the attachment-stitching logic in isolation by calling a
helper method on the service, so we don't have to spin up a real LLM.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest


def _service(tmp_data_dir: Path):
    """Real ChatService against a real App with a tmp data dir."""
    from mycelos.app import App
    os.environ["MYCELOS_MASTER_KEY"] = "multipart-test-key"
    app = App(tmp_data_dir)
    app.initialize()
    return app.chat_service, app


def test_no_attachments_returns_text_user_message(tmp_data_dir: Path) -> None:
    service, app = _service(tmp_data_dir)
    blocks, evicted = service._build_attachment_blocks(
        session_id="s1", budget_tokens=200_000,
    )
    assert blocks == []
    assert evicted == []


def test_pdf_attachment_becomes_document_block(tmp_data_dir: Path) -> None:
    from mycelos.files.session_attachments import SessionAttachmentStore

    service, app = _service(tmp_data_dir)
    store = SessionAttachmentStore(app.data_dir / "sessions")
    store.save("s1", b"%PDF-fake-bytes", "report.pdf")

    blocks, evicted = service._build_attachment_blocks(
        session_id="s1", budget_tokens=200_000,
    )
    assert len(blocks) == 1
    assert blocks[0]["type"] == "document"
    assert blocks[0]["source"]["media_type"] == "application/pdf"
    assert blocks[0]["source"]["type"] == "base64"
    assert evicted == []


def test_image_attachment_becomes_image_block(tmp_data_dir: Path) -> None:
    from mycelos.files.session_attachments import SessionAttachmentStore

    service, app = _service(tmp_data_dir)
    store = SessionAttachmentStore(app.data_dir / "sessions")
    store.save("s1", b"\x89PNG\r\n\x1a\n", "photo.png")

    blocks, evicted = service._build_attachment_blocks(
        session_id="s1", budget_tokens=200_000,
    )
    assert len(blocks) == 1
    assert blocks[0]["type"] == "image"
    assert blocks[0]["source"]["media_type"] == "image/png"


def test_text_attachment_becomes_text_block(tmp_data_dir: Path) -> None:
    from mycelos.files.session_attachments import SessionAttachmentStore

    service, app = _service(tmp_data_dir)
    store = SessionAttachmentStore(app.data_dir / "sessions")
    store.save("s1", b"hello world\nthis is text", "notes.txt")

    blocks, evicted = service._build_attachment_blocks(
        session_id="s1", budget_tokens=200_000,
    )
    assert len(blocks) == 1
    assert blocks[0]["type"] == "text"
    assert "hello world" in blocks[0]["text"]
    assert "[Attachment: notes.txt]" in blocks[0]["text"]


def test_eviction_kicks_oldest_first(tmp_data_dir: Path) -> None:
    import time
    from mycelos.files.session_attachments import SessionAttachmentStore

    service, app = _service(tmp_data_dir)
    store = SessionAttachmentStore(app.data_dir / "sessions")
    # Three large PDFs — saved at distinct mtimes
    store.save("s1", b"x" * 100_000, "old.pdf")
    time.sleep(0.01)
    store.save("s1", b"x" * 100_000, "mid.pdf")
    time.sleep(0.01)
    store.save("s1", b"x" * 100_000, "new.pdf")

    # Tiny budget forces eviction
    blocks, evicted = service._build_attachment_blocks(
        session_id="s1", budget_tokens=500,  # ~50KB worth
    )
    # Oldest goes first, evicted list reflects it
    assert "old.pdf" in evicted
    # The newest survives
    block_filenames_in_order = [e for e in evicted]
    assert evicted.index("old.pdf") < (evicted.index("mid.pdf") if "mid.pdf" in evicted else 99)


def test_force_include_skips_eviction(tmp_data_dir: Path) -> None:
    import time
    from mycelos.files.session_attachments import SessionAttachmentStore

    service, app = _service(tmp_data_dir)
    store = SessionAttachmentStore(app.data_dir / "sessions")
    store.save("s1", b"x" * 100_000, "important.pdf")
    time.sleep(0.01)
    store.save("s1", b"x" * 100_000, "later.pdf")

    # Mark the OLDER one as force-included → eviction must skip it,
    # kick the newer one instead even though it's not oldest.
    service.mark_force_include("s1", "important.pdf")

    blocks, evicted = service._build_attachment_blocks(
        session_id="s1", budget_tokens=500,
    )
    assert "important.pdf" not in evicted
    # Force-include flag is consumed
    assert "s1" not in service._session_force_include or \
        "important.pdf" not in service._session_force_include.get("s1", set())


def test_unsupported_files_are_skipped(tmp_data_dir: Path) -> None:
    from mycelos.files.session_attachments import SessionAttachmentStore

    service, app = _service(tmp_data_dir)
    store = SessionAttachmentStore(app.data_dir / "sessions")
    store.save("s1", b"binary garbage", "weird.bin")

    blocks, evicted = service._build_attachment_blocks(
        session_id="s1", budget_tokens=200_000,
    )
    assert blocks == []
```

- [ ] **Step 3: Run, confirm fails**

```
PYTHONPATH=src pytest tests/test_chat_multipart.py -v
```

Expected: failures because `_build_attachment_blocks` doesn't exist yet.

- [ ] **Step 4: Implement `_build_attachment_blocks` on ChatService**

In `src/mycelos/chat/service.py`, add this method on `ChatService` (place near `mark_force_include`):

```python
    def _build_attachment_blocks(
        self,
        session_id: str,
        budget_tokens: int,
    ) -> tuple[list[dict], list[str]]:
        """Build Multi-Part content blocks for the session's attachments.

        Returns (blocks, evicted_filenames). Each block is an Anthropic
        Multi-Part content dict (document / image / text). Eviction is
        oldest-first, except attachments that the agent flagged via
        attachment_load (force_include) — those stay in even under
        budget pressure.

        Tokens are estimated cheaply: ~4 chars/token for text, and
        ~bytes/100 for base64-encoded binary (PDF/image), since the
        model also runs page-render / decoding work.
        """
        import base64
        from mycelos.files.session_attachments import (
            SessionAttachmentStore, content_kind, media_type,
        )

        store = SessionAttachmentStore(self._app.data_dir / "sessions")
        attachments = store.list(session_id)
        if not attachments:
            return [], []

        force_include = self._session_force_include.pop(session_id, set())

        # Build candidate blocks in upload order.
        candidates: list[tuple[Path_, dict]] = []
        for path in attachments:
            kind = content_kind(path)
            if kind == "unsupported":
                continue
            try:
                raw = path.read_bytes()
            except OSError:
                continue
            if kind == "document":
                candidates.append((path, {
                    "type": "document",
                    "source": {
                        "type": "base64",
                        "media_type": media_type(path),
                        "data": base64.b64encode(raw).decode("ascii"),
                    },
                }))
            elif kind == "image":
                candidates.append((path, {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": media_type(path),
                        "data": base64.b64encode(raw).decode("ascii"),
                    },
                }))
            elif kind == "text":
                try:
                    text = raw.decode("utf-8", errors="replace")
                except Exception:
                    text = ""
                candidates.append((path, {
                    "type": "text",
                    "text": f"[Attachment: {path.name}]\n{text[:50_000]}",
                }))

        def _block_tokens(block: dict) -> int:
            if block.get("type") == "text":
                return len(block.get("text", "")) // 4
            data = block.get("source", {}).get("data", "")
            # base64 → original bytes ≈ data * 3/4; conservative tokens ≈ bytes/100
            return len(data) * 3 // 4 // 100

        # Eviction loop: kick oldest non-force-included until we fit.
        evicted: list[str] = []
        while candidates and sum(_block_tokens(b) for _, b in candidates) > budget_tokens:
            kicked_one = False
            for i, (path, _) in enumerate(candidates):
                if path.name in force_include:
                    continue
                evicted.append(path.name)
                candidates.pop(i)
                kicked_one = True
                break
            if not kicked_one:
                # Everything left is force-included — can't shrink further
                break

        return [b for _, b in candidates], evicted
```

Add the missing import alias at the top of the function — Python doesn't have `Path_`. Use `pathlib.Path`:

```python
from pathlib import Path as Path_  # avoid shadowing — local alias
```

(Or simply use the type as `tuple[Path, dict]` if `Path` is already imported from pathlib at module top. The existing file already imports `Path` — verify with `grep -n "from pathlib import" src/mycelos/chat/service.py` and just use that. The `Path_` alias above is only needed if the module doesn't import Path already. Use `Path` directly if it's available.)

- [ ] **Step 5: Wire `_build_attachment_blocks` into `handle_message`**

Find the place in `handle_message` where the user message is appended to `conversation`. It currently looks roughly like:

```python
        try:
            now = datetime.now()
            time_prefix = f"[current time: {now.strftime('%Y-%m-%d %H:%M')}]\n"
        except Exception:
            time_prefix = ""
        conversation.append({"role": "user", "content": time_prefix + message})
        self._app.session_store.append_message(session_id, role="user", content=message)
```

Replace with:

```python
        try:
            now = datetime.now()
            time_prefix = f"[current time: {now.strftime('%Y-%m-%d %H:%M')}]\n"
        except Exception:
            time_prefix = ""

        # Multi-Part: stitch session attachments into the user message
        # so the agent can read them natively.
        attachment_blocks, evicted = self._build_attachment_blocks(
            session_id=session_id,
            budget_tokens=int(200_000 * 0.85),
        )
        evicted_stubs = "\n".join(
            f"[Attachment '{name}' parked — call attachment_load('{name}') "
            f"to bring it back into context.]"
            for name in evicted
        )
        text_part = (evicted_stubs + "\n\n" + time_prefix + message).strip()

        if attachment_blocks:
            user_content: list[dict] | str = attachment_blocks + [
                {"type": "text", "text": text_part}
            ]
        else:
            user_content = time_prefix + message

        conversation.append({"role": "user", "content": user_content})
        # Persist the pure text version — Multi-Part content can't be
        # cleanly replayed in JSONL conversation history, and the binary
        # data is still on disk in the session attachments folder.
        self._app.session_store.append_message(session_id, role="user", content=message)
```

- [ ] **Step 6: Run new tests**

```
PYTHONPATH=src pytest tests/test_chat_multipart.py -v
```

Expected: 7 passing.

- [ ] **Step 7: Run baseline**

```
PYTHONPATH=src pytest tests/ --ignore=tests/e2e --ignore=tests/integration -q
```

Expected: zero failures. The marker-removal might break tests that asserted on marker behavior — fix or remove them in Task 6 (Constitution Rule 2 update).

- [ ] **Step 8: Commit**

```bash
git add src/mycelos/chat/service.py tests/test_chat_multipart.py
git commit -m "feat(chat): Multi-Part attachment content + token-budget eviction"
```

---

## Task 4: Replace `/api/upload` with the lean save+emit version

**Files:**
- Modify: `src/mycelos/gateway/routes.py`
- Test: `tests/test_upload_flow.py`

The /api/upload handler today is ~150 lines that ingest, marker-write, branch on vision-needed. Replaced by ~30 lines that save + emit.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_upload_flow.py`:

```python
"""Tests for the new /api/upload + /api/sessions/.../attachments flow."""

from __future__ import annotations

import io
import os
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client() -> TestClient:
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["MYCELOS_MASTER_KEY"] = "upload-flow-test-key"
        from mycelos.app import App
        from mycelos.gateway.server import create_app
        App(Path(tmp)).initialize()
        fastapi_app = create_app(Path(tmp), no_scheduler=True, host="0.0.0.0")
        with TestClient(fastapi_app) as c:
            yield c, Path(tmp)


def test_upload_saves_to_session_folder(client) -> None:
    c, tmp = client
    files = {"file": ("hello.txt", io.BytesIO(b"hi"), "text/plain")}
    resp = c.post("/api/upload", files=files, data={"session_id": "s-test"})
    assert resp.status_code == 200, resp.text

    saved = tmp / "sessions" / "s-test" / "attachments" / "hello.txt"
    assert saved.exists()
    assert saved.read_bytes() == b"hi"


def test_upload_does_not_write_marker_into_session_history(client) -> None:
    c, tmp = client
    files = {"file": ("doc.pdf", io.BytesIO(b"%PDF-fake"), "application/pdf")}
    resp = c.post("/api/upload", files=files, data={"session_id": "s-mk"})
    assert resp.status_code == 200, resp.text

    # No `[System: User uploaded ...]` marker should be persisted
    from mycelos.app import App
    app = App(tmp)
    msgs = app.session_store.load_messages("s-mk")
    assert all(
        not m.get("content", "").lstrip().startswith("[System:")
        for m in msgs
    ), msgs


def test_upload_does_not_auto_ingest_into_kb(client) -> None:
    c, tmp = client
    files = {"file": ("auto.pdf", io.BytesIO(b"%PDF-fake"), "application/pdf")}
    resp = c.post("/api/upload", files=files, data={"session_id": "s-kb"})
    assert resp.status_code == 200

    from mycelos.app import App
    app = App(tmp)
    notes = app.storage.fetchall(
        "SELECT path FROM knowledge_notes WHERE path LIKE '%auto%'"
    )
    assert notes == [], notes


def test_upload_oversized_pdf_rejected(client) -> None:
    c, _ = client
    big = b"x" * (33 * 1024 * 1024)
    files = {"file": ("big.pdf", io.BytesIO(big), "application/pdf")}
    resp = c.post("/api/upload", files=files, data={"session_id": "s-big"})
    assert resp.status_code == 200  # SSE returns 200 with error event in stream
    assert "too large" in resp.text.lower()


def test_upload_unsupported_type_rejected(client) -> None:
    c, _ = client
    files = {"file": ("evil.exe", io.BytesIO(b"\x00\x01"), "application/octet-stream")}
    resp = c.post("/api/upload", files=files, data={"session_id": "s-evil"})
    assert "unsupported" in resp.text.lower()


def test_serve_attachment_endpoint(client) -> None:
    c, _ = client
    files = {"file": ("readme.txt", io.BytesIO(b"content"), "text/plain")}
    c.post("/api/upload", files=files, data={"session_id": "s-srv"})

    resp = c.get("/api/sessions/s-srv/attachments/readme.txt")
    assert resp.status_code == 200
    assert resp.content == b"content"


def test_serve_attachment_path_traversal(client) -> None:
    c, _ = client
    resp = c.get("/api/sessions/s/attachments/../../etc/passwd")
    assert resp.status_code in (400, 404)


def test_serve_attachment_missing(client) -> None:
    c, _ = client
    resp = c.get("/api/sessions/no-such/attachments/no.txt")
    assert resp.status_code == 404
```

- [ ] **Step 2: Run, confirm fails (or passes the wrong way)**

```
PYTHONPATH=src pytest tests/test_upload_flow.py -v
```

Expected: most fail. The current `/api/upload` writes markers + auto-ingests, so the "does NOT" assertions will fail. The new endpoint at `/api/sessions/.../attachments/<filename>` doesn't exist yet → 404.

- [ ] **Step 3: Replace `/api/upload` body in `routes.py`**

Open `src/mycelos/gateway/routes.py`. Find `@api.post("/api/upload")` (around line 1597). Replace the **entire** handler body (everything from `async def handle_upload(` up to the end of the final `return StreamingResponse(...)` for the text-extract branch, ~130 lines) with:

```python
    @api.post("/api/upload")
    async def handle_upload(
        request: Request,
        file: UploadFile,
        session_id: str = "",
    ) -> StreamingResponse:
        """Save an uploaded file to the session's attachment folder and
        emit a file-attached SSE event so the chat UI can render its
        preview card. The file rides along in every subsequent LLM call
        for this session via ChatService Multi-Part build — no marker,
        no auto-ingest.
        """
        from mycelos.chat.events import (
            error_event, done_event, session_event, file_attached_event,
        )
        from mycelos.files.session_attachments import (
            SessionAttachmentStore, SIZE_CAPS_BYTES, content_kind,
        )
        from pathlib import Path as _Path

        service = api.state.chat_service
        mycelos = api.state.mycelos
        user_id = _resolve_user_id(request)

        if not session_id:
            session_id = service.create_session(user_id=user_id)

        file_bytes = await file.read()
        filename = file.filename or "unnamed"
        kind = content_kind(_Path(filename))

        if kind == "unsupported":
            async def err():
                yield session_event(session_id).to_sse()
                yield error_event(
                    f"Unsupported file type for chat attachments: {filename}"
                ).to_sse()
                yield done_event().to_sse()
            return StreamingResponse(err(), media_type="text/event-stream")

        cap = SIZE_CAPS_BYTES.get(kind, 0)
        if cap and len(file_bytes) > cap:
            async def too_large():
                yield session_event(session_id).to_sse()
                yield error_event(
                    f"File too large ({len(file_bytes)} bytes > {cap} for {kind})"
                ).to_sse()
                yield done_event().to_sse()
            return StreamingResponse(too_large(), media_type="text/event-stream")

        store = SessionAttachmentStore(mycelos.data_dir / "sessions")
        try:
            saved = store.save(session_id, file_bytes, filename)
        except ValueError as e:
            async def save_err():
                yield session_event(session_id).to_sse()
                yield error_event(str(e)).to_sse()
                yield done_event().to_sse()
            return StreamingResponse(save_err(), media_type="text/event-stream")

        # Map the internal kind to the frontend's preview-card discriminator.
        ui_kind = {"document": "pdf", "image": "image", "text": "other"}.get(kind, "other")
        preview = file_attached_event(
            filename=saved.name,
            url=f"/api/sessions/{session_id}/attachments/{saved.name}",
            kind=ui_kind,
            size=len(file_bytes),
        )

        async def stream():
            yield session_event(session_id).to_sse()
            yield preview.to_sse()
            yield done_event().to_sse()
        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache"},
        )
```

- [ ] **Step 4: Add the new attachment-serve endpoint**

In the same file, locate the existing `@api.get("/api/inbox/{filename:path}")` endpoint (added in an earlier task). Replace it with the new session-attachment endpoint:

```python
    @api.get("/api/sessions/{session_id}/attachments/{filename:path}")
    async def serve_session_attachment(session_id: str, filename: str) -> Any:
        """Serve a file from the session's attachment folder.

        Path-traversal-safe: the resolved filename must live inside the
        session's attachments folder. Used by the chat preview card to
        render images / link to PDFs.
        """
        from starlette.responses import FileResponse
        mycelos = api.state.mycelos
        base = (mycelos.data_dir / "sessions" / session_id / "attachments").resolve()
        target = (base / filename).resolve()
        try:
            target.relative_to(base)
        except ValueError:
            return JSONResponse({"error": "path traversal blocked"}, status_code=400)
        if not target.is_file():
            return JSONResponse({"error": "not found"}, status_code=404)
        return FileResponse(str(target), filename=target.name)
```

The old `@api.get("/api/inbox/{filename:path}")` endpoint is **removed** entirely — search for `inbox_file_serve` and delete it.

- [ ] **Step 5: Run the new tests**

```
PYTHONPATH=src pytest tests/test_upload_flow.py -v
```

Expected: 8 passing.

- [ ] **Step 6: Run baseline**

```
PYTHONPATH=src pytest tests/ --ignore=tests/e2e --ignore=tests/integration -q
```

Expected: some existing tests will fail because they asserted old upload behavior (markers, auto-ingest). Note the failures and address in Task 6. New baseline should be: only the to-be-fixed tests fail.

- [ ] **Step 7: Commit**

```bash
git add src/mycelos/gateway/routes.py tests/test_upload_flow.py
git commit -m "refactor(upload): /api/upload saves to session folder, no marker, no auto-ingest"
```

---

## Task 5: Telegram channel — same flow with pending_attachment state

**Files:**
- Modify: `src/mycelos/channels/telegram.py`

Telegram receives Document or Photo, saves to session attachments, then either runs the caption as a chat message or stashes a pending_attachment until the next text message.

- [ ] **Step 1: Read the current telegram.py handlers**

```
grep -n "@dp.message(F.document)\|@dp.message(F.photo)\|async def handle_document\|async def handle_photo" src/mycelos/channels/telegram.py
```

The handlers are around lines 297 and 398. Read 80 lines around each to understand current behavior, particularly:
- How `_app` is referenced (module-level singleton via `set_app`).
- How the session id is resolved per Telegram user (`_ensure_session_for_user` or similar).
- How a chat message is dispatched (`_process_user_message` / `ChatService.handle_message`).

Whatever the existing function names are, reuse them rather than inventing new ones.

- [ ] **Step 2: Add module-level `_pending_attachments`**

Near the top of `telegram.py`, after the existing module-level state (e.g. `_app`, `_bot`), add:

```python
# Maps session_id → filename of an attachment uploaded without a caption.
# Cleared when the next text message arrives in that session — that text
# becomes the user's question and the attachment is already in the
# session's attachments folder, so the Multi-Part build picks it up.
_pending_attachments: dict[str, str] = {}
```

- [ ] **Step 3: Replace `handle_document`**

Find the existing `@dp.message(F.document)` handler. Replace its body with:

```python
@dp.message(F.document)
async def handle_document(message: types.Message) -> None:
    """Save the document to the session's attachment folder. If the
    message has a caption, treat that as the user's question and run
    a normal chat turn — the Multi-Part build picks the file up. If
    there's no caption, stash a pending_attachment marker and ask
    'Was möchtest du wissen?' so the next text turn carries the question.
    """
    if _app is None or _bot is None:
        return
    doc = message.document
    if doc is None:
        return

    try:
        file = await _bot.get_file(doc.file_id)
        # The aiogram download path varies between versions — match
        # the existing pattern in this file. Look at how handle_photo
        # downloads bytes today and mirror that.
        file_bytes_io = await _bot.download(file)
        file_bytes = file_bytes_io.read() if hasattr(file_bytes_io, "read") else file_bytes_io
    except Exception as e:
        await _safe_answer(message, f"Konnte die Datei nicht laden: {e}")
        return

    session_id = _ensure_session_for_user(message.from_user.id) if message.from_user else _app.chat_service.create_session()

    from mycelos.files.session_attachments import (
        SessionAttachmentStore, SIZE_CAPS_BYTES, content_kind,
    )
    from pathlib import Path as _Path

    filename = doc.file_name or f"doc-{doc.file_unique_id}"
    kind = content_kind(_Path(filename))
    if kind == "unsupported":
        await _safe_answer(message, f"Dateityp nicht unterstützt: {filename}")
        return

    cap = SIZE_CAPS_BYTES.get(kind, 0)
    if cap and len(file_bytes) > cap:
        await _safe_answer(
            message,
            f"Datei zu groß ({len(file_bytes) // 1024 // 1024} MB > "
            f"{cap // 1024 // 1024} MB für {kind}).",
        )
        return

    store = SessionAttachmentStore(_app.data_dir / "sessions")
    try:
        saved = store.save(session_id, file_bytes, filename)
    except ValueError as e:
        await _safe_answer(message, f"Speichern fehlgeschlagen: {e}")
        return

    if message.caption:
        # Caption is the user's question — process normally.
        await _process_user_message(message, message.caption, session_id)
    else:
        _pending_attachments[session_id] = saved.name
        await _safe_answer(message, "Was möchtest du wissen?")
```

If the existing handler uses different download/dispatch primitives (e.g. `inbox.save` was called with bytes; the dispatch was named `handle_chat_message`), preserve those names — only the file-store call and the no-caption branch are new logic.

- [ ] **Step 4: Replace `handle_photo`**

Mirror Step 3 for the `@dp.message(F.photo)` handler. The differences are:

- `message.photo[-1]` to pick the largest size.
- Filename is derived as `f"photo-{photo.file_unique_id}.jpg"`.
- Otherwise the flow is identical.

```python
@dp.message(F.photo)
async def handle_photo(message: types.Message) -> None:
    if _app is None or _bot is None:
        return
    photo = message.photo[-1] if message.photo else None
    if photo is None:
        return

    try:
        file = await _bot.get_file(photo.file_id)
        data_io = await _bot.download(file)
        data = data_io.read() if hasattr(data_io, "read") else data_io
    except Exception as e:
        await _safe_answer(message, f"Konnte das Bild nicht laden: {e}")
        return

    session_id = _ensure_session_for_user(message.from_user.id) if message.from_user else _app.chat_service.create_session()
    filename = f"photo-{photo.file_unique_id}.jpg"

    from mycelos.files.session_attachments import (
        SessionAttachmentStore, SIZE_CAPS_BYTES,
    )
    if len(data) > SIZE_CAPS_BYTES["image"]:
        await _safe_answer(
            message,
            f"Bild zu groß ({len(data) // 1024 // 1024} MB > "
            f"{SIZE_CAPS_BYTES['image'] // 1024 // 1024} MB).",
        )
        return

    store = SessionAttachmentStore(_app.data_dir / "sessions")
    saved = store.save(session_id, data, filename)

    if message.caption:
        await _process_user_message(message, message.caption, session_id)
    else:
        _pending_attachments[session_id] = saved.name
        await _safe_answer(message, "Was möchtest du wissen?")
```

- [ ] **Step 5: Clear pending_attachment on the next text message**

Find the text-message handler (likely `@dp.message(F.text)` or a generic message handler). At the top of the handler, after the session_id is resolved, add:

```python
    # If the user sent an attachment without a caption last turn,
    # clear the pending marker — the attachment is in the session's
    # folder and the Multi-Part build sees it. We only need to clear
    # the channel-side flag.
    _pending_attachments.pop(session_id, None)
```

If there's no central text-handler, place this in whichever function dispatches a text-only Telegram message to `ChatService.handle_message`.

- [ ] **Step 6: Smoke test**

There are no automated Telegram-integration tests in the existing test suite. Verify manually after Task 9 push, or skip live testing until then.

For now, just confirm imports are valid:

```
PYTHONPATH=src python3 -c "from mycelos.channels import telegram"
```

Expected: no errors.

- [ ] **Step 7: Run baseline**

```
PYTHONPATH=src pytest tests/ --ignore=tests/e2e --ignore=tests/integration -q
```

Expected: no new failures introduced by Telegram changes (these handlers aren't unit-tested today).

- [ ] **Step 8: Commit**

```bash
git add src/mycelos/channels/telegram.py
git commit -m "refactor(telegram): document/photo handlers use SessionAttachmentStore"
```

---

## Task 6: Update `mycelos.md` prompt + clean up Constitution-Rule-2 tests + remove Auto-Ingest

**Files:**
- Modify: `src/mycelos/prompts/mycelos.md`
- Modify: `tests/security/test_constitution_rule_2.py` (if it asserted upload-creates-generation)
- Possibly delete or simplify: any other tests that asserted on marker presence

The prompt section that explained markers is obsolete. Constitution-Rule-2 tests that asserted upload created a config generation (via the marker write to session_store) are now wrong — uploads no longer mutate any state-tables.

- [ ] **Step 1: Replace the "## File uploads" section in mycelos.md**

Open `src/mycelos/prompts/mycelos.md`. Find `## File uploads` (around line 87). Replace the entire section through the next `##` heading with:

```markdown
## File uploads

The chat UI lets the user upload files (PDF, DOCX, images, plain text). Uploaded files are automatically attached to every subsequent LLM call in this session — you have direct access to them via Anthropic's Multi-Part content. Read them like any other text or image; you don't need a tool to "open" them.

If the user wants to keep a file longer than the conversation: call `note_save_attachment(filename, summary, tags)`. The file is copied to the Knowledge Base permanently with the summary you generated. Use this when the user says "merk dir das" / "speicher das" / "save this for later".

If you see a "[Attachment '...' parked — call attachment_load(...)]" stub instead of the file content, the attachment was evicted due to token-budget pressure. Call `attachment_load(filename)` to bring it back for the next turn.
```

- [ ] **Step 2: Find existing tests asserting on upload markers**

```
grep -rn "User uploaded\|System: User\|knowledge.document.ingested" tests/ 2>/dev/null
```

Each match is a candidate for update or delete. The test should be:
- **Updated** if it tests the upload endpoint AND asserts something the new flow still does (file is saved, response is 200, etc.).
- **Deleted** if it specifically tests marker presence or auto-ingest behavior.

- [ ] **Step 3: Update Constitution-Rule-2 tests**

Open `tests/security/test_constitution_rule_2.py`. The `/api/upload` endpoint is no longer state-mutating from a Constitution Rule 2 perspective (no longer writes markers, no longer ingests to KB). If there's a test like `test_post_upload_creates_generation`, **delete it**. The endpoint isn't in the audit set anymore.

If no such test exists in that file (Rule 2 covers the connector/credential/agent endpoints, not upload), nothing to change here.

- [ ] **Step 4: Run all tests, fix collateral damage**

```
PYTHONPATH=src pytest tests/ --ignore=tests/e2e --ignore=tests/integration -q
```

Expected: zero failures. Any test that was failing because of the upload-flow change should now pass after the prompt + tests update.

If a test still fails:
- Read the failure message
- Decide: is the assertion about old behavior we're removing, or about new behavior we should preserve?
- Old behavior (markers, auto-ingest): delete the test (or the specific assertion)
- New behavior: update the assertion to match Multi-Part flow

- [ ] **Step 5: Commit**

```bash
git add src/mycelos/prompts/mycelos.md tests/
git commit -m "refactor(prompts+tests): drop marker-era File uploads section + obsolete tests"
```

---

## Task 7: Wire session deletion to clean up attachments folder

**Files:**
- Modify: wherever `session_store.delete_session` / equivalent is called (likely `routes.py` `DELETE /api/sessions/<id>`)

When a session is deleted, the attachments folder must go too. Otherwise we leak files.

- [ ] **Step 1: Find session-delete code paths**

```
grep -rn "delete_session\|DELETE.*sessions\b" src/mycelos/gateway/routes.py src/mycelos/sessions/store.py
```

Likely matches:
- `routes.py`: `@api.delete("/api/sessions/{session_id}")` calling `session_store.delete_session(...)` or similar
- `sessions/store.py`: the SessionStore.delete_session or equivalent method

- [ ] **Step 2: Add attachment cleanup on the same path**

In the routes.py session-delete handler (or wherever the delete happens), AFTER the session_store cleanup, add:

```python
        # Also drop the per-session attachment folder.
        from mycelos.files.session_attachments import SessionAttachmentStore
        attach_store = SessionAttachmentStore(mycelos.data_dir / "sessions")
        try:
            attach_store.delete_session(session_id)
        except Exception:
            logger.warning("Failed to clean session attachments for %s", session_id, exc_info=True)
```

If the SessionStore class itself owns the attachments-folder lifecycle (matter of taste), put it there instead — but keeping it at the route handler is simpler and matches where session_id is in scope.

- [ ] **Step 3: Quick smoke test**

Build a small one-off script to verify the cleanup runs:

```
PYTHONPATH=src python3 -c "
import os, tempfile
from pathlib import Path
os.environ['MYCELOS_MASTER_KEY'] = 'cleanup-test'
from mycelos.app import App
from mycelos.files.session_attachments import SessionAttachmentStore

with tempfile.TemporaryDirectory() as tmp:
    app = App(Path(tmp))
    app.initialize()
    store = SessionAttachmentStore(app.data_dir / 'sessions')
    store.save('s1', b'hello', 'a.txt')
    print('before delete:', (Path(tmp) / 'sessions' / 's1').exists())
    store.delete_session('s1')
    print('after delete:', (Path(tmp) / 'sessions' / 's1').exists())
"
```

Expected output: `before delete: True`, `after delete: False`.

- [ ] **Step 4: Run baseline**

```
PYTHONPATH=src pytest tests/ --ignore=tests/e2e --ignore=tests/integration -q
```

Expected: zero failures.

- [ ] **Step 5: Commit**

```bash
git add src/mycelos/gateway/routes.py
git commit -m "feat(sessions): clean up attachments folder on session delete"
```

---

## Task 8: Remove the obsolete /api/inbox endpoint references and clean up

**Files:**
- Verify: nothing else still references `/api/inbox/`

The `/api/inbox/<filename>` endpoint was added a few tasks ago to serve preview files. It's now replaced by `/api/sessions/<id>/attachments/<filename>`. Make sure no frontend code still points at the old URL.

- [ ] **Step 1: Grep for stale references**

```
grep -rn "/api/inbox/" src/mycelos/ tests/ 2>&1 | grep -v __pycache__
```

Expected: zero matches. If any remain, update them to use `/api/sessions/<id>/attachments/<filename>` — but the file-attached event already carries the right URL, so most callsites should already be correct.

If something does match, edit it manually and re-run the grep until clean.

- [ ] **Step 2: Run baseline**

```
PYTHONPATH=src pytest tests/ --ignore=tests/e2e --ignore=tests/integration -q
```

Expected: zero failures.

- [ ] **Step 3: Commit (only if Step 1 found something)**

If you needed to fix references:

```bash
git add <whatever-files>
git commit -m "refactor(uploads): drop residual /api/inbox/ references"
```

If nothing was found, skip this commit.

---

## Task 9: CHANGELOG + push + manual smoke

**Files:**
- Modify: `CHANGELOG.md`

- [ ] **Step 1: Add entry under the current week**

Open `CHANGELOG.md`. Find the current week's heading (likely `## Week 18 (2026)` — if not present, add it above `## Week 17 (2026)`). Append:

```markdown
### Session attachments — Multi-Part LLM content replaces marker pipeline
- File uploads in chat (Web UI + Telegram) now save to a per-session folder at `~/.mycelos/sessions/<id>/attachments/<filename>` and are automatically attached to every subsequent LLM call as Anthropic Multi-Part content (Document for PDFs, Image for images, Text for plaintext). The agent reads them directly, no marker pipeline needed.
- Two new tools the agent uses to manage attachments:
  - `note_save_attachment(filename, summary, tags)` — promotes a session attachment to the permanent Knowledge Base (file + agent-generated summary).
  - `attachment_load(filename)` — forces a previously-evicted attachment back into the next turn's context.
- Token-budget eviction: when the model context is near full, oldest attachments are replaced by a text stub like `[Attachment 'old.pdf' parked — call attachment_load('old.pdf') ...]`. The agent can re-load any of them via `attachment_load`.
- Session deletion now also drops the attachments folder.
- Per-type size caps enforced at upload time: PDFs ≤ 32 MB, images ≤ 5 MB, text ≤ 10 MB. Other binary types rejected.
- Removed: the `[System: User uploaded ...]` marker write in `/api/upload`, the auto-ingest of PDFs into the Knowledge Base from `/api/upload` and Telegram, the marker-detection / promote-to-system-prompt logic in `ChatService`, the marker-era prompt section in `mycelos.md`, the `/api/inbox/<filename>` endpoint.
- Spec / plan: `docs/superpowers/specs/2026-04-29-session-attachments-design.md`, `docs/superpowers/plans/2026-04-29-session-attachments-plan.md`.
```

- [ ] **Step 2: Final baseline**

```
PYTHONPATH=src pytest tests/ --ignore=tests/e2e --ignore=tests/integration -q
```

Expected: zero failures.

- [ ] **Step 3: Commit + push**

```bash
git add CHANGELOG.md
git commit -m "docs(changelog): session attachments + Multi-Part LLM content"
git push origin main
```

- [ ] **Step 4: Manual smoke test**

After the push, restart Mycelos locally:

```bash
mycelos serve
```

In a browser, load `http://localhost:9100/pages/chat.html` and:
1. Upload `BH 03 2026.pdf` (or any PDF you have handy).
2. Ask: "Was steht da drin?"
3. Verify the agent answers with content from the file (not "I don't see a document").
4. Ask: "Speicher das in der Wissensdatenbank."
5. Verify the agent calls `note_save_attachment` and reports a knowledge note path.
6. Open `~/.mycelos/sessions/<session_id>/attachments/` — the original PDF should still be there.
7. Open the Knowledge Base UI / list — the new note should appear.

For Telegram (if a bot is configured): send a PDF without caption, verify "Was möchtest du wissen?" reply, send a follow-up text — verify the file is in context.

---

## Self-review notes

Spec coverage check (against `2026-04-29-session-attachments-design.md`):

- D1 (per-session folders) → Task 1 (SessionAttachmentStore).
- D2 (files in every LLM call) → Task 3 (`_build_attachment_blocks` + handle_message integration).
- D3 (token-budget eviction) → Task 3 (eviction loop, force_include skip).
- D4 (two new tools) → Task 2.
- D5 (Telegram parity) → Task 5.
- D6 (size limits) → Task 1 (`SIZE_CAPS_BYTES`) + Task 4 (upload validation).
- D7 (cleanup of marker plumbing) → Task 4 (routes.py) + Task 5 (telegram) + Task 3 (chat/service.py) + Task 6 (prompts/tests).
- D8 (no new frontend UI) → respected; chat.html unchanged because the URL is delivered via the existing event payload.
- Success criterion 1-9 → covered across Tasks 1-9.

Type / name consistency:

- `SessionAttachmentStore` API stays consistent (`save`, `list`, `read`, `delete_session`).
- `content_kind` / `media_type` / `SIZE_CAPS_BYTES` all referenced under the same name across tasks.
- `_session_force_include` field, `mark_force_include` method, `_build_attachment_blocks` method — all referenced consistently.
- Tool names: `note_save_attachment`, `attachment_load` — consistent in spec, prompt, registry, tests.

No placeholders. Every step shows the actual code or command. The only "find the existing X" steps are deliberately delegated to grep because the surrounding code patterns vary slightly between aiogram versions / refactors.
