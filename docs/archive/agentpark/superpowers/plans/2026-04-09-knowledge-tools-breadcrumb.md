# Knowledge Tools + Breadcrumb Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add breadcrumb navigation on the note detail view, implement 7 new knowledge tools (topic_rename, topic_merge, topic_delete, note_archive, find_related, topic_overview, knowledge_stats), extend the organizer lifecycle with a 30-day hard-delete for archived notes, and update the Discovery category registry.

**Architecture:** New `KnowledgeBase` methods for rename/merge/delete/archive. New tool schemas + execute functions in `tools/knowledge.py`. Breadcrumb is a pure frontend change on `knowledge.html`. The organizer handler gets a new lifecycle rule for hard-deleting old archived notes.

**Tech Stack:** Python 3.12, SQLite, Alpine.js, existing ToolRegistry + KnowledgeBase + Organizer.

**Spec reference:** `docs/superpowers/specs/2026-04-09-knowledge-tools-breadcrumb-design.md`

---

## File Structure

### Files created
- `tests/test_knowledge_tools.py` — unit tests for the new KnowledgeBase methods (rename, merge, delete, archive)
- `tests/test_knowledge_tools_api.py` — API/tool tests for the new tools

### Files modified
- `src/mycelos/knowledge/service.py` — add `rename_topic()`, `merge_topics()`, `delete_topic()`, `archive_note()` methods
- `src/mycelos/tools/knowledge.py` — add 7 new tool schemas + execute functions + register calls
- `src/mycelos/tools/categories.py` — update `TOOL_CATEGORIES` with `find_related` and `topic_overview` in `knowledge_read`; verify `knowledge_manage` list matches
- `src/mycelos/knowledge/organizer.py` — add `is_archived_older_than()` predicate
- `src/mycelos/agents/handlers/knowledge_organizer_handler.py` — add hard-delete lifecycle rule
- `src/mycelos/frontend/pages/knowledge.html` — add breadcrumb to the note detail header
- `CHANGELOG.md`

---

## Task 1: Breadcrumb on Note Detail View

**Files:**
- Modify: `src/mycelos/frontend/pages/knowledge.html`

- [ ] **Step 1: Find the note title header**

Open `src/mycelos/frontend/pages/knowledge.html`. Find the `<h1>` that shows `selectedNote.title` (around line 753). The surrounding block is:

```html
<template x-if="!creatingNew && selectedNote">
  <h1 class="text-lg font-headline font-bold text-on-surface truncate"
      x-text="selectedNote.title || 'Untitled'"></h1>
</template>
```

- [ ] **Step 2: Insert breadcrumb above the title**

Replace that `<template x-if="!creatingNew && selectedNote">` block with:

```html
<template x-if="!creatingNew && selectedNote">
  <div>
    <div x-show="selectedNote.parent_path"
         class="flex items-center gap-1 text-[11px] mb-0.5 flex-wrap"
         style="color: var(--on-surface-variant)">
      <template x-for="(seg, i) in buildBreadcrumb(selectedNote)" :key="i">
        <span class="flex items-center gap-1">
          <span x-show="i > 0" class="opacity-40">/</span>
          <button @click="navigateToBreadcrumb(seg.path)"
                  class="hover:text-[var(--primary)] transition-colors truncate max-w-[120px]"
                  x-text="seg.label"></button>
        </span>
      </template>
    </div>
    <h1 class="text-lg font-headline font-bold text-on-surface truncate"
        x-text="selectedNote.title || 'Untitled'"></h1>
  </div>
</template>
```

- [ ] **Step 3: Add `buildBreadcrumb` and `navigateToBreadcrumb` methods**

Find the `knowledgeApp()` function in the same file (around line 933). Add these methods inside the returned object, near the other navigation methods:

