"""Knowledge tools — notes, search, and linking."""

from __future__ import annotations

from typing import Any

from mycelos.tools.registry import ToolPermission

# --- Tool Schemas ---

NOTE_WRITE_SCHEMA = {
    "type": "function",
    "function": {
        "name": "note_write",
        "description": (
            "Create a new note in the knowledge base. Use for facts, tasks, decisions, references. "
            "For tasks with reminders: set reminder=true and due date. The system will notify via "
            "all active channels (chat, Telegram). For timed reminders, use note_remind after creating."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Note title"},
                "content": {"type": "string", "description": "Note content (Markdown)"},
                "note_type": {
                    "type": "string",
                    "enum": ["note", "task", "decision", "reference", "fact", "journal"],
                    "description": "Type of note",
                },
                "tags": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Tags for categorization",
                },
                "status": {
                    "type": "string",
                    "enum": ["active", "open", "in-progress", "done", "archived"],
                    "description": "Status (tasks use open/in-progress/done)",
                },
                "due": {
                    "type": "string",
                    "description": "Due date in YYYY-MM-DD format (for tasks)",
                },
                "priority": {
                    "type": "integer",
                    "enum": [0, 1, 2, 3],
                    "description": "Priority: 0=normal, 1=important, 2=urgent, 3=critical",
                },
                "topic": {
                    "type": "string",
                    "description": "Parent topic path to file this note under (e.g., 'topics/einkauf')",
                },
                "reminder": {
                    "type": "boolean",
                    "description": "If true, trigger notification when due date arrives",
                },
                "remind_in": {
                    "type": "string",
                    "description": "Relative reminder timer: '5m', '10min', '1h', '2h'. Starts countdown immediately.",
                },
            },
            "required": ["title", "content"],
        },
    },
}

NOTE_READ_SCHEMA = {
    "type": "function",
    "function": {
        "name": "note_read",
        "description": "Read a note from the knowledge base by path.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Note path (e.g., tasks/fix-planner)"},
            },
            "required": ["path"],
        },
    },
}

NOTE_SEARCH_SCHEMA = {
    "type": "function",
    "function": {
        "name": "note_search",
        "description": "Search the knowledge base. Uses full-text and semantic search. Can filter by type, status, and due date.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query"},
                "note_type": {"type": "string", "description": "Filter by type (note, task, topic, decision, reference)"},
                "status": {"type": "string", "description": "Filter by status (open, done, active, in-progress)"},
                "due": {"type": "string", "description": "Filter by due: 'today', 'overdue', 'this_week', or YYYY-MM-DD"},
                "limit": {"type": "integer", "description": "Max results (default 10)"},
            },
            "required": ["query"],
        },
    },
}

NOTE_LIST_SCHEMA = {
    "type": "function",
    "function": {
        "name": "note_list",
        "description": "List notes filtered by type, status, or due date.",
        "parameters": {
            "type": "object",
            "properties": {
                "note_type": {"type": "string", "description": "Filter by type (task, decision, note, etc.)"},
                "status": {"type": "string", "description": "Filter by status (open, done, active, etc.)"},
                "due": {"type": "string", "description": "Filter by due date: 'today', 'overdue', 'this_week', or YYYY-MM-DD"},
                "limit": {"type": "integer", "description": "Max results (default 20)"},
            },
        },
    },
}

NOTE_UPDATE_SCHEMA = {
    "type": "function",
    "function": {
        "name": "note_update",
        "description": "Update an existing note's status, tags, content, or priority.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Note path"},
                "status": {"type": "string", "description": "New status"},
                "tags": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "New tags (replaces existing)",
                },
                "due": {"type": "string", "description": "New due date (YYYY-MM-DD)"},
                "content": {"type": "string", "description": "New content or content to append"},
                "append": {"type": "boolean", "description": "If true, append content instead of replacing"},
                "priority": {"type": "integer", "description": "New priority (0-3)"},
            },
            "required": ["path"],
        },
    },
}

NOTE_LINK_SCHEMA = {
    "type": "function",
    "function": {
        "name": "note_link",
        "description": "Create a link between two notes in the knowledge base.",
        "parameters": {
            "type": "object",
            "properties": {
                "from_path": {"type": "string", "description": "Source note path"},
                "to_path": {"type": "string", "description": "Target note path"},
            },
            "required": ["from_path", "to_path"],
        },
    },
}


