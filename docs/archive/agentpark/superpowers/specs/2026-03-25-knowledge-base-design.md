# Knowledge Base — Design Specification

**Date:** 2026-03-25
**Status:** DRAFT
**Scope:** LLM-native personal wiki with embedding search and automatic context enrichment

---

## 1. Overview

A conversational knowledge base where everything happens through chat. The
user says "Merke dir dass Alex 50k Budget genehmigt hat" and it becomes a
linked, searchable, tagged note. When the user later says "Schreib Alex
wegen dem Budget", the LLM automatically finds and uses that context.

### Core Principles

1. **Conversational first** — No editor, no UI needed. Chat is the interface.
2. **Markdown as truth** — Files are exportable, Git-friendly, Obsidian-compatible.
3. **Automatic context** — Relevant knowledge is injected into every conversation.
4. **Memory stays** — System config in Memory, everything else in Knowledge Base.

### What This Is NOT

- Not a Notion clone (no block editor, no database views, no collaboration)
- Not a search engine (the LLM understands context, not just keywords)
- Not a replacement for Memory (Memory = system config, KB = user knowledge)

---

## 2. Architecture

```
User message arrives
  |
  v
Compute embedding for message (local model or OpenAI, ~50ms)
  |
  v
sqlite-vec: find top 5 similar notes (~5ms, local)
  |
  v
Score > 0.7? → inject into system prompt as [Knowledge Context]
  |
  v
LLM responds with relevant context from your knowledge base
```

### Storage Stack

```
~/.mycelos/knowledge/           ← Markdown files (source of truth)
  projects/mycelos.md
  tasks/fix-planner.md
  decisions/security-fail-closed.md
  facts/alex-budget.md
  journal/2026-03-25.md
  index.md                     ← Auto-generated

mycelos.db (SQLite)
  knowledge_notes              ← Index: path, title, type, status, tags, due
  knowledge_links              ← Relations: from_path, to_path
  knowledge_fts                ← FTS5 full-text search
  knowledge_embeddings         ← sqlite-vec embeddings (384 or 1536 dims)
```

### Why Markdown + SQLite?

- **Exportable** — Copy the folder, open in Obsidian or any editor
- **Git-friendly** — Version control your knowledge
- **Human-readable** — No lock-in, notes readable without Mycelos
- **LLM-friendly** — Markdown is the native LLM format
- SQLite is the INDEX, not the storage. Files are the truth.

---

## 3. Data Model

### Note Format (Markdown + YAML Frontmatter)

```yaml
---
title: Alex Budget Genehmigung
type: fact
tags: [budget, alex, meeting]
status: active
created: 2026-03-25
updated: 2026-03-25
due: null
links:
  - projects/mycelos
  - tasks/budget-review
---

Beim Meeting am 23. März hat Alex das Budget von 50k genehmigt.
Nächster Schritt: Bestellung aufgeben bis Ende März.

## Related
- [[projects/mycelos]] — Projektkontext
- [[tasks/budget-review]] — Follow-up
```

### Note Types

| Type | Description | Has Due Date |
|------|-------------|-------------|
| `note` | General note, memo | No |
| `task` | Actionable item | Yes |
| `decision` | Documented decision with context | No |
| `reference` | External link, resource, doc | No |
| `fact` | Learned fact about the world or user | No |
| `journal` | Daily session summary | No |

### Priority

Integer field (0-3) for search result boosting:

| Value | Meaning |
|-------|---------|
| 0 | Normal (default) |
| 1 | Important |
| 2 | Urgent |
| 3 | Critical |

Notes with higher priority get a boost in similarity search results.
The LLM sets priority based on user language: "wichtig", "dringend",
"ASAP" → priority 2. Default is 0.

### Statuses

| Status | Meaning |
|--------|---------|
| `active` | Current, relevant |
| `open` | Task: not started |
| `in-progress` | Task: being worked on |
| `done` | Task: completed |
| `archived` | No longer relevant, kept for history |

### Wikilinks

Notes link via `[[path/to/note]]` syntax:
- `[[projects/mycelos]]` → links to `projects/mycelos.md`
- Bidirectional: if A links to B, B's backlinks show A
- Links stored in `knowledge_links` table for fast traversal

