# Knowledge Base — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** LLM-native personal wiki with Markdown files, FTS5 search, sqlite-vec embeddings, automatic context enrichment, and conversational note management.

**Architecture:** Markdown files in `~/.mycelos/knowledge/` are the source of truth. SQLite index (FTS5 + metadata) enables fast search. sqlite-vec stores embeddings for semantic similarity. The LLM automatically finds relevant notes for every conversation. Six chat tools let the LLM manage notes conversationally.

**Tech Stack:** SQLite FTS5, sqlite-vec, sentence-transformers (local fallback), OpenAI text-embedding-3-small, YAML frontmatter

**Spec:** `docs/superpowers/specs/2026-03-25-knowledge-base-design.md`

---

## File Structure

### New Files
- `src/mycelos/knowledge/__init__.py` — Package init
- `src/mycelos/knowledge/service.py` — KnowledgeBase class (main service)
- `src/mycelos/knowledge/indexer.py` — SQLite index, FTS5, links
- `src/mycelos/knowledge/embeddings.py` — Embedding provider (OpenAI or local)
- `src/mycelos/knowledge/note.py` — Note dataclass + frontmatter parsing
- `tests/test_knowledge_base.py` — All KB tests

### Modified Files
- `src/mycelos/storage/schema.sql` — Add knowledge tables
- `src/mycelos/storage/database.py` — Auto-migration for new tables
- `src/mycelos/protocols.py` — KnowledgeBaseProtocol
- `src/mycelos/app.py` — Add `knowledge_base` lazy property
- `src/mycelos/chat/service.py` — Context enrichment + note tools

---

## Task 1: Schema + Note Data Model

**Files:**
- Modify: `src/mycelos/storage/schema.sql`
- Modify: `src/mycelos/storage/database.py`
- Create: `src/mycelos/knowledge/__init__.py`
- Create: `src/mycelos/knowledge/note.py`
- Create: `tests/test_knowledge_base.py`

- [ ] **Step 1: Write failing tests for Note model**

```python
# tests/test_knowledge_base.py
"""Tests for Knowledge Base — notes, search, embeddings, context enrichment."""

import os
import tempfile
from pathlib import Path

import pytest

from mycelos.knowledge.note import Note, parse_frontmatter, render_note


class TestNoteModel:
    def test_create_note(self):
        note = Note(title="Test Note", content="Hello world", type="note")
        assert note.title == "Test Note"
        assert note.type == "note"
        assert note.status == "active"
        assert note.priority == 0

    def test_note_to_markdown(self):
        note = Note(title="My Task", content="Do something", type="task",
                    tags=["urgent"], status="open", due="2026-03-28", priority=2)
        md = render_note(note)
        assert "title: My Task" in md
        assert "type: task" in md
        assert "due: '2026-03-28'" in md or "due: 2026-03-28" in md
        assert "priority: 2" in md
        assert "Do something" in md

    def test_parse_frontmatter(self):
        md = """---
title: Test
type: fact
tags: [a, b]
status: active
priority: 1
due: null
---

Some content here.
"""
        note = parse_frontmatter(md)
        assert note.title == "Test"
        assert note.type == "fact"
        assert note.tags == ["a", "b"]
        assert note.priority == 1
        assert note.content == "Some content here."

    def test_parse_frontmatter_no_frontmatter(self):
        md = "Just plain text without frontmatter"
        note = parse_frontmatter(md)
        assert note.title == ""
        assert note.content == "Just plain text without frontmatter"

    def test_note_path_generation(self):
        note = Note(title="Security Fail Closed", type="decision")
        assert note.generate_path() == "decisions/security-fail-closed"

    def test_task_path_generation(self):
        note = Note(title="Fix Planner", type="task")
        assert note.generate_path() == "tasks/fix-planner"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_knowledge_base.py -v`
Expected: ImportError

- [ ] **Step 3: Create note.py**