```javascript
buildBreadcrumb(note) {
  if (!note || !note.parent_path) return [];
  const parts = note.parent_path.split('/');
  const segments = [];
  let cumulative = '';
  for (const part of parts) {
    cumulative = cumulative ? cumulative + '/' + part : part;
    segments.push({
      path: cumulative,
      label: part.charAt(0).toUpperCase() + part.slice(1).replace(/-/g, ' '),
    });
  }
  return segments;
},
navigateToBreadcrumb(topicPath) {
  this.viewMode = 'topics';
  this.loadTopics();
  this.$nextTick(() => this.toggleTopic({ path: topicPath }));
},
```

- [ ] **Step 4: Verify in browser**

Load the Knowledge page, select a note that has a `parent_path`. The breadcrumb should appear above the title. Clicking a segment should expand that topic in the left panel.

- [ ] **Step 5: Commit**

```bash
git add src/mycelos/frontend/pages/knowledge.html
git commit -m "feat(knowledge): breadcrumb path on note detail view"
```

---

## Task 2: KnowledgeBase CRUD methods (rename, merge, delete, archive)

**Files:**
- Modify: `src/mycelos/knowledge/service.py`
- Test: `tests/test_knowledge_tools.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_knowledge_tools.py`:

```python
"""Unit tests for new KnowledgeBase methods: rename, merge, delete, archive."""
from __future__ import annotations

import pytest


@pytest.mark.integration
def test_rename_topic(integration_app) -> None:
    kb = integration_app.knowledge_base
    old_path = kb.create_topic("Kaffee")
    note_path = kb.write(title="Espresso", content="18g in", topic=old_path)

    new_path = kb.rename_topic(old_path, "Getraenke")

    assert new_path != old_path
    assert "getraenke" in new_path
    # The topic note should exist at the new path
    topic_row = integration_app.storage.fetchone(
        "SELECT path, title FROM knowledge_notes WHERE path=?", (new_path,)
    )
    assert topic_row is not None
    assert topic_row["title"] == "Getraenke"
    # The old path should be gone
    old_row = integration_app.storage.fetchone(
        "SELECT path FROM knowledge_notes WHERE path=?", (old_path,)
    )
    assert old_row is None
    # Children should have updated parent_path
    child = integration_app.storage.fetchone(
        "SELECT parent_path FROM knowledge_notes WHERE path=?", (note_path,)
    )
    assert child["parent_path"] == new_path


@pytest.mark.integration
def test_merge_topics(integration_app) -> None:
    kb = integration_app.knowledge_base
    source = kb.create_topic("Kaffee")
    target = kb.create_topic("Getraenke")
    note1 = kb.write(title="Espresso", content="body", topic=source)
    note2 = kb.write(title="Latte", content="body", topic=source)

    result = kb.merge_topics(source, target)

    assert result["moved"] == 2
    # Children should now be under target
    child1 = integration_app.storage.fetchone(
        "SELECT parent_path FROM knowledge_notes WHERE path=?", (note1,)
    )
    assert child1["parent_path"] == target
    # Source topic should still exist as a redirect
    source_note = integration_app.storage.fetchone(
        "SELECT title, content FROM knowledge_notes WHERE path=?", (source,)
    )
    assert source_note is not None
    redirect_file = kb._knowledge_dir / (source + ".md")
    assert "[[" in redirect_file.read_text()  # contains a wikilink to target


@pytest.mark.integration
def test_delete_empty_topic(integration_app) -> None:
    kb = integration_app.knowledge_base
    path = kb.create_topic("Empty")
    assert kb.delete_topic(path) is True
    row = integration_app.storage.fetchone(
        "SELECT path FROM knowledge_notes WHERE path=?", (path,)
    )
    assert row is None


@pytest.mark.integration
def test_delete_nonempty_topic_fails(integration_app) -> None:
    kb = integration_app.knowledge_base
    path = kb.create_topic("Full")
    kb.write(title="Child", content="body", topic=path)
    with pytest.raises(ValueError, match="children"):
        kb.delete_topic(path)


@pytest.mark.integration
def test_archive_note(integration_app) -> None:
    kb = integration_app.knowledge_base
    path = kb.write(title="Old idea", content="body")
    kb.archive_note(path)
    row = integration_app.storage.fetchone(
        "SELECT status, organizer_state FROM knowledge_notes WHERE path=?", (path,)
    )
    assert row["status"] == "archived"
    assert row["organizer_state"] == "archived"
```