# --- Tool Execution ---

def execute_note_write(args: dict, context: dict) -> Any:
    """Create a new note. Persists remind_at if remind_in is set — the
    actual firing is driven by the Huey reminder_tick periodic job, not
    by a per-call daemon thread.
    """
    title = args.get("title")
    content = args.get("content")
    if not title or not content:
        return {"error": "Missing required parameter: title and content"}
    app = context["app"]
    kb = app.knowledge_base

    reminder = args.get("reminder", False)
    remind_in = args.get("remind_in")
    due = args.get("due")

    # If remind_in is set, force reminder=True and set due to today if missing
    if remind_in:
        reminder = True
        if not due:
            from datetime import date as _date
            due = _date.today().isoformat()

    path = kb.write(
        title=title,
        content=content,
        type=args.get("note_type", "note"),
        tags=args.get("tags"),
        status=args.get("status", "active"),
        due=due,
        priority=args.get("priority", 0),
        topic=args.get("topic"),
        reminder=reminder,
    )

    result = {"path": path, "status": "created"}

    # Compute remind_at = now + delta and persist it via the indexer.
    # No background thread — the Huey reminder_tick job polls the row.
    if remind_in:
        _, delay_seconds = _parse_when(remind_in)
        if delay_seconds:
            try:
                from datetime import datetime, timezone, timedelta
                remind_at = (datetime.now(timezone.utc) + timedelta(seconds=delay_seconds)).isoformat()
                kb._indexer.set_reminder(path, due=due, reminder=True, remind_at=remind_at)
                result["reminder_in"] = f"{delay_seconds // 60} min"
                result["remind_at"] = remind_at
                result["reminder_channels"] = "chat + telegram (if active)"
            except Exception as e:
                result["timer_error"] = str(e)

    return result


def execute_note_read(args: dict, context: dict) -> Any:
    """Read a note by path."""
    path = args.get("path")
    if not path:
        return {"error": "Missing required parameter: path"}
    kb = context["app"].knowledge_base
    result = kb.read(path)
    return result or {"error": f"Note not found: {path}"}


def execute_note_search(args: dict, context: dict) -> Any:
    """Search notes with optional filters."""
    kb = context["app"].knowledge_base
    status = args.get("status")
    due = args.get("due")

    # If filters are set, use list_notes with query as a post-filter
    if status or due:
        results = kb.list_notes(
            type=args.get("note_type"),
            status=status,
            due=due,
            limit=args.get("limit", 10),
        )
        query = args.get("query", "").lower()
        if query:
            results = [r for r in results
                       if query in (r.get("title", "") or "").lower()
                       or query in (r.get("tags", "") or "").lower()]
        return results

    query = args.get("query", "")
    if not query:
        return {"error": "Missing required parameter: query"}
    return kb.search(
        query,
        type=args.get("note_type"),
        limit=args.get("limit", 10),
    )


def execute_note_list(args: dict, context: dict) -> Any:
    """List notes with filters."""
    kb = context["app"].knowledge_base
    return kb.list_notes(
        type=args.get("note_type"),
        status=args.get("status"),
        due=args.get("due"),
        limit=args.get("limit", 20),
    )


def execute_note_update(args: dict, context: dict) -> Any:
    """Update a note."""
    path = args.get("path")
    if not path:
        return {"error": "Missing required parameter: path"}
    kb = context["app"].knowledge_base
    success = kb.update(
        path,
        status=args.get("status"),
        tags=args.get("tags"),
        due=args.get("due"),
        content=args.get("content"),
        append=args.get("append", False),
        priority=args.get("priority"),
    )
    return {"status": "updated" if success else "not_found"}


def execute_note_link(args: dict, context: dict) -> Any:
    """Link two notes."""
    from_path = args.get("from_path")
    to_path = args.get("to_path")
    if not from_path or not to_path:
        return {"error": "Missing required parameter: from_path and to_path"}
    kb = context["app"].knowledge_base
    kb.link(from_path, to_path)
    return {"status": "linked"}