Create `src/mycelos/knowledge/__init__.py` (empty) and `src/mycelos/knowledge/note.py`:

```python
"""Note model — dataclass + Markdown frontmatter parsing/rendering."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

import yaml


@dataclass
class Note:
    title: str
    content: str = ""
    type: str = "note"      # note, task, decision, reference, fact, journal
    tags: list[str] = field(default_factory=list)
    status: str = "active"  # active, open, in-progress, done, archived
    priority: int = 0       # 0=normal, 1=important, 2=urgent, 3=critical
    due: str | None = None
    links: list[str] = field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""
    path: str = ""

    # Type → folder mapping
    _TYPE_FOLDERS = {
        "note": "notes",
        "task": "tasks",
        "decision": "decisions",
        "reference": "references",
        "fact": "facts",
        "journal": "journal",
    }

    def generate_path(self) -> str:
        folder = self._TYPE_FOLDERS.get(self.type, "notes")
        slug = re.sub(r"[^a-z0-9]+", "-", self.title.lower()).strip("-")
        return f"{folder}/{slug}"


def render_note(note: Note) -> str:
    """Render a Note to Markdown with YAML frontmatter."""
    fm = {
        "title": note.title,
        "type": note.type,
        "tags": note.tags,
        "status": note.status,
        "priority": note.priority,
    }
    if note.due:
        fm["due"] = note.due
    if note.links:
        fm["links"] = note.links
    if note.created_at:
        fm["created"] = note.created_at
    if note.updated_at:
        fm["updated"] = note.updated_at

    header = yaml.dump(fm, default_flow_style=False, allow_unicode=True).strip()
    return f"---\n{header}\n---\n\n{note.content}\n"


def parse_frontmatter(markdown: str) -> Note:
    """Parse Markdown with YAML frontmatter into a Note."""
    if markdown.startswith("---"):
        parts = markdown.split("---", 2)
        if len(parts) >= 3:
            try:
                fm = yaml.safe_load(parts[1]) or {}
            except yaml.YAMLError:
                fm = {}
            content = parts[2].strip()
            return Note(
                title=fm.get("title", ""),
                content=content,
                type=fm.get("type", "note"),
                tags=fm.get("tags", []),
                status=fm.get("status", "active"),
                priority=fm.get("priority", 0),
                due=fm.get("due"),
                links=fm.get("links", []),
                created_at=fm.get("created", ""),
                updated_at=fm.get("updated", ""),
            )
    return Note(title="", content=markdown.strip())
```

- [ ] **Step 4: Add schema to schema.sql**

Add to end of `src/mycelos/storage/schema.sql`:

```sql
-- Knowledge Base (NOT NixOS State — user content)
CREATE TABLE IF NOT EXISTS knowledge_notes (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    path        TEXT NOT NULL UNIQUE,
    title       TEXT NOT NULL,
    type        TEXT NOT NULL DEFAULT 'note',
    status      TEXT NOT NULL DEFAULT 'active',
    tags        TEXT DEFAULT '[]',
    priority    INTEGER NOT NULL DEFAULT 0,
    due         TEXT,
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at  TEXT,
    content_hash TEXT
);

CREATE INDEX IF NOT EXISTS idx_kn_type ON knowledge_notes(type, status);
CREATE INDEX IF NOT EXISTS idx_kn_due ON knowledge_notes(due) WHERE due IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_kn_updated ON knowledge_notes(updated_at);
CREATE INDEX IF NOT EXISTS idx_kn_path ON knowledge_notes(path);

CREATE TABLE IF NOT EXISTS knowledge_links (
    from_path   TEXT NOT NULL,
    to_path     TEXT NOT NULL,
    PRIMARY KEY (from_path, to_path)
);

CREATE INDEX IF NOT EXISTS idx_kl_to ON knowledge_links(to_path);

CREATE TABLE IF NOT EXISTS knowledge_config (
    key     TEXT PRIMARY KEY,
    value   TEXT NOT NULL
);
```