- [ ] **Step 2: Run — expect ImportError/AttributeError**

Run: `pytest tests/test_knowledge_tools.py -v -m integration`

- [ ] **Step 3: Implement the four methods on KnowledgeBase**

Add to `src/mycelos/knowledge/service.py`, after the existing `move_to_topic` method (around line 495):

```python
def rename_topic(self, path: str, new_name: str) -> str:
    """Rename a topic: update title, path, children, and on-disk files.

    Returns the new topic path.
    """
    import re
    slug = re.sub(r"[^a-z0-9]+", "-", new_name.lower()).strip("-")
    # Compute new path: keep parent prefix, replace last segment
    parts = path.rsplit("/", 1)
    new_path = f"{parts[0]}/{slug}" if len(parts) > 1 else f"topics/{slug}"

    # Rename the topic note itself
    old_file = self._safe_path(path)
    new_file = self._knowledge_dir / (new_path + ".md")
    new_file.parent.mkdir(parents=True, exist_ok=True)

    if old_file.exists():
        content = old_file.read_text(encoding="utf-8")
        # Update the title in frontmatter/content
        content = content.replace(f"title: {path.rsplit('/', 1)[-1]}", f"title: {new_name}", 1)
        new_file.write_text(content, encoding="utf-8")
        if old_file != new_file:
            old_file.unlink()

    # Update DB: the topic note itself
    self._app.storage.execute(
        "UPDATE knowledge_notes SET path=?, title=? WHERE path=?",
        (new_path, new_name, path),
    )
    # Update DB: all children's parent_path
    self._app.storage.execute(
        "UPDATE knowledge_notes SET parent_path=? WHERE parent_path=?",
        (new_path, path),
    )
    # Rename children's files whose paths start with the old topic path
    children = self.list_children(new_path)
    for child in children:
        old_child_path = child["path"]
        if old_child_path.startswith(path + "/"):
            new_child_path = new_path + old_child_path[len(path):]
            old_child_file = self._knowledge_dir / (old_child_path + ".md")
            new_child_file = self._knowledge_dir / (new_child_path + ".md")
            if old_child_file.exists() and old_child_file != new_child_file:
                new_child_file.parent.mkdir(parents=True, exist_ok=True)
                old_child_file.rename(new_child_file)
            self._app.storage.execute(
                "UPDATE knowledge_notes SET path=? WHERE path=?",
                (new_child_path, old_child_path),
            )

    return new_path

def merge_topics(self, source: str, target: str) -> dict:
    """Soft-merge: move all children from source to target, make source a redirect.

    Returns: {"moved": int, "redirect": str}
    """
    children = self.list_children(source)
    moved = 0
    for child in children:
        self.move_to_topic(child["path"], target)
        moved += 1

    # Turn the source topic into a redirect note
    source_file = self._safe_path(source)
    source_row = self._app.storage.fetchone(
        "SELECT title FROM knowledge_notes WHERE path=?", (source,)
    )
    old_title = source_row["title"] if source_row else source.rsplit("/", 1)[-1]
    redirect_content = f"# {old_title}\n\n> Moved to [[{target}]]\n"
    if source_file.exists():
        from mycelos.knowledge.note import Note, render_note, parse_frontmatter
        md = source_file.read_text(encoding="utf-8")
        note = parse_frontmatter(md)
        note.content = redirect_content
        source_file.write_text(render_note(note), encoding="utf-8")

    return {"moved": moved, "redirect": source}

def delete_topic(self, path: str) -> bool:
    """Delete an empty topic. Raises ValueError if it has children."""
    children = self.list_children(path)
    if children:
        raise ValueError(f"Cannot delete topic '{path}': has {len(children)} children")
    # Delete file
    file_path = self._safe_path(path)
    if file_path.exists():
        file_path.unlink()
    # Delete DB row
    self._app.storage.execute(
        "DELETE FROM knowledge_notes WHERE path=?", (path,)
    )
    return True

def archive_note(self, path: str) -> bool:
    """Archive a note: set status='archived', hide from all views."""
    from datetime import datetime, timezone
    now = datetime.now(tz=timezone.utc).isoformat()
    self._app.storage.execute(
        "UPDATE knowledge_notes SET status='archived', organizer_state='archived', "
        "organizer_seen_at=? WHERE path=?",
        (now, path),
    )
    return True
```