### Folder Structure

```
knowledge/
  projects/       ← Project pages
  tasks/          ← Actionable items with due dates
  decisions/      ← Documented decisions
  facts/          ← Learned facts
  references/     ← External resources
  journal/        ← Daily summaries
  index.md        ← Auto-generated overview
```

Folders are conventions, not enforced. The LLM creates notes in the
appropriate folder based on type.

---

## 4. SQLite Schema

### knowledge_notes

```sql
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
```

Note: `id INTEGER PRIMARY KEY AUTOINCREMENT` is required for FTS5 `content_rowid`.

### knowledge_links

```sql
CREATE TABLE IF NOT EXISTS knowledge_links (
    from_path   TEXT NOT NULL,
    to_path     TEXT NOT NULL,
    PRIMARY KEY (from_path, to_path)
);

CREATE INDEX IF NOT EXISTS idx_kl_to ON knowledge_links(to_path);
```

### knowledge_fts (FTS5)

```sql
CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_fts USING fts5(
    title, content, tags,
    content='knowledge_notes',
    content_rowid='id'
);
```

Note: `content_rowid='id'` references the `INTEGER PRIMARY KEY AUTOINCREMENT`
column in `knowledge_notes`. The `path` column is not indexed in FTS5 (it is
a lookup key, not searchable content).

### knowledge_embeddings (sqlite-vec)

```sql
-- Created programmatically via sqlite-vec
-- Columns: note_id (INTEGER), embedding (FLOAT32 vector)
-- Dimension is fixed at table creation time
```

### Embedding Dimension Management

The embedding table is created with a fixed dimension matching the active
provider:
- Local model (`all-MiniLM-L6-v2`): 384 dimensions
- OpenAI (`text-embedding-3-small`): 1536 dimensions

**On provider change** (e.g., user adds OpenAI key after using local model):
1. Drop the existing `knowledge_embeddings` table
2. Recreate with the new dimension
3. Recompute all embeddings in background (via BackgroundTaskRunner)
4. Log audit event `knowledge.embeddings.recomputed`
5. During recomputation: fall back to FTS5-only search (graceful degradation)

A `knowledge_meta` row in the `knowledge_notes` table or a separate small
table tracks the current embedding provider and dimension:

```sql
CREATE TABLE IF NOT EXISTS knowledge_config (
    key     TEXT PRIMARY KEY,
    value   TEXT NOT NULL
);
-- Rows: embedding_provider, embedding_dimension
```

---

## 5. Embedding System

### Provider Selection

```
At startup:
  1. Check if OpenAI credential exists
     → Yes: use text-embedding-3-small (1536 dims, best quality)
  2. No OpenAI key:
     → Use sentence-transformers/all-MiniLM-L6-v2 (384 dims, local, free)
     → First use: download model (~80MB, cached)
```

### When Embeddings Are Computed

- **On note save/update**: compute embedding for the note content + title
- **On every user message**: compute embedding for the message text
- Embeddings are stored in `knowledge_embeddings` table
- No recomputation needed unless content changes (tracked via `content_hash`)

### Similarity Search

```python
# Pseudo-code for context enrichment
def find_relevant_notes(message: str, top_k: int = 5, threshold: float = 0.7):
    embedding = compute_embedding(message)
    results = sqlite_vec_search(embedding, top_k)
    return [r for r in results if r.similarity >= threshold]
```

### User Communication

During `mycelos init` or first KB use:
```
Knowledge Base initialized.
Embedding search: using local model (sentence-transformers).
For better search quality, add an OpenAI key:
  mycelos credential store openai
```

---

## 6. LLM Tools

### note_write

```json
{
  "name": "note_write",
  "description": "Create a new note in the knowledge base.",
  "parameters": {
    "title": "string (required)",
    "content": "string (required, markdown)",
    "type": "note|task|decision|reference|fact|journal",
    "tags": ["string"],
    "status": "active|open|in-progress|done",
    "due": "YYYY-MM-DD or null",
    "links": ["path/to/other/note"]
  }
}
```

The LLM chooses type, tags, and folder automatically based on content.
"Neue Aufgabe: Blumen kaufen bis Freitag" → type: task, due: Friday,
tags: [personal], folder: tasks/.