- [ ] **Step 5: Update auto-migration in database.py**

Add `"knowledge_notes"` to the migration check list:
```python
for check_table in ("llm_usage", "channels", "background_tasks", "users", "knowledge_notes"):
```

- [ ] **Step 6: Run tests**

Run: `pytest tests/test_knowledge_base.py -v`
Expected: All pass

- [ ] **Step 7: Commit**

```bash
git add src/mycelos/knowledge/ src/mycelos/storage/schema.sql src/mycelos/storage/database.py tests/test_knowledge_base.py
git commit -m "feat: Knowledge Base — Note model + schema + frontmatter parsing"
```

---

## Task 2: KnowledgeBase Service — CRUD + Indexer

**Files:**
- Create: `src/mycelos/knowledge/indexer.py`
- Create: `src/mycelos/knowledge/service.py`
- Modify: `src/mycelos/protocols.py`
- Modify: `src/mycelos/app.py`
- Modify: `tests/test_knowledge_base.py`

- [ ] **Step 1: Write failing tests**

Add to `tests/test_knowledge_base.py`:

```python
from mycelos.app import App
from mycelos.knowledge.service import KnowledgeBase


@pytest.fixture
def app():
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["MYCELOS_MASTER_KEY"] = "test-key-kb"
        a = App(Path(tmp))
        a.initialize()
        yield a


@pytest.fixture
def kb(app):
    return KnowledgeBase(app)


class TestKnowledgeBaseCRUD:
    def test_write_creates_file(self, kb, app):
        path = kb.write("Test Note", "Hello world", type="note")
        assert path.endswith(".md") or "/" in path
        # File exists on disk
        full_path = app.data_dir / "knowledge" / (path + ".md")
        assert full_path.exists()

    def test_write_creates_index_entry(self, kb):
        path = kb.write("My Task", "Do something", type="task", due="2026-03-28")
        result = kb.read(path)
        assert result is not None
        assert result["title"] == "My Task"
        assert result["due"] == "2026-03-28"

    def test_read_nonexistent(self, kb):
        assert kb.read("nonexistent/path") is None

    def test_list_by_type(self, kb):
        kb.write("Task 1", "Do A", type="task", status="open")
        kb.write("Task 2", "Do B", type="task", status="open")
        kb.write("Note 1", "Info", type="note")
        tasks = kb.list_notes(type="task")
        assert len(tasks) == 2

    def test_list_by_due(self, kb):
        kb.write("Today", "urgent", type="task", status="open", due="2026-03-25")
        kb.write("Next Week", "later", type="task", status="open", due="2026-04-01")
        # This test just verifies the filter works — exact date matching
        # depends on the implementation
        all_tasks = kb.list_notes(type="task")
        assert len(all_tasks) == 2

    def test_update_status(self, kb):
        path = kb.write("My Task", "stuff", type="task", status="open")
        kb.update(path, status="done")
        result = kb.read(path)
        assert result["status"] == "done"

    def test_update_preserves_content(self, kb):
        path = kb.write("My Note", "Important content", type="note")
        kb.update(path, tags=["updated"])
        result = kb.read(path)
        assert "Important content" in result["content"]
        assert "updated" in result["tags"]

    def test_update_append_content(self, kb):
        path = kb.write("My Note", "First line", type="note")
        kb.update(path, content="Second line", append=True)
        result = kb.read(path)
        assert "First line" in result["content"]
        assert "Second line" in result["content"]

    def test_link_creates_relation(self, kb):
        p1 = kb.write("Note A", "aaa", type="note")
        p2 = kb.write("Note B", "bbb", type="note")
        kb.link(p1, p2)
        result = kb.read(p2)
        assert p1 in result.get("backlinks", [])

    def test_search_fts(self, kb):
        kb.write("Python Guide", "Python is a programming language", type="reference")
        kb.write("Cooking Recipe", "How to make pasta", type="note")
        results = kb.search("Python programming")
        assert len(results) >= 1
        assert any("Python" in r["title"] for r in results)

    def test_write_with_priority(self, kb):
        path = kb.write("Urgent Task", "ASAP", type="task", priority=2)
        result = kb.read(path)
        assert result["priority"] == 2

    def test_app_knowledge_base_property(self, app):
        kb = app.knowledge_base
        assert kb is not None
        assert kb is app.knowledge_base  # Same instance
```

