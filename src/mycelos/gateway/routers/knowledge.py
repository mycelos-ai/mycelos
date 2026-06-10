"""Knowledge + Organizer endpoints — notes, topics, graph, import, suggestions."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from mycelos.gateway.routers._helpers import resolve_user_id

router = APIRouter()


@router.get("/api/knowledge/notes")
async def knowledge_notes(
    request: Request,
    query: str | None = None,
    type: str | None = None,
    status: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """List/search notes for the web knowledge view."""
    kb = request.app.state.mycelos.knowledge_base
    if query:
        return kb.search(query=query, type=type, limit=limit)
    return kb.list_notes(type=type, status=status, limit=limit)


@router.post("/api/knowledge/notes")
async def knowledge_create_note(request: Request) -> Any:
    """Create a note via Quick Capture.

    Runs the deterministic DE+EN parser over the payload, applies
    deterministic bucketing, and delegates to KnowledgeService.write.
    Caller-supplied fields always win over parser defaults.
    """
    from mycelos.knowledge.parse_note import parse_note_text
    from mycelos.knowledge.service import bucket_note

    mycelos = request.app.state.mycelos
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
        created_by="user",
        source={"kind": "quick_capture"},
    )

    try:
        mycelos.audit.log(
            "knowledge.note.created",
            user_id=resolve_user_id(request),
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


@router.post("/api/knowledge/ingest/{source}")
async def knowledge_ingest(source: str, request: Request) -> dict[str, Any]:
    """Day-one knowledge: pull recent content from a connected service
    (e.g. gmail) into the knowledge base. Idempotent via external ids;
    the organizer classifies the new notes afterwards."""
    from mycelos.knowledge.connector_ingest import INGEST_SOURCES

    ingest_fn = INGEST_SOURCES.get(source)
    if ingest_fn is None:
        return JSONResponse(
            {"error": f"Unknown ingest source: {source}",
             "available": sorted(INGEST_SOURCES)},
            status_code=404,
        )

    mycelos = request.app.state.mycelos
    try:
        body = await request.json()
    except Exception:
        body = {}
    kwargs: dict[str, Any] = {"user_id": resolve_user_id(request)}
    if body.get("max_items"):
        kwargs["max_items"] = int(body["max_items"])
    if body.get("query"):
        kwargs["query"] = str(body["query"])

    try:
        result = ingest_fn(mycelos, **kwargs)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)
    if result.get("error"):
        return JSONResponse(result, status_code=502)
    return result


@router.post("/api/knowledge/enhance")
async def knowledge_enhance(request: Request) -> dict[str, Any]:
    """AI-enhance a note — expand, improve, or organize content using a cheap model."""
    mycelos = request.app.state.mycelos
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


@router.put("/api/knowledge/notes/{path:path}")
async def knowledge_update_note(path: str, request: Request) -> dict[str, Any]:
    """Update an existing note (content, status, tags, priority, parent_path, organizer_state, archive)."""
    mycelos = request.app.state.mycelos
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
                user_id=resolve_user_id(request),
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
                user_id=resolve_user_id(request),
                details={"path": path},
            )
        except Exception:
            pass

    return {"status": "updated", "path": path}


@router.get("/api/knowledge/notes/{path:path}")
async def knowledge_note(path: str, request: Request) -> dict[str, Any]:
    """Fetch a single note by path."""
    kb = request.app.state.mycelos.knowledge_base
    note = kb.read(path)
    if not note:
        return JSONResponse({"error": "not_found", "path": path}, status_code=404)
    return note


@router.get("/api/knowledge/graph")
async def knowledge_graph(request: Request) -> dict[str, Any]:
    """Return note graph (nodes + links) for web visualization."""
    kb = request.app.state.mycelos.knowledge_base
    return kb.get_graph_data()


@router.get("/api/knowledge/topics")
async def knowledge_topics(request: Request) -> list[dict[str, Any]]:
    """List top-level topic notes with child counts."""
    kb = request.app.state.mycelos.knowledge_base
    topics = kb.list_topics(top_level_only=True)
    for t in topics:
        children = kb.list_children(t["path"])
        t["child_count"] = len(children)
        t["open_tasks"] = sum(1 for c in children if c.get("type") == "task" and c.get("status") in ("open", "in-progress"))
    return topics


@router.post("/api/knowledge/topics")
async def knowledge_create_topic(request: Request) -> dict[str, Any]:
    """Create a new topic. Body: {name, tags?, parent?}."""
    mycelos = request.app.state.mycelos
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
            user_id=resolve_user_id(request),
            details={"path": path, "name": name},
        )
    except Exception:
        pass
    return {"path": path, "name": name}


@router.post("/api/knowledge/topics/{path:path}/rename")
async def knowledge_rename_topic(path: str, request: Request) -> dict[str, Any]:
    """Rename a topic. Body: {name: "New Name"}."""
    mycelos = request.app.state.mycelos
    body = await request.json()
    name = body.get("name", "").strip()
    if not name:
        return JSONResponse({"error": "name is required"}, status_code=422)
    kb = mycelos.knowledge_base
    new_path = kb.rename_topic(path, name)
    try:
        mycelos.audit.log(
            "knowledge.topic.renamed",
            user_id=resolve_user_id(request),
            details={"old_path": path, "new_path": new_path, "name": name},
        )
    except Exception:
        pass
    return {"status": "renamed", "old_path": path, "new_path": new_path, "name": name}


@router.get("/api/knowledge/topics/{path:path}/children")
async def knowledge_topic_children(path: str, request: Request) -> list[dict[str, Any]]:
    """List notes belonging to a topic."""
    kb = request.app.state.mycelos.knowledge_base
    return kb.list_children(path)


@router.post("/api/knowledge/notes/{path:path}/done")
async def knowledge_note_done(path: str, request: Request) -> dict[str, Any]:
    """Mark a task as done."""
    kb = request.app.state.mycelos.knowledge_base
    success = kb.mark_done(path)
    if not success:
        return JSONResponse({"error": "not_found", "path": path}, status_code=404)
    return {"status": "done"}


@router.post("/api/knowledge/notes/{path:path}/remind")
async def knowledge_note_remind(path: str, request: Request) -> dict[str, Any]:
    """Set a reminder on a note.

    Body: ``{"when": "<due date>", "remind_at": "<ISO datetime>"}``.
    ``remind_at`` is optional — omit it to fire "sometime on due day".
    """
    body = await request.json()
    kb = request.app.state.mycelos.knowledge_base
    success = kb.set_reminder(
        path,
        due=body.get("when", ""),
        remind_at=body.get("remind_at") or None,
    )
    if not success:
        return JSONResponse({"error": "not_found", "path": path}, status_code=404)
    return {"status": "reminder_set"}


@router.post("/api/knowledge/notes/{path:path}/move")
async def knowledge_note_move(path: str, request: Request) -> dict[str, Any]:
    """Move a note to a different topic."""
    body = await request.json()
    kb = request.app.state.mycelos.knowledge_base
    success = kb.move_to_topic(path, body.get("topic", ""))
    if not success:
        return JSONResponse({"error": "not_found", "path": path}, status_code=404)
    return {"status": "moved"}


@router.get("/api/knowledge/documents/{path:path}")
async def knowledge_document_serve(path: str, request: Request) -> Any:
    """Serve an original document file (PDF, DOCX, etc.) for a Knowledge note.

    `path` is the note path (e.g. `notes/2026-04-29-foo`). We look up
    the linked source_file via `knowledge_notes.source_file` and
    serve that.
    """
    from starlette.responses import FileResponse
    mycelos = request.app.state.mycelos
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


@router.post("/api/knowledge/notes/{path:path}/vision")
async def knowledge_note_vision(path: str, request: Request) -> dict[str, Any]:
    """Trigger Vision analysis for a scanned document note."""
    from mycelos.knowledge.ingest import vision_analyze
    mycelos = request.app.state.mycelos
    result = vision_analyze(mycelos, path)
    if result["status"] == "error":
        return JSONResponse({"error": result["message"]}, status_code=400)
    return result


@router.post("/api/knowledge/notes/{path:path}/split")
async def knowledge_note_split(path: str, request: Request) -> dict[str, Any]:
    """Split a note into multiple sub-notes via LLM analysis."""
    mycelos = request.app.state.mycelos
    body = {}
    content_type = request.headers.get("content-type", "")
    if content_type.startswith("application/json"):
        body = await request.json()

    from mycelos.tools.knowledge import execute_note_split
    result = execute_note_split(
        {"path": path, "confirm": body.get("confirm", False), "sections": body.get("sections")},
        {"app": mycelos, "user_id": resolve_user_id(request)},
    )
    if result.get("status") == "error":
        return JSONResponse({"error": result["message"]}, status_code=400)
    return result


@router.get("/api/organizer/suggestions")
async def organizer_list(request: Request) -> Any:
    from mycelos.knowledge.inbox import InboxService
    mycelos = request.app.state.mycelos
    inbox = InboxService(mycelos.storage)
    return inbox.list_pending_by_topic()


@router.post("/api/organizer/accept-all")
async def organizer_accept_all(request: Request) -> dict[str, Any]:
    """Accept every pending suggestion: create new topics, move notes."""
    from mycelos.knowledge.inbox import InboxService
    mycelos = request.app.state.mycelos
    kb = mycelos.knowledge_base
    inbox = InboxService(mycelos.storage)
    user_id = resolve_user_id(request)

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


@router.post("/api/organizer/suggestions/{sid}/accept")
async def organizer_accept(sid: int, request: Request) -> Any:
    from mycelos.knowledge.inbox import InboxService
    mycelos = request.app.state.mycelos
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
                    resolve_user_id(request),
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
            user_id=resolve_user_id(request),
            details={"id": sid, "kind": kind},
        )
    except Exception:
        pass
    return {"ok": True, "id": sid, "kind": kind}


@router.post("/api/organizer/suggestions/{sid}/dismiss")
async def organizer_dismiss(sid: int, request: Request) -> Any:
    from mycelos.knowledge.inbox import InboxService
    mycelos = request.app.state.mycelos
    inbox = InboxService(mycelos.storage)
    if not inbox.get(sid):
        return JSONResponse({"error": "not found"}, status_code=404)
    inbox.dismiss(sid)
    try:
        mycelos.audit.log(
            "organizer.suggestion.dismissed",
            user_id=resolve_user_id(request),
            details={"id": sid},
        )
    except Exception:
        pass
    return {"ok": True, "id": sid}


@router.post("/api/organizer/run")
async def organizer_run(request: Request) -> dict[str, Any]:
    mycelos = request.app.state.mycelos
    user_id = resolve_user_id(request)
    return mycelos.knowledge_organizer.run(user_id)


@router.post("/api/organizer/sweep-duplicates")
async def organizer_sweep_duplicates(request: Request) -> dict[str, Any]:
    """Scan all notes for duplicates and create merge suggestions."""
    mycelos = request.app.state.mycelos
    handler = mycelos.knowledge_organizer
    count = handler.sweep_duplicates(resolve_user_id(request))
    return {"duplicates_found": count}


@router.post("/api/knowledge/sync-relations")
async def knowledge_sync_relations(request: Request) -> dict[str, Any]:
    """Rebuild relation links from note content and frontmatter."""
    kb = request.app.state.mycelos.knowledge_base
    return kb.sync_relations()


@router.post("/api/knowledge/import")
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

    mycelos = request.app.state.mycelos
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
                organizer.run(resolve_user_id(request))
            except Exception:
                pass

    try:
        mycelos.audit.log(
            "knowledge.import",
            user_id=resolve_user_id(request),
            details={"mode": mode, "count": len(result.get("created", []))},
        )
    except Exception:
        pass

    return result