### note_read

```json
{
  "name": "note_read",
  "description": "Read a note with its content and backlinks.",
  "parameters": {
    "path": "string (required)"
  }
}
```

Returns full content + YAML frontmatter + list of backlinks.

### note_search

```json
{
  "name": "note_search",
  "description": "Search the knowledge base. Uses both full-text and semantic search.",
  "parameters": {
    "query": "string (required)",
    "type": "string (optional, filter by type)",
    "tags": ["string (optional, filter by tags)"],
    "limit": "int (default 10)"
  }
}
```

Combines FTS5 keyword search + embedding similarity. Results ranked by
combined score.

### note_list

```json
{
  "name": "note_list",
  "description": "List notes filtered by type, status, tags, or due date.",
  "parameters": {
    "type": "string (optional)",
    "status": "string (optional)",
    "tags": ["string (optional)"],
    "due": "today|overdue|this_week|YYYY-MM-DD (optional)",
    "limit": "int (default 20)"
  }
}
```

"Was steht heute an?" → `note_list(type="task", status="open", due="today")`

### note_update

```json
{
  "name": "note_update",
  "description": "Update a note's status, tags, content, or due date.",
  "parameters": {
    "path": "string (required)",
    "status": "string (optional)",
    "tags": ["string (optional)"],
    "due": "string (optional)",
    "content": "string (optional, appends or replaces)",
    "append": "bool (default false, if true appends to content)"
  }
}
```

"Planner Handoff ist fertig" → `note_update(path="tasks/fix-planner", status="done")`

### note_link

```json
{
  "name": "note_link",
  "description": "Create a link between two notes.",
  "parameters": {
    "from_path": "string (required)",
    "to_path": "string (required)"
  }
}
```

Links are bidirectional — stored in `knowledge_links` and visible as
backlinks when reading either note.

---

## 7. Automatic Context Enrichment

### How It Works

At the start of `handle_message()` in ChatService:

```python
# After proactive notification check, before LLM call:
relevant_notes = knowledge_base.find_relevant(message, top_k=5, threshold=0.7)
if relevant_notes:
    context = format_knowledge_context(relevant_notes)
    # Inject into system prompt or as a system message
    conversation.append({
        "role": "system",
        "content": f"[Knowledge Context]\n{context}"
    })
```

### Context Format

```
[Knowledge Context]
The following notes from your knowledge base may be relevant:

1. **Alex Budget Genehmigung** (fact, 2026-03-23)
   Beim Meeting am 23. März hat Alex das Budget von 50k genehmigt.

2. **Budget Review** (task, due: 2026-03-30, status: open)
   Bestellung aufgeben bis Ende März.
```

### Cost

- Embedding computation: ~$0.00001 per message (OpenAI) or free (local)
- sqlite-vec search: < 5ms, zero cost
- Context injection: adds ~200-500 tokens to the prompt per relevant note
- Total overhead: negligible for the value it provides

### When NOT To Inject

- If no notes score above 0.7 similarity → no injection
- Slash commands (start with `/`) → skip KB search
- System messages → skip KB search

---

## 8. Memory Integration

### What Stays in Memory

| Key Pattern | Example | Stays in Memory |
|-------------|---------|----------------|
| `user.name` | "Stefan" | Yes — system config |
| `user.language` | "de" | Yes — system config |
| `user.timezone` | "Europe/Berlin" | Yes — system config |
| `agent.*.preference` | Creator model tier | Yes — agent config |

### What Moves to Knowledge Base

| Current Memory | KB Note |
|---------------|---------|
| "User likes Python over JavaScript" | `facts/stefan-prefers-python.md` |
| "User's wife birthday is March 15" | `facts/frau-geburtstag.md` |
| "We decided to use fail-closed security" | `decisions/security-fail-closed.md` |
| "TODO: fix planner handoff" | `tasks/fix-planner-handoff.md` |

### Migration Path

Memory entries that match personal facts, decisions, or tasks should
gradually be created as KB notes instead. The Memory system is not removed —
it keeps its role for system config. The LLM is instructed to use
`note_write` for user knowledge and `memory.set` only for system preferences.

---

## 9. Proactive Features

### Task Reminder at Chat Start