NOTE_DONE_SCHEMA = {
    "type": "function",
    "function": {
        "name": "note_done",
        "description": "Mark a task as done.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Note/task path"},
            },
            "required": ["path"],
        },
    },
}

NOTE_REMIND_SCHEMA = {
    "type": "function",
    "function": {
        "name": "note_remind",
        "description": (
            "Set a timed reminder on a note. Supports relative times (e.g., '5m', '1h', '30min') "
            "and absolute dates ('2026-04-01', 'tomorrow'). The system will send a notification "
            "via all active channels (chat, Telegram) when the time comes."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Note path"},
                "when": {
                    "type": "string",
                    "description": (
                        "When to remind. Relative: '5m', '10min', '1h', '2h'. "
                        "Absolute: 'YYYY-MM-DD', 'tomorrow', 'today'. "
                        "For short timers, use relative format."
                    ),
                },
            },
            "required": ["path", "when"],
        },
    },
}

NOTE_MOVE_SCHEMA = {
    "type": "function",
    "function": {
        "name": "note_move",
        "description": "Move a note to a different topic.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Note path to move"},
                "topic": {"type": "string", "description": "Target topic path (e.g., 'topics/einkauf')"},
            },
            "required": ["path", "topic"],
        },
    },
}


def execute_note_done(args: dict, context: dict) -> Any:
    """Mark a task as done."""
    path = args.get("path")
    if not path:
        return {"error": "Missing required parameter: path"}
    kb = context["app"].knowledge_base
    success = kb.mark_done(path)
    return {"status": "done" if success else "not_found"}


def _parse_when(when: str) -> tuple[str, int | None]:
    """Parse a when string into (due_date, delay_seconds).

    Returns:
        (date_str, delay_seconds) — delay_seconds is set for relative times.
    """
    import re
    from datetime import date, timedelta, datetime, timezone

    when = when.strip().lower()

    # Relative: "5m", "10min", "1h", "2h", "30min"
    match = re.match(r"^(\d+)\s*(m|min|mins|minutes?|h|hr|hrs|hours?)$", when)
    if match:
        value = int(match.group(1))
        unit = match.group(2)
        if unit.startswith("h"):
            seconds = value * 3600
        else:
            seconds = value * 60
        return date.today().isoformat(), seconds

    # "tomorrow"
    if when == "tomorrow":
        return (date.today() + timedelta(days=1)).isoformat(), None

    # "today"
    if when == "today":
        return date.today().isoformat(), None

    # Absolute date: YYYY-MM-DD
    try:
        datetime.strptime(when, "%Y-%m-%d")
        return when, None
    except ValueError:
        pass

    # Fallback: treat as today
    return date.today().isoformat(), None


def execute_note_remind(args: dict, context: dict) -> Any:
    """Set a reminder. Relative durations are converted to remind_at;
    the Huey reminder_tick job handles the actual firing."""
    path = args.get("path")
    when = args.get("when")
    if not path or not when:
        return {"error": "Missing required parameter: path and when"}

    due_date, delay_seconds = _parse_when(when)
    kb = context["app"].knowledge_base

    remind_at: str | None = None
    if delay_seconds:
        from datetime import datetime, timezone, timedelta
        remind_at = (
            datetime.now(timezone.utc) + timedelta(seconds=delay_seconds)
        ).isoformat()

    success = kb.set_reminder(path, due_date, remind_at=remind_at)
    if not success:
        return {"status": "not_found"}

    if delay_seconds:
        minutes = delay_seconds // 60
        return {
            "status": "reminder_scheduled",
            "due": due_date,
            "remind_at": remind_at,
            "notify_in": f"{minutes} min",
            "channels": "chat + telegram (if active)",
        }
    return {"status": "reminder_set", "due": due_date}


def execute_note_move(args: dict, context: dict) -> Any:
    """Move a note to a topic."""
    path = args.get("path")
    topic = args.get("topic")
    if not path or not topic:
        return {"error": "Missing required parameter: path and topic"}
    kb = context["app"].knowledge_base
    success = kb.move_to_topic(path, topic)
    return {"status": "moved" if success else "not_found"}


# --- Registration ---

