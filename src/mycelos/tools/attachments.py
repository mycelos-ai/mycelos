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
            "a file that's no longer visible to you (you'll see a brief "
            "placeholder stub instead of the file content when a file has "
            "been evicted)."
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
    raw_tags = args.get("tags") or []
    tags: list[str] = raw_tags if isinstance(raw_tags, list) else [raw_tags]

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

    try:
        note_path = app.knowledge_base.store_document(
            file_bytes=data,
            filename=filename,
            title="",
            summary=summary,
            tags=tags,
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