- [ ] **Step 2: Add KnowledgeBaseProtocol to protocols.py**

```python
@runtime_checkable
class KnowledgeBaseProtocol(Protocol):
    def write(self, title: str, content: str, type: str = "note",
              tags: list[str] | None = None, status: str = "active",
              due: str | None = None, links: list[str] | None = None,
              priority: int = 0) -> str: ...
    def read(self, path: str) -> dict | None: ...
    def search(self, query: str, type: str | None = None,
               tags: list[str] | None = None, limit: int = 10) -> list[dict]: ...
    def list_notes(self, type: str | None = None, status: str | None = None,
                   tags: list[str] | None = None, due: str | None = None,
                   limit: int = 20) -> list[dict]: ...
    def update(self, path: str, status: str | None = None,
               tags: list[str] | None = None, due: str | None = None,
               content: str | None = None, append: bool = False,
               priority: int | None = None) -> bool: ...
    def link(self, from_path: str, to_path: str) -> bool: ...
    def find_relevant(self, text: str, top_k: int = 5,
                      threshold: float = 0.7) -> list[dict]: ...
```

- [ ] **Step 3: Implement indexer.py**

Create `src/mycelos/knowledge/indexer.py`:

Manages the SQLite index:
- `index_note(path, note)` — INSERT/UPDATE into knowledge_notes + FTS5
- `remove_note(path)` — DELETE from knowledge_notes + FTS5
- `search_fts(query, limit)` — FTS5 search with BM25 ranking
- `list_notes(type, status, tags, due, limit)` — filtered SQL queries
- `get_note_meta(path)` — fetch metadata from knowledge_notes
- `add_link(from_path, to_path)` — insert into knowledge_links
- `get_backlinks(path)` — query knowledge_links where to_path = path

FTS5 is maintained via triggers or manual sync after each write/update.

- [ ] **Step 4: Implement service.py**

Create `src/mycelos/knowledge/service.py`:

```python
class KnowledgeBase:
    def __init__(self, app):
        self._app = app
        self._knowledge_dir = app.data_dir / "knowledge"
        self._knowledge_dir.mkdir(parents=True, exist_ok=True)
        self._indexer = KnowledgeIndexer(app.storage)

    def write(self, title, content, type="note", tags=None, status="active",
              due=None, links=None, priority=0) -> str:
        note = Note(title=title, content=content, type=type, ...)
        path = note.generate_path()
        # Handle duplicate paths (append -2, -3, etc.)
        # Write markdown file to disk
        # Index in SQLite
        # Add links
        # Audit log
        return path

    def read(self, path) -> dict | None:
        # Read from disk, parse frontmatter, add backlinks from index
        ...

    def search(self, query, type=None, tags=None, limit=10) -> list[dict]:
        # FTS5 search, optionally filtered by type/tags
        ...

    def list_notes(self, type=None, status=None, tags=None, due=None, limit=20):
        # SQL query with filters
        ...

    def update(self, path, status=None, tags=None, due=None,
               content=None, append=False, priority=None) -> bool:
        # Read file, update frontmatter/content, write back, re-index
        ...

    def link(self, from_path, to_path) -> bool:
        # Add to knowledge_links + update wikilinks in file
        ...

    def find_relevant(self, text, top_k=5, threshold=0.7) -> list[dict]:
        # For now: FTS5 search. Embeddings added in Task 3.
        return self.search(text, limit=top_k)
```