TOPIC_CREATE_SCHEMA = {
    "type": "function",
    "function": {
        "name": "topic_create",
        "description": "Create a new topic (folder) for organizing notes. Can create sub-topics by specifying a parent.",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Topic name (e.g., 'Projekte', 'Kaffee')"},
                "parent": {"type": "string", "description": "Parent topic path for sub-topics (e.g., 'topics/projekte'). Omit for top-level topics."},
                "tags": {"type": "array", "items": {"type": "string"}, "description": "Optional tags for the topic"},
            },
            "required": ["name"],
        },
    },
}


def execute_topic_create(args: dict, context: dict) -> Any:
    """Create a new topic or sub-topic."""
    name = args.get("name")
    if not name:
        return {"error": "Missing required parameter: name"}
    parent = args.get("parent") or None
    tags = args.get("tags") or []
    kb = context["app"].knowledge_base
    path = kb.create_topic(name, tags=tags, parent=parent)
    return {"status": "created", "path": path, "name": name}


TOPIC_LIST_SCHEMA = {
    "type": "function",
    "function": {
        "name": "topic_list",
        "description": "List all existing topics with their note counts.",
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },
}


def execute_topic_list(args: dict, context: dict) -> Any:
    """List all topics."""
    kb = context["app"].knowledge_base
    topics = kb.list_topics()
    result = []
    for t in topics:
        children = kb.list_children(t["path"])
        result.append({
            "path": t["path"],
            "title": t.get("title", ""),
            "note_count": len(children),
        })
    return {"topics": result, "count": len(result)}


# --- New Knowledge Tools (manage, read, stats) ---

TOPIC_RENAME_SCHEMA = {
    "type": "function",
    "function": {
        "name": "topic_rename",
        "description": "Rename a topic. Updates its path and all child note paths accordingly.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Current topic path (e.g., 'topics/old-name')"},
                "name": {"type": "string", "description": "New topic name"},
            },
            "required": ["path", "name"],
        },
    },
}


def execute_topic_rename(args: dict, context: dict) -> Any:
    """Rename a topic."""
    path = args.get("path")
    name = args.get("name")
    if not path or not name:
        return {"error": "Missing required parameter: path and name"}
    kb = context["app"].knowledge_base
    new_path = kb.rename_topic(path, name)
    return {"status": "renamed", "old_path": path, "new_path": new_path}


TOPIC_MERGE_SCHEMA = {
    "type": "function",
    "function": {
        "name": "topic_merge",
        "description": "Merge one topic into another. Moves all notes from source to target, then removes source.",
        "parameters": {
            "type": "object",
            "properties": {
                "source": {"type": "string", "description": "Source topic path to merge from"},
                "target": {"type": "string", "description": "Target topic path to merge into"},
            },
            "required": ["source", "target"],
        },
    },
}


def execute_topic_merge(args: dict, context: dict) -> Any:
    """Merge source topic into target."""
    source = args.get("source")
    target = args.get("target")
    if not source or not target:
        return {"error": "Missing required parameter: source and target"}
    kb = context["app"].knowledge_base
    result = kb.merge_topics(source, target)
    return {"status": "merged", "moved": result["moved"], "redirect": result["redirect"]}


TOPIC_DELETE_SCHEMA = {
    "type": "function",
    "function": {
        "name": "topic_delete",
        "description": "Delete an empty topic. Fails if the topic still contains notes or sub-topics.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Topic path to delete"},
            },
            "required": ["path"],
        },
    },
}


def execute_topic_delete(args: dict, context: dict) -> Any:
    """Delete an empty topic."""
    path = args.get("path")
    if not path:
        return {"error": "Missing required parameter: path"}
    kb = context["app"].knowledge_base
    try:
        kb.delete_topic(path)
        return {"status": "deleted", "path": path}
    except ValueError as e:
        return {"error": str(e)}


NOTE_ARCHIVE_SCHEMA = {
    "type": "function",
    "function": {
        "name": "note_archive",
        "description": "Archive a note. Sets its status to 'archived' so it no longer appears in active lists.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Note path to archive"},
            },
            "required": ["path"],
        },
    },
}


def execute_note_archive(args: dict, context: dict) -> Any:
    """Archive a note."""
    path = args.get("path")
    if not path:
        return {"error": "Missing required parameter: path"}
    kb = context["app"].knowledge_base
    success = kb.archive_note(path)
    return {"status": "archived" if success else "not_found", "path": path}