**Before implementing:** read `src/mycelos/knowledge/note.py` to confirm that `parse_frontmatter` and `render_note` are importable from there. If not, find where they live and adjust the import.

- [ ] **Step 4: Run — expect pass**

Run: `pytest tests/test_knowledge_tools.py -v -m integration`

- [ ] **Step 5: Commit**

```bash
git add src/mycelos/knowledge/service.py tests/test_knowledge_tools.py
git commit -m "feat(knowledge): rename_topic, merge_topics, delete_topic, archive_note"
```

---

## Task 3: New tool schemas + execute functions

**Files:**
- Modify: `src/mycelos/tools/knowledge.py`
- Test: `tests/test_knowledge_tools_api.py`

- [ ] **Step 1: Add the 7 new tool schemas and execute functions**

Open `src/mycelos/tools/knowledge.py`. Before the `register()` function (around line 527), add:

```python
# --- Topic Rename ---

TOPIC_RENAME_SCHEMA = {
    "type": "function",
    "function": {
        "name": "topic_rename",
        "description": "Rename a topic. Updates the topic title, path, and all children.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Current topic path (e.g., 'topics/kaffee')"},
                "name": {"type": "string", "description": "New topic name"},
            },
            "required": ["path", "name"],
        },
    },
}

def execute_topic_rename(args: dict, context: dict) -> Any:
    path = args.get("path")
    name = args.get("name")
    if not path or not name:
        return {"error": "Missing required parameters: path and name"}
    kb = context["app"].knowledge_base
    try:
        new_path = kb.rename_topic(path, name)
        return {"status": "renamed", "old_path": path, "new_path": new_path}
    except Exception as e:
        return {"error": str(e)}


# --- Topic Merge ---

TOPIC_MERGE_SCHEMA = {
    "type": "function",
    "function": {
        "name": "topic_merge",
        "description": "Merge two topics: move all notes from source to target. Source becomes a redirect.",
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
    source = args.get("source")
    target = args.get("target")
    if not source or not target:
        return {"error": "Missing required parameters: source and target"}
    kb = context["app"].knowledge_base
    try:
        result = kb.merge_topics(source, target)
        return {"status": "merged", **result}
    except Exception as e:
        return {"error": str(e)}


# --- Topic Delete ---

TOPIC_DELETE_SCHEMA = {
    "type": "function",
    "function": {
        "name": "topic_delete",
        "description": "Delete an empty topic. Refuses if the topic still has notes — move or delete them first.",
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
    path = args.get("path")
    if not path:
        return {"error": "Missing required parameter: path"}
    kb = context["app"].knowledge_base
    try:
        kb.delete_topic(path)
        return {"status": "deleted", "path": path}
    except ValueError as e:
        return {"error": str(e)}


# --- Note Archive ---

NOTE_ARCHIVE_SCHEMA = {
    "type": "function",
    "function": {
        "name": "note_archive",
        "description": "Archive a note. It disappears from all views but stays recoverable for 30 days.",
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
    path = args.get("path")
    if not path:
        return {"error": "Missing required parameter: path"}
    kb = context["app"].knowledge_base
    kb.archive_note(path)
    return {"status": "archived", "path": path}


# --- Find Related ---

FIND_RELATED_SCHEMA = {
    "type": "function",
    "function": {
        "name": "find_related",
        "description": "Find notes semantically similar to a given note. Uses full-text search.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path of the note to find related notes for"},
                "limit": {"type": "integer", "description": "Max results (default 5)"},
            },
            "required": ["path"],
        },
    },
}

def execute_find_related(args: dict, context: dict) -> Any:
    path = args.get("path")
    if not path:
        return {"error": "Missing required parameter: path"}
    limit = args.get("limit", 5)
    kb = context["app"].knowledge_base
    # Read the note title from DB
    row = kb._app.storage.fetchone(
        "SELECT title FROM knowledge_notes WHERE path=?", (path,)
    )
    if not row:
        return {"error": f"Note not found: {path}"}
    # Read body from disk
    file_path = kb._knowledge_dir / (path + ".md")
    body = ""
    if file_path.exists():
        body = file_path.read_text(encoding="utf-8")[:200]
    query = f"{row['title']} {body}".strip()
    results = kb.find_relevant(query, top_k=limit + 1)
    # Filter out the source note
    related = [r for r in results if r.get("path") != path][:limit]
    return {"related": related, "count": len(related)}


# --- Topic Overview ---

TOPIC_OVERVIEW_SCHEMA = {
    "type": "function",
    "function": {
        "name": "topic_overview",
        "description": "Get a structured overview of a topic: children, counts, sub-topics.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Topic path"},
            },
            "required": ["path"],
        },
    },
}

def execute_topic_overview(args: dict, context: dict) -> Any:
    path = args.get("path")
    if not path:
        return {"error": "Missing required parameter: path"}
    kb = context["app"].knowledge_base
    children = kb.list_children(path)
    notes = [c for c in children if c.get("type") != "topic"]
    sub_topics = [c for c in children if c.get("type") == "topic"]
    open_tasks = [c for c in notes if c.get("type") == "task" and c.get("status") in ("active", "open", "in-progress")]
    done_tasks = [c for c in notes if c.get("type") == "task" and c.get("status") == "done"]
    return {
        "path": path,
        "children": [{"path": c["path"], "title": c.get("title", ""), "type": c.get("type", "note")} for c in children],
        "sub_topics": [{"path": s["path"], "title": s.get("title", "")} for s in sub_topics],
        "total": len(children),
        "open_tasks": len(open_tasks),
        "done_tasks": len(done_tasks),
        "notes": len(notes) - len(open_tasks) - len(done_tasks),
    }


# --- Knowledge Stats ---

KNOWLEDGE_STATS_SCHEMA = {
    "type": "function",
    "function": {
        "name": "knowledge_stats",
        "description": "Get knowledge base statistics: note counts, topic counts, task status.",
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },
}

def execute_knowledge_stats(args: dict, context: dict) -> Any:
    storage = context["app"].storage
    total = storage.fetchone("SELECT COUNT(*) AS c FROM knowledge_notes WHERE status != 'archived'")
    topics = storage.fetchone("SELECT COUNT(*) AS c FROM knowledge_notes WHERE type='topic' AND status != 'archived'")
    tasks_open = storage.fetchone("SELECT COUNT(*) AS c FROM knowledge_notes WHERE type='task' AND status IN ('active','open','in-progress')")
    tasks_done = storage.fetchone("SELECT COUNT(*) AS c FROM knowledge_notes WHERE type='task' AND status='done'")
    archived = storage.fetchone("SELECT COUNT(*) AS c FROM knowledge_notes WHERE status='archived'")
    pending = storage.fetchone("SELECT COUNT(*) AS c FROM knowledge_notes WHERE organizer_state='pending'")
    return {
        "total_notes": total["c"] if total else 0,
        "topics": topics["c"] if topics else 0,
        "tasks_open": tasks_open["c"] if tasks_open else 0,
        "tasks_done": tasks_done["c"] if tasks_done else 0,
        "archived": archived["c"] if archived else 0,
        "organizer_pending": pending["c"] if pending else 0,
    }
```