At the start of each chat session, check for overdue and due-today tasks:

```python
overdue = kb.note_list(type="task", status="open", due="overdue")
today = kb.note_list(type="task", status="open", due="today")
if overdue or today:
    reminder = format_task_reminder(overdue, today)
    events.append(system_response_event(reminder))
```

The reminder text is generated by the LLM (not hardcoded CLI strings), so
it adapts to the user's language preference automatically. The LLM receives
the task list as structured data and formats it naturally.

Example (LLM output, language depends on user preference):
```
Du hast 2 überfällige Tasks:
  - Blumen für Frau kaufen (fällig: gestern)
  - Budget Review (fällig: heute)
```

### Auto-Generated Index

`knowledge/index.md` is regenerated on every note change:

```markdown
# Knowledge Base

## Recent (last 7 days)
- [[facts/alex-budget]] — 2026-03-25
- [[tasks/blumen-kaufen]] — due: 2026-03-26

## Open Tasks (3)
- [[tasks/blumen-kaufen]] — due: 2026-03-26
- [[tasks/budget-review]] — due: 2026-03-30
- [[tasks/google-mcp]] — no due date

## Projects
- [[projects/mycelos]] (12 linked notes)

## Tags
#security (4) | #personal (3) | #budget (2) | #mcp (5)
```

### Session Journal (Future)

The Session Summary agent can auto-create daily journal entries from chat
sessions. This is not in Phase 1 scope but the infrastructure supports it.

---

## 10. Implementation Structure

### New Files

```
src/mycelos/knowledge/
  __init__.py
  service.py          ← KnowledgeBase class (main service)
  indexer.py           ← SQLite index + FTS5 + embeddings
  embeddings.py        ← Embedding provider (OpenAI or local)
  note.py              ← Note dataclass + frontmatter parsing
tests/
  test_knowledge_base.py
```

### Modified Files

- `src/mycelos/storage/schema.sql` — Add knowledge tables
- `src/mycelos/storage/database.py` — Auto-migration for new tables
- `src/mycelos/app.py` — Add `knowledge_base` lazy property
- `src/mycelos/chat/service.py` — Context enrichment + note tools
- `src/mycelos/protocols.py` — KnowledgeBaseProtocol:

```python
@runtime_checkable
class KnowledgeBaseProtocol(Protocol):
    def write(self, title: str, content: str, type: str = "note",
              tags: list[str] | None = None, status: str = "active",
              due: str | None = None, links: list[str] | None = None) -> str: ...
    def read(self, path: str) -> dict | None: ...
    def search(self, query: str, type: str | None = None,
               tags: list[str] | None = None, limit: int = 10) -> list[dict]: ...
    def list_notes(self, type: str | None = None, status: str | None = None,
                   tags: list[str] | None = None, due: str | None = None,
                   limit: int = 20) -> list[dict]: ...
    def update(self, path: str, status: str | None = None,
               tags: list[str] | None = None, due: str | None = None,
               content: str | None = None, append: bool = False) -> bool: ...
    def link(self, from_path: str, to_path: str) -> bool: ...
    def find_relevant(self, text: str, top_k: int = 5,
                      threshold: float = 0.7) -> list[dict]: ...
```

### Dependencies

- `sqlite-vec` — Vector similarity search (pip install sqlite-vec)
- `sentence-transformers` — Local embedding model (optional, lazy import)
- `pyyaml` — Already installed (used by i18n)

---

## 11. Security

- Knowledge base files are in `~/.mycelos/knowledge/` — same permissions
  as the rest of Mycelos data
- Notes are NOT accessible to agents unless they have the `knowledge.*`
  capability
- **Capability check is fail-closed**: if the capability validation errors
  out, DENY the action. Never `except: return None`. This follows
  Constitution Rule 3.
- The `note_write` tool validates that notes stay within the knowledge
  directory (no path traversal: `..` in path is rejected)
- Embedding API calls go through the SecurityProxy (credential injection)
- Note content is NOT sanitized by ResponseSanitizer (it's user content,
  not LLM output) — but credential patterns in notes ARE flagged on read
- Granting `knowledge.*` capability to an agent follows the existing agent
  registration flow, which already triggers `config.apply_from_state()`

---