FIND_RELATED_SCHEMA = {
    "type": "function",
    "function": {
        "name": "find_related",
        "description": "Find notes related to a given note using full-text search on its title and content.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Note path to find related notes for"},
                "limit": {"type": "integer", "description": "Max number of related notes to return (default 5)"},
            },
            "required": ["path"],
        },
    },
}


def execute_find_related(args: dict, context: dict) -> Any:
    """Find notes related to a given note."""
    path = args.get("path")
    if not path:
        return {"error": "Missing required parameter: path"}
    limit = args.get("limit", 5)
    kb = context["app"].knowledge_base

    # Read note title from DB
    row = kb._app.storage.fetchone(
        "SELECT title FROM knowledge_notes WHERE path = ?", (path,)
    )
    if not row:
        return {"error": f"Note not found: {path}"}
    title = row["title"] or ""

    # Read first 200 chars of body from disk
    snippet = ""
    try:
        md_path = kb._knowledge_dir / (path + ".md")
        if md_path.exists():
            snippet = md_path.read_text(encoding="utf-8")[:200]
    except Exception:
        pass

    query = (title + " " + snippet).strip()
    if not query:
        return {"related": [], "count": 0}

    results = kb.find_relevant(query, top_k=limit + 1)
    # Filter out the source note itself
    related = [r for r in results if r.get("path") != path][:limit]
    return {"related": related, "count": len(related)}


TOPIC_OVERVIEW_SCHEMA = {
    "type": "function",
    "function": {
        "name": "topic_overview",
        "description": "Get an overview of a topic: lists all children grouped by type (notes, tasks, sub-topics) with counts.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Topic path to get overview for"},
            },
            "required": ["path"],
        },
    },
}


def execute_topic_overview(args: dict, context: dict) -> Any:
    """Get a structured overview of a topic."""
    path = args.get("path")
    if not path:
        return {"error": "Missing required parameter: path"}
    kb = context["app"].knowledge_base
    children = kb.list_children(path)

    notes = []
    tasks_open = []
    tasks_done = []
    sub_topics = []

    for child in children:
        ctype = child.get("type", "note")
        if ctype == "topic":
            sub_topics.append(child)
        elif ctype == "task":
            if child.get("status") == "done":
                tasks_done.append(child)
            else:
                tasks_open.append(child)
        else:
            notes.append(child)

    return {
        "path": path,
        "notes": notes,
        "tasks_open": tasks_open,
        "tasks_done": tasks_done,
        "sub_topics": sub_topics,
        "counts": {
            "notes": len(notes),
            "tasks_open": len(tasks_open),
            "tasks_done": len(tasks_done),
            "sub_topics": len(sub_topics),
            "total": len(children),
        },
    }


KNOWLEDGE_STATS_SCHEMA = {
    "type": "function",
    "function": {
        "name": "knowledge_stats",
        "description": "Get overall knowledge base statistics: total notes, topics, tasks (open/done), archived, and pending reminders.",
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },
}


def execute_knowledge_stats(args: dict, context: dict) -> Any:
    """Get knowledge base statistics."""
    app = context["app"]
    storage = app.storage

    total = storage.fetchone("SELECT COUNT(*) AS c FROM knowledge_notes", ())["c"]
    topics = storage.fetchone(
        "SELECT COUNT(*) AS c FROM knowledge_notes WHERE type = 'topic'", ()
    )["c"]
    tasks_open = storage.fetchone(
        "SELECT COUNT(*) AS c FROM knowledge_notes WHERE type = 'task' AND status IN ('open', 'in-progress')", ()
    )["c"]
    tasks_done = storage.fetchone(
        "SELECT COUNT(*) AS c FROM knowledge_notes WHERE type = 'task' AND status = 'done'", ()
    )["c"]
    archived = storage.fetchone(
        "SELECT COUNT(*) AS c FROM knowledge_notes WHERE status = 'archived'", ()
    )["c"]
    pending = storage.fetchone(
        "SELECT COUNT(*) AS c FROM knowledge_notes WHERE reminder = 1 AND status != 'done'", ()
    )["c"]

    return {
        "total": total,
        "topics": topics,
        "tasks_open": tasks_open,
        "tasks_done": tasks_done,
        "archived": archived,
        "pending_reminders": pending,
    }