- [ ] **Step 2: Register all new tools**

In the `register()` function at the bottom of `tools/knowledge.py`, append:

```python
    registry.register("topic_rename", TOPIC_RENAME_SCHEMA, execute_topic_rename, ToolPermission.STANDARD, category="knowledge_manage")
    registry.register("topic_merge", TOPIC_MERGE_SCHEMA, execute_topic_merge, ToolPermission.STANDARD, category="knowledge_manage")
    registry.register("topic_delete", TOPIC_DELETE_SCHEMA, execute_topic_delete, ToolPermission.STANDARD, category="knowledge_manage")
    registry.register("note_archive", NOTE_ARCHIVE_SCHEMA, execute_note_archive, ToolPermission.STANDARD, category="knowledge_manage")
    registry.register("find_related", FIND_RELATED_SCHEMA, execute_find_related, ToolPermission.OPEN, concurrent_safe=True, category="knowledge_read")
    registry.register("topic_overview", TOPIC_OVERVIEW_SCHEMA, execute_topic_overview, ToolPermission.OPEN, concurrent_safe=True, category="knowledge_read")
    registry.register("knowledge_stats", KNOWLEDGE_STATS_SCHEMA, execute_knowledge_stats, ToolPermission.OPEN, concurrent_safe=True, category="knowledge_manage")
```