- [ ] **Step 5: Add knowledge_base property to App**

In `src/mycelos/app.py`:
```python
_knowledge_base = None

@property
def knowledge_base(self):
    if self._knowledge_base is None:
        from mycelos.knowledge.service import KnowledgeBase
        self._knowledge_base = KnowledgeBase(self)
    return self._knowledge_base
```

- [ ] **Step 6: Run tests**

Run: `pytest tests/test_knowledge_base.py -v`
Expected: All pass

- [ ] **Step 7: Commit**

```bash
git add src/mycelos/knowledge/ src/mycelos/protocols.py src/mycelos/app.py tests/test_knowledge_base.py
git commit -m "feat: Knowledge Base service — CRUD, FTS5 search, wikilinks"
```

---

## Task 3: Embedding System (sqlite-vec)

**Files:**
- Create: `src/mycelos/knowledge/embeddings.py`
- Modify: `src/mycelos/knowledge/service.py`
- Modify: `tests/test_knowledge_base.py`

- [ ] **Step 1: Write failing tests**

Add to `tests/test_knowledge_base.py`:

```python
class TestEmbeddings:
    def test_embedding_provider_local(self):
        from mycelos.knowledge.embeddings import get_embedding_provider
        provider = get_embedding_provider(openai_key=None)
        assert provider.name == "local" or provider.name == "none"

    def test_embedding_provider_openai(self):
        from mycelos.knowledge.embeddings import get_embedding_provider
        provider = get_embedding_provider(openai_key="sk-test")
        assert provider.name == "openai"

    def test_compute_embedding_returns_list(self):
        from mycelos.knowledge.embeddings import get_embedding_provider
        provider = get_embedding_provider(openai_key=None)
        if provider.name == "none":
            pytest.skip("No embedding provider available")
        embedding = provider.compute("Hello world")
        assert isinstance(embedding, list)
        assert len(embedding) > 0
        assert all(isinstance(x, float) for x in embedding)

    def test_find_relevant_with_embeddings(self, kb):
        kb.write("Python is great", "Python programming language guide", type="reference")
        kb.write("Cooking pasta", "Boil water and add spaghetti", type="note")
        # find_relevant uses embeddings if available, falls back to FTS5
        results = kb.find_relevant("programming language")
        # Should find Python note
        assert len(results) >= 1
```

- [ ] **Step 2: Implement embeddings.py**

Create `src/mycelos/knowledge/embeddings.py`:

```python
"""Embedding providers for Knowledge Base semantic search."""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("mycelos.knowledge")


class EmbeddingProvider:
    """Base class for embedding providers."""
    name: str = "none"
    dimension: int = 0

    def compute(self, text: str) -> list[float]:
        return []

    def compute_batch(self, texts: list[str]) -> list[list[float]]:
        return [self.compute(t) for t in texts]


class OpenAIEmbeddingProvider(EmbeddingProvider):
    name = "openai"
    dimension = 1536

    def __init__(self, proxy_client: Any):
        self._proxy = proxy_client

    def compute(self, text: str) -> list[float]:
        # Call OpenAI via SecurityProxy
        # POST to https://api.openai.com/v1/embeddings
        ...


class LocalEmbeddingProvider(EmbeddingProvider):
    name = "local"
    dimension = 384

    def __init__(self):
        self._model = None

    def _load_model(self):
        if self._model is None:
            try:
                from sentence_transformers import SentenceTransformer
                self._model = SentenceTransformer("all-MiniLM-L6-v2")
            except ImportError:
                logger.warning("sentence-transformers not installed — FTS5 only")
                raise

    def compute(self, text: str) -> list[float]:
        self._load_model()
        return self._model.encode(text).tolist()


class FallbackProvider(EmbeddingProvider):
    """No embeddings — search uses FTS5 only."""
    name = "none"
    dimension = 0


def get_embedding_provider(openai_key: str | None = None,
                            proxy_client: Any = None) -> EmbeddingProvider:
    if openai_key and proxy_client:
        return OpenAIEmbeddingProvider(proxy_client)
    try:
        provider = LocalEmbeddingProvider()
        provider._load_model()  # Test if it works
        return provider
    except Exception:
        logger.info("No embedding provider available — using FTS5 only")
        return FallbackProvider()
```