## 11.1 Audit Events

Every state-mutating KB operation logs an audit event (Constitution Rule 1):

| Event | Details |
|-------|---------|
| `knowledge.note.created` | path, type, tags, user_id |
| `knowledge.note.updated` | path, fields_changed, user_id |
| `knowledge.note.deleted` | path, user_id |
| `knowledge.note.linked` | from_path, to_path, user_id |
| `knowledge.search` | query, results_count, user_id |
| `knowledge.context.injected` | note_paths, similarity_scores, user_id |
| `knowledge.embeddings.recomputed` | provider, dimension, note_count |

---

## 11.2 Note Deletion

Notes can be either **archived** (status: archived, file kept) or
**deleted** (file removed, index entry removed). The `note_update` tool
handles archival. A `note_delete` tool is NOT provided — the LLM uses
`note_update(path, status="archived")` instead. Physical deletion is only
available via slash command `/knowledge delete <path>` for advanced users.

---

## 11.3 Update Semantics

`note_update` behavior:
- If only `status`, `tags`, or `due` are provided: update frontmatter only,
  body content unchanged
- If `content` is provided and `append=false` (default): replace the body
  content (everything after the YAML frontmatter), keep frontmatter fields
  that were not explicitly changed
- If `content` is provided and `append=true`: append to the body content
  after a blank line
- Frontmatter fields not mentioned in the update are preserved
- `updated_at` is always set to now

---

## 11.4 Context Enrichment Token Budget

Maximum KB context injection: **1500 tokens** (configurable).

Before injecting notes into the system prompt, `LLMBroker.count_tokens()`
is used to check the total context size. If the conversation is already
near the model's context limit, KB context is truncated or omitted:

```python
MAX_KB_CONTEXT_TOKENS = 1500

# Measure current conversation tokens
current_tokens = llm.count_tokens(conversation)
available = model_context_limit - current_tokens - 500  # 500 token safety margin
kb_budget = min(MAX_KB_CONTEXT_TOKENS, available)

if kb_budget < 200:
    # Not enough room — skip KB context
    pass
else:
    # Inject notes up to kb_budget tokens
    inject_knowledge_context(relevant_notes, max_tokens=kb_budget)
```

---

## 11.5 Embedding Download Failure

If the local sentence-transformers model cannot be downloaded (no internet,
disk full, etc.):

1. Log warning: "Embedding model not available — using FTS5 search only"
2. KB search falls back to FTS5 full-text search (keyword-based)
3. Context enrichment still works, but with keyword matching instead of
   semantic similarity
4. User is notified once: "For better search, ensure internet access or
   add an OpenAI key: `mycelos credential store openai`"
5. This is NOT a security failure — FTS5 fallback is acceptable degradation

---

## 12. Testing Strategy

### Unit Tests

- Note CRUD: create, read, update, delete markdown files
- Frontmatter parsing and generation
- FTS5 search accuracy
- Wikilink parsing and bidirectional link resolution
- Embedding computation (mocked provider)
- Similarity search with sqlite-vec
- Index auto-generation
- note_list filters (type, status, due date)

### Integration Tests

- Full flow: note_write → embedding → note_search finds it
- Context enrichment: message triggers KB search, relevant notes injected
- Task reminder at chat start
- Memory vs KB: system config stays in Memory, facts go to KB

### Security Tests

- Path traversal blocked (no `../../etc/passwd`)
- Agent without `knowledge.*` capability cannot access notes
- Embedding API calls go through SecurityProxy

---

## 13. Scope Boundary

### In Scope (Phase 1)

- Markdown files with YAML frontmatter
- 6 LLM tools: write, read, search, list, update, link
- SQLite index with FTS5 full-text search
- sqlite-vec embedding search (local model default, OpenAI upgrade)
- Automatic context enrichment on every message
- Task reminders (overdue + due today)
- Wikilinks with bidirectional backlinks
- Auto-generated index.md
- Capability-scoped access

### Out of Scope (Future)

- Web UI for note browsing (table/kanban/graph views)
- Session journal auto-generation
- Obsidian vault full compatibility
- Note templates
- Scheduled digests ("weekly summary of new notes")
- Import from Notion/Obsidian
- Multi-user KB isolation