NOTE_SPLIT_SCHEMA = {
    "type": "function",
    "function": {
        "name": "note_split",
        "description": "Split a long note into multiple focused sub-notes. The LLM analyzes the content and proposes sections.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path of the note to split"},
                "sections": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional: explicit section titles. If omitted, LLM proposes sections.",
                },
                "confirm": {
                    "type": "boolean",
                    "description": "If true, execute the split. If false or omitted, only propose sections.",
                },
            },
            "required": ["path"],
        },
    },
}

NOTE_VISION_SCHEMA = {
    "type": "function",
    "function": {
        "name": "note_vision",
        "description": "Analyze a scanned document with Vision AI. Only for document notes with source_file.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path of the document note"},
            },
            "required": ["path"],
        },
    },
}


def _propose_sections(app: Any, title: str, content: str) -> list[dict]:
    """Ask Haiku to split note content into 2-5 sections.

    Returns a list of dicts with 'title' and 'content' keys.
    """
    import json

    response = app.llm.complete(
        [
            {
                "role": "system",
                "content": (
                    "You are a note organizer. Analyze the given note and identify 2-5 distinct "
                    "sections. Respond as JSON only: {\"sections\": [{\"title\": \"...\", \"content\": \"...\"}]}"
                ),
            },
            {
                "role": "user",
                "content": f"Note title: {title}\n\nContent:\n{content}",
            },
        ],
        model="claude-haiku-4-5",
    )
    raw = response.content.strip()
    # Strip code fences if present
    if raw.startswith("```"):
        raw = raw.strip("`").strip()
        if raw.lower().startswith("json"):
            raw = raw[4:].strip()
    data = json.loads(raw)
    return data.get("sections", [])


def _assign_to_sections(app: Any, content: str, section_titles: list[str]) -> list[dict]:
    """Ask Haiku to assign note content to explicit section titles.

    Returns a list of dicts with 'title' and 'content' keys.
    """
    import json

    titles_str = "\n".join(f"- {t}" for t in section_titles)
    response = app.llm.complete(
        [
            {
                "role": "system",
                "content": (
                    "You are a note organizer. Distribute the given note content into the provided "
                    "sections. Respond as JSON only: {\"sections\": [{\"title\": \"...\", \"content\": \"...\"}]}"
                ),
            },
            {
                "role": "user",
                "content": f"Sections:\n{titles_str}\n\nContent:\n{content}",
            },
        ],
        model="claude-haiku-4-5",
    )
    raw = response.content.strip()
    if raw.startswith("```"):
        raw = raw.strip("`").strip()
        if raw.lower().startswith("json"):
            raw = raw[4:].strip()
    data = json.loads(raw)
    return data.get("sections", [])


def execute_note_split(args: dict, context: dict) -> Any:
    """Split a long note into multiple focused sub-notes."""
    path = args.get("path")
    if not path:
        return {"status": "error", "message": "Missing required parameter: path"}

    app = context["app"]
    kb = app.knowledge_base

    from mycelos.knowledge.note import parse_frontmatter

    md_path = kb._knowledge_dir / (path + ".md")
    if not md_path.exists():
        return {"status": "error", "message": f"Note not found: {path}"}

    raw = md_path.read_text(encoding="utf-8")
    note = parse_frontmatter(raw)
    title = note.title or path.split("/")[-1]
    content = note.content

    explicit_sections = args.get("sections")
    confirm = args.get("confirm", False)

    try:
        if explicit_sections:
            sections = _assign_to_sections(app, content, explicit_sections)
        else:
            sections = _propose_sections(app, title, content)
    except Exception as e:
        return {"status": "error", "message": f"LLM call failed: {e}"}

    if not confirm:
        return {
            "status": "proposed",
            "sections": [s.get("title", "") for s in sections],
            "message": (
                f"Found {len(sections)} sections. Call again with confirm=true to execute the split."
            ),
        }

    # Execute the split: create child notes
    parent_path = "/".join(path.split("/")[:-1]) if "/" in path else path.split("/")[0]
    child_paths = []
    for section in sections:
        child_title = section.get("title", "Untitled")
        child_content = section.get("content", "")
        child_path = kb.write(
            title=child_title,
            content=child_content,
            type=note.type,
            tags=note.tags,
            topic=parent_path if parent_path else None,
        )
        child_paths.append(child_path)

    # Convert original note to index note
    children_list = "\n".join(f"- [[{p}]]" for p in child_paths)
    index_content = f"Split into:\n{children_list}"
    kb.update(path, content=index_content)

    app.audit.log("knowledge.note.split", details={
        "path": path,
        "children": child_paths,
        "section_count": len(sections),
    })

    return {
        "status": "split",
        "children": child_paths,
        "index": path,
    }