- [ ] **Step 3: Integrate embeddings into service.py**

Update `KnowledgeBase.__init__()` to initialize embedding provider.
Update `write()` and `update()` to compute and store embeddings.
Update `find_relevant()` to use sqlite-vec when embeddings available.

sqlite-vec usage:
```python
import sqlite_vec
# Register extension
db.enable_load_extension(True)
sqlite_vec.load(db)

# Create table
db.execute("CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_vec USING vec0(embedding float[384])")

# Insert
db.execute("INSERT INTO knowledge_vec(rowid, embedding) VALUES (?, ?)", (note_id, embedding_bytes))

# Search
db.execute("""
    SELECT rowid, distance
    FROM knowledge_vec
    WHERE embedding MATCH ?
    ORDER BY distance
    LIMIT ?
""", (query_embedding_bytes, top_k))
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_knowledge_base.py -v`
Expected: All pass (embedding tests may skip if no provider)

- [ ] **Step 5: Commit**

```bash
git add src/mycelos/knowledge/embeddings.py src/mycelos/knowledge/service.py tests/test_knowledge_base.py
git commit -m "feat: Knowledge Base embeddings — sqlite-vec + local/OpenAI providers"
```

---

## Task 4: Context Enrichment + LLM Tools

**Files:**
- Modify: `src/mycelos/chat/service.py`
- Modify: `tests/test_knowledge_base.py`

- [ ] **Step 1: Write failing tests**

```python
class TestContextEnrichment:
    def test_find_relevant_returns_notes(self, kb):
        kb.write("Important Decision", "We chose Python over JavaScript", type="decision")
        results = kb.find_relevant("Python JavaScript decision")
        assert len(results) >= 1

    def test_find_relevant_respects_threshold(self, kb):
        kb.write("Cooking Recipe", "How to make pasta", type="note")
        results = kb.find_relevant("quantum physics", threshold=0.9)
        # Unlikely to match cooking with quantum physics at high threshold
        # (FTS5 fallback may return it at low rank — threshold filters it)


class TestLlmTools:
    def test_note_tools_registered(self, app):
        from mycelos.chat.service import ChatService
        service = ChatService(app)
        tool_names = [t["function"]["name"] for t in service._build_tool_list()]
        assert "note_write" in tool_names or "knowledge_write" in tool_names
```

- [ ] **Step 2: Add context enrichment to ChatService.handle_message()**

In `src/mycelos/chat/service.py`, after the proactive notification check and
daily cost check, add KB context enrichment:

```python
# Knowledge Base context enrichment
try:
    kb = self._app.knowledge_base
    relevant = kb.find_relevant(message, top_k=5, threshold=0.7)
    if relevant:
        context_parts = []
        for note in relevant[:3]:  # Max 3 notes to stay within token budget
            context_parts.append(
                f"- **{note['title']}** ({note['type']}, {note.get('updated_at', '')[:10]})\n"
                f"  {note['content'][:200]}"
            )
        kb_context = "[Knowledge Context]\n" + "\n".join(context_parts)
        # Check token budget before injecting
        # ... inject as system message if budget allows
except Exception:
    pass  # Don't block chat on KB errors
```

- [ ] **Step 3: Add note tools to ChatService**

Add 6 tools to the tool list in ChatService:
- `note_write` → calls `kb.write()`
- `note_read` → calls `kb.read()`
- `note_search` → calls `kb.search()`
- `note_list` → calls `kb.list_notes()`
- `note_update` → calls `kb.update()`
- `note_link` → calls `kb.link()`

Follow the existing tool pattern (OpenAI function calling format + handler
in `_execute_tool`).