- [ ] **Step 3: Run the category test to verify registration**

Run: `pytest tests/test_tool_categories.py::test_every_registered_tool_has_a_category -v`

- [ ] **Step 4: Commit**

```bash
git add src/mycelos/tools/knowledge.py
git commit -m "feat(tools): 7 new knowledge tools (rename, merge, delete, archive, find_related, overview, stats)"
```

---

## Task 4: Update Discovery categories

**Files:**
- Modify: `src/mycelos/tools/categories.py`

- [ ] **Step 1: Update TOOL_CATEGORIES**

Open `src/mycelos/tools/categories.py`. Update `knowledge_read` to include the new tools:

```python
"knowledge_read": [
    "note_search",
    "note_read",
    "note_list",
    "topic_list",
    "find_related",
    "topic_overview",
],
```

Verify `knowledge_manage` already lists `topic_rename`, `topic_merge`, `note_archive`, `note_delete`, `knowledge_stats`. Remove `note_delete` since we're not implementing it as a separate tool (archive is the user-facing delete). Add `topic_delete` if missing.

The final `knowledge_manage` should be:
```python
"knowledge_manage": [
    "topic_create",
    "topic_rename",
    "topic_merge",
    "topic_delete",
    "note_archive",
    "knowledge_stats",
],
```

- [ ] **Step 2: Run category tests**

Run: `pytest tests/test_tool_categories.py -v`

- [ ] **Step 3: Commit**

```bash
git add src/mycelos/tools/categories.py
git commit -m "feat(tools): update discovery categories with new knowledge tools"
```

---

## Task 5: 30-day hard-delete lifecycle rule

**Files:**
- Modify: `src/mycelos/knowledge/organizer.py`
- Modify: `src/mycelos/agents/handlers/knowledge_organizer_handler.py`
- Test: `tests/test_organizer_lifecycle.py` (extend)

- [ ] **Step 1: Add `is_archived_older_than` predicate**

Append to `src/mycelos/knowledge/organizer.py`:

```python
def is_archived_older_than(note: dict, *, days: int) -> bool:
    """True if the note is archived and the archive timestamp is older than `days`."""
    if note.get("status") != "archived":
        return False
    # Use organizer_seen_at (set when archived), fall back to updated_at
    ts = _parse_iso(note.get("organizer_seen_at") or note.get("updated_at"))
    if not ts:
        return False
    now = datetime.now(tz=timezone.utc)
    return (now - ts) >= timedelta(days=days)
```

- [ ] **Step 2: Write the failing test**