def execute_note_vision(args: dict, context: dict) -> Any:
    """Analyze a scanned document note with Vision AI."""
    from mycelos.knowledge.ingest import vision_analyze
    return vision_analyze(context["app"], args["path"])


def register(registry: type) -> None:
    """Register all knowledge tools."""
    registry.register("note_write", NOTE_WRITE_SCHEMA, execute_note_write, ToolPermission.STANDARD, category="knowledge_write")
    registry.register("note_read", NOTE_READ_SCHEMA, execute_note_read, ToolPermission.OPEN, concurrent_safe=True, category="knowledge_read")
    registry.register("note_search", NOTE_SEARCH_SCHEMA, execute_note_search, ToolPermission.OPEN, concurrent_safe=True, category="knowledge_read")
    registry.register("note_list", NOTE_LIST_SCHEMA, execute_note_list, ToolPermission.OPEN, concurrent_safe=True, category="knowledge_read")
    registry.register("note_update", NOTE_UPDATE_SCHEMA, execute_note_update, ToolPermission.STANDARD, category="knowledge_write")
    registry.register("note_link", NOTE_LINK_SCHEMA, execute_note_link, ToolPermission.STANDARD, category="knowledge_write")
    registry.register("note_done", NOTE_DONE_SCHEMA, execute_note_done, ToolPermission.STANDARD, category="knowledge_write")
    registry.register("note_remind", NOTE_REMIND_SCHEMA, execute_note_remind, ToolPermission.STANDARD, category="knowledge_write")
    registry.register("note_move", NOTE_MOVE_SCHEMA, execute_note_move, ToolPermission.STANDARD, category="knowledge_write")
    registry.register("topic_create", TOPIC_CREATE_SCHEMA, execute_topic_create, ToolPermission.STANDARD, category="knowledge_manage")
    registry.register("topic_list", TOPIC_LIST_SCHEMA, execute_topic_list, ToolPermission.OPEN, concurrent_safe=True, category="knowledge_read")
    registry.register("topic_rename", TOPIC_RENAME_SCHEMA, execute_topic_rename, ToolPermission.STANDARD, category="knowledge_manage")
    registry.register("topic_merge", TOPIC_MERGE_SCHEMA, execute_topic_merge, ToolPermission.STANDARD, category="knowledge_manage")
    registry.register("topic_delete", TOPIC_DELETE_SCHEMA, execute_topic_delete, ToolPermission.STANDARD, category="knowledge_manage")
    registry.register("note_archive", NOTE_ARCHIVE_SCHEMA, execute_note_archive, ToolPermission.STANDARD, category="knowledge_manage")
    registry.register("find_related", FIND_RELATED_SCHEMA, execute_find_related, ToolPermission.OPEN, concurrent_safe=True, category="knowledge_read")
    registry.register("topic_overview", TOPIC_OVERVIEW_SCHEMA, execute_topic_overview, ToolPermission.OPEN, concurrent_safe=True, category="knowledge_read")
    registry.register("knowledge_stats", KNOWLEDGE_STATS_SCHEMA, execute_knowledge_stats, ToolPermission.OPEN, concurrent_safe=True, category="knowledge_manage")
    registry.register("note_split", NOTE_SPLIT_SCHEMA, execute_note_split, ToolPermission.STANDARD, category="knowledge_manage")
    registry.register("note_vision", NOTE_VISION_SCHEMA, execute_note_vision, ToolPermission.STANDARD, category="knowledge_manage")