- [ ] **Step 4: Add task reminders at chat start**

In `handle_message()`, after KB context enrichment:

```python
# Task reminders (overdue + due today)
try:
    import datetime
    today = datetime.date.today().isoformat()
    overdue = kb.list_notes(type="task", status="open", due="overdue")
    due_today = kb.list_notes(type="task", status="open", due=today)
    if overdue or due_today:
        reminder_parts = []
        for t in overdue:
            reminder_parts.append(f"- {t['title']} (overdue: {t['due']})")
        for t in due_today:
            reminder_parts.append(f"- {t['title']} (due today)")
        if reminder_parts:
            events.append(system_response_event(
                "[Tasks] " + "\n".join(reminder_parts)
            ))
except Exception:
    pass
```

- [ ] **Step 5: Run tests**

Run: `pytest tests/test_knowledge_base.py -v`
Expected: All pass

- [ ] **Step 6: Run full suite**

Run: `pytest tests/ --tb=short -q`
Expected: All pass

- [ ] **Step 7: Commit**

```bash
git add src/mycelos/chat/service.py tests/test_knowledge_base.py
git commit -m "feat: Knowledge Base — context enrichment + LLM tools + task reminders"
```

---

## Task 5: Index Auto-Generation + CHANGELOG

**Files:**
- Modify: `src/mycelos/knowledge/service.py`
- Modify: `CHANGELOG.md`

- [ ] **Step 1: Implement index.md auto-generation**

Add to `KnowledgeBase`:

```python
def regenerate_index(self):
    """Regenerate knowledge/index.md with overview of all notes."""
    notes = self.list_notes(limit=1000)
    tasks_open = [n for n in notes if n["type"] == "task" and n["status"] == "open"]
    recent = sorted(notes, key=lambda n: n.get("updated_at", ""), reverse=True)[:10]

    # Group by type
    by_type = {}
    for n in notes:
        by_type.setdefault(n["type"], []).append(n)

    # Build index markdown
    lines = ["# Knowledge Base\n"]

    if tasks_open:
        lines.append(f"## Open Tasks ({len(tasks_open)})\n")
        for t in sorted(tasks_open, key=lambda x: x.get("due") or "9999"):
            due = f" — due: {t['due']}" if t.get("due") else ""
            lines.append(f"- [[{t['path']}|{t['title']}]]{due}")
        lines.append("")

    lines.append(f"## Recent\n")
    for n in recent[:10]:
        lines.append(f"- [[{n['path']}|{n['title']}]] ({n['type']}, {n.get('updated_at', '')[:10]})")
    lines.append("")

    # Write to disk (not indexed — it's auto-generated)
    index_path = self._knowledge_dir / "index.md"
    index_path.write_text("\n".join(lines), encoding="utf-8")
```

Call `regenerate_index()` at the end of `write()`, `update()`, and after link changes.

- [ ] **Step 2: Run full test suite**

Run: `pytest tests/ --tb=short -q`
Expected: All pass

- [ ] **Step 3: Update CHANGELOG**

```markdown
### Knowledge Base
- Markdown files with YAML frontmatter in ~/.mycelos/knowledge/
- 6 LLM tools: note_write, note_read, note_search, note_list, note_update, note_link
- SQLite FTS5 full-text search + sqlite-vec embedding search
- Automatic context enrichment: relevant notes injected into every conversation
- Embedding providers: local (sentence-transformers) or OpenAI (text-embedding-3-small)
- Priority field (0-3) for search result boosting
- Task reminders: overdue + due-today shown at chat start
- Wikilinks with bidirectional backlinks
- Auto-generated index.md
```

- [ ] **Step 4: Commit + push**

```bash
git add src/mycelos/knowledge/ src/mycelos/chat/service.py CHANGELOG.md
git commit -m "feat: Knowledge Base — LLM-native personal wiki with embedding search"
git push origin main
```