Append to `tests/test_organizer_lifecycle.py`:

```python
from mycelos.knowledge.organizer import is_archived_older_than

@freezegun.freeze_time("2026-05-15T12:00:00Z")
def test_archived_older_than_30_days() -> None:
    note = {"status": "archived", "organizer_seen_at": "2026-04-10T10:00:00Z"}
    assert is_archived_older_than(note, days=30) is True

@freezegun.freeze_time("2026-05-15T12:00:00Z")
def test_archived_under_30_days() -> None:
    note = {"status": "archived", "organizer_seen_at": "2026-05-01T10:00:00Z"}
    assert is_archived_older_than(note, days=30) is False

@freezegun.freeze_time("2026-05-15T12:00:00Z")
def test_active_note_never_hard_deleted() -> None:
    note = {"status": "active", "organizer_seen_at": "2026-01-01T00:00:00Z"}
    assert is_archived_older_than(note, days=30) is False
```

- [ ] **Step 3: Run — verify the new tests pass**

Run: `pytest tests/test_organizer_lifecycle.py -v`

- [ ] **Step 4: Add hard-delete to the organizer handler**

In `src/mycelos/agents/handlers/knowledge_organizer_handler.py`, add the import:

```python
from mycelos.knowledge.organizer import is_archived_older_than
```

In the `run()` method, add a new sweep BEFORE the existing pending-note loop. After the dedup housekeeping and before `pending = storage.fetchall(...)`:

```python
        # Hard-delete archived notes older than 30 days
        archived_notes = storage.fetchall(
            "SELECT * FROM knowledge_notes WHERE status='archived' LIMIT 50"
        )
        hard_deleted = 0
        for note in archived_notes:
            if is_archived_older_than(note, days=30):
                path = note["path"]
                # Delete file from disk
                file_path = kb._knowledge_dir / (path + ".md")
                if file_path.exists():
                    file_path.unlink()
                # Delete from DB
                storage.execute("DELETE FROM knowledge_notes WHERE path=?", (path,))
                storage.execute("DELETE FROM organizer_suggestions WHERE note_path=?", (path,))
                self._audit(user_id, "organizer.hard_delete", {"path": path})
                hard_deleted += 1
```

Add `"hard_deleted": hard_deleted` to the return dict.

- [ ] **Step 5: Commit**

```bash
git add src/mycelos/knowledge/organizer.py src/mycelos/agents/handlers/knowledge_organizer_handler.py tests/test_organizer_lifecycle.py
git commit -m "feat(organizer): hard-delete archived notes after 30 days"
```

---

## Task 6: CHANGELOG + final verification

- [ ] **Step 1: Add changelog**

Under `## Week 15 (2026)` in `CHANGELOG.md`:

```markdown
### Knowledge Tools + Breadcrumb
- Breadcrumb path on note detail view — clickable segments navigate to the parent topic
- `topic_rename` tool — renames topic + updates children paths + on-disk files
- `topic_merge` tool — soft-merge with redirect note (no wikilink rewriting)
- `topic_delete` tool — deletes empty topics (refuses if children exist)
- `note_archive` tool — archives note (hidden from views, auto-deleted after 30 days)
- `find_related` tool — finds semantically similar notes via FTS5
- `topic_overview` tool — structured topic summary with child counts
- `knowledge_stats` tool — knowledge base statistics (notes, topics, tasks, archived)
- Organizer lifecycle: archived > 30 days → hard delete (DB + disk)
- All new tools registered in Lazy Tool Discovery categories
```

- [ ] **Step 2: Run the verification suite**

```bash
pytest \
  tests/test_organizer_lifecycle.py \
  tests/test_tool_categories.py \
  tests/test_tool_discovery.py \
  tests/test_knowledge_base.py \
  tests/test_storage.py \
  -v
```

- [ ] **Step 3: Commit**

```bash
git add CHANGELOG.md
git commit -m "docs(changelog): Knowledge Tools + Breadcrumb"
```
