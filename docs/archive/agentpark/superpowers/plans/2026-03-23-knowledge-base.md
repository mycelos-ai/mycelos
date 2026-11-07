# Knowledge Base — LLM-Native Personal Wiki

**Status:** PLAN (nicht implementieren)
**Priority:** P1 (after Planner Handoff)

---

## Vision

A personal knowledge base that feels like talking to your notebook.
Everything happens through conversation — no slash commands needed.
The LLM reads, writes, links, tags, and searches your notes naturally.

"Merke dir, dass wir fuer Security fail-closed entschieden haben"
→ Creates a note, tags it #security #decision, links it to the Mycelos project.

"Was hatten wir bei den MCP-Tools entschieden?"
→ Semantic search, finds the decision note, shows context.

"Zeig mir alle offenen Tasks"
→ Searches notes with status: open, shows as list with due dates.

---

## Data Model

### Notes as Markdown Files

```
~/.mycelos/knowledge/
  index.md                    ← Auto-generated, links to all notes
  projects/
    mycelos.md                 ← Project page with links to related notes
    rechnungs-scanner.md
  decisions/
    security-fail-closed.md
    mcp-http-transport.md
  tasks/
    fix-planner-handoff.md
    google-mcp-integration.md
  journal/
    2026-03-23.md             ← Daily journal, auto-generated from sessions
```

### Frontmatter (YAML)

```yaml
---
title: Security Fail-Closed Decision
type: decision                    # note, task, decision, reference, journal
tags: [security, architecture]
status: active                    # active, done, archived, open, in-progress
created: 2026-03-23
updated: 2026-03-23
due: null                         # for tasks
links:                            # relations to other notes
  - projects/mycelos.md
  - decisions/mcp-http-transport.md
---

We decided that all security checks must fail closed.
If a capability check errors out, DENY the action.
Never `except: return None` in security code.

## Context
- Discussed 2026-03-23 during security review
- Based on OpenClaw analysis where security was #1 criticism

## Related
- [[projects/mycelos]] — parent project
- [[decisions/mcp-http-transport]] — similar architectural decision
```

### Wikilinks

Notes link to each other via `[[path/to/note]]` syntax (like Obsidian):
- `[[projects/mycelos]]` → links to projects/mycelos.md
- `[[decisions/security-fail-closed]]` → links to decisions/security-fail-closed.md
- Bidirectional: if A links to B, B's backlinks show A

### index.md (Auto-Generated)

```markdown
# Knowledge Base

## Recent
- [[decisions/security-fail-closed]] — 2026-03-23
- [[tasks/fix-planner-handoff]] — due: 2026-03-25

## Projects
- [[projects/mycelos]] (12 linked notes)
- [[projects/rechnungs-scanner]] (3 linked notes)

## Open Tasks
- [[tasks/fix-planner-handoff]] — due: 2026-03-25, status: open
- [[tasks/google-mcp-integration]] — status: open

## Tags
#security (4 notes) | #architecture (3) | #telegram (2) | #mcp (5)
```

---

## LLM Tools

The LLM interacts with the knowledge base through these tools:

### note_write
```
"Merke dir dass wir sqlite-vec fuer Embeddings nehmen"
→ note_write({
    title: "SQLite-Vec for Embeddings",
    content: "Decision to use sqlite-vec...",
    type: "decision",
    tags: ["database", "search"],
    links: ["projects/mycelos"]
  })
```

### note_search
```
"Was hatten wir bei Security entschieden?"
→ note_search({query: "security decision"})
→ Returns matching notes with snippets
```

### note_read
```
"Zeig mir die Mycelos Projektseite"
→ note_read({path: "projects/mycelos"})
→ Returns full content + backlinks
```

### note_list
```
"Welche Tasks sind offen?"
→ note_list({type: "task", status: "open"})
→ Returns list with due dates
```

### note_update
```
"Der Planner Handoff ist jetzt erledigt"
→ note_update({path: "tasks/fix-planner-handoff", status: "done"})
```

### note_link
```
"Das gehoert zum Mycelos Projekt"
→ note_link({from: "decisions/security-fail-closed", to: "projects/mycelos"})
```

---

## Storage Architecture

```
Knowledge Base
  |
  ├── Markdown Files (source of truth, exportable)
  |     ~/.mycelos/knowledge/**/*.md
  |
  ├── SQLite Index (fast search, metadata queries)
  |     notes table: path, title, type, status, tags, created, updated, due
  |     links table: from_path, to_path (relations)
  |     fts table: full-text search via FTS5
  |
  └── Embeddings (optional, Phase 2)
        sqlite-vec: vector search for semantic queries
        Generated via Haiku or local model on save
```

### Why Markdown + SQLite (not just SQLite)?

1. **Exportable** — Copy the folder, you have your wiki. Works in Obsidian, VS Code, any editor.
2. **Git-friendly** — Can version-control your knowledge base.
3. **Human-readable** — No lock-in. Even without Mycelos, your notes are readable.
4. **LLM-friendly** — Markdown is the native format for LLMs.

SQLite is the INDEX, not the storage. It makes search fast but the files are the truth.

---

## Integration with Existing Systems

### Memory → Knowledge Base

Current memory entries (user.preference.*, user.fact.*, etc.) could migrate:
- Preferences → knowledge/preferences.md (single note)
- Decisions → knowledge/decisions/*.md (one per decision)
- Context → knowledge/projects/*.md (project notes)

Memory stays for quick LLM-accessible facts. Knowledge Base is for structured, linked content.

### Session Summaries → Journal

The Session Summary agent could auto-generate daily journal entries:
```
knowledge/journal/2026-03-23.md
---
title: "2026-03-23"
type: journal
tags: [telegram, mcp, security, github]
---

## What was done
- Telegram production-ready (polling, typing indicator)
- GitHub MCP via official HTTP endpoint
- Security Gate on all tool calls
- Cost tracking fixed

## Decisions
- [[decisions/security-fail-closed]]
- [[decisions/mcp-http-transport]]

## Open Items
- [[tasks/fix-planner-handoff]]
```

### Telegram / Chat

User sends: "Notiz: Meeting mit Alex am Freitag, Thema Budget"
→ LLM creates note with type: note, tags: [meeting, alex], due: 2026-03-28

User sends: "Was steht bei meinen Tasks an?"
→ LLM searches notes with type: task, status: open, sorts by due date

---

## Phase Plan

### Phase 1: Core (1 session)
- Markdown files with YAML frontmatter
- note_write, note_read, note_search, note_list tools
- SQLite index with FTS5 for full-text search
- index.md auto-generation
- Wikilinks parsing

### Phase 2: Relations + Tags (1 session)
- Bidirectional links (backlinks)
- Tag-based browsing
- note_link, note_update tools
- Tag cloud in index.md

### Phase 3: Semantic Search (optional)
- sqlite-vec for embedding-based search
- Embeddings via Haiku on save
- "Find notes similar to X"

### Phase 4: Web UI (future)
- React component for note browsing
- Table/Kanban views for tasks
- Graph visualization of note links (like Obsidian)

---

## Daily Task Management (Key Use Case)

The knowledge base doubles as a task manager. The LLM handles it conversationally:

```
User: "Was steht heute an?"
→ LLM calls note_list({type: "task", status: "open", due: "today"})
→ "Du hast 3 Tasks fuer heute:
   1. Planner Handoff fixen (due: heute)
   2. Meeting mit Alex um 14:00
   3. Rechnungs-Scanner Agent testen"

User: "Neue Aufgabe: Google MCP einrichten, bis Freitag"
→ LLM calls note_write({
    title: "Google MCP einrichten",
    type: "task", status: "open",
    due: "2026-03-28",
    tags: ["mycelos", "mcp"],
    links: ["projects/mycelos"]
  })
→ "Eingetragen: Google MCP einrichten, faellig am 28. Maerz."

User: "Planner Handoff ist fertig"
→ LLM calls note_update({path: "tasks/fix-planner-handoff", status: "done"})
→ "Erledigt! Noch 2 offene Tasks fuer heute."

User: "Was war diese Woche alles erledigt?"
→ LLM calls note_list({type: "task", status: "done", updated_since: "this_week"})
→ Shows completed tasks as summary
```

### Proactive Morning Briefing (with Schedule)

Combined with the scheduler, the LLM can send a daily task overview:

```
Workflow: daily-briefing
Steps:
  1. note_list(type=task, status=open, due=today) → today's tasks
  2. note_list(type=task, status=open, due=overdue) → overdue tasks
  3. search_news(query=user's interests) → relevant news
  4. Summarize → send via Telegram

Schedule: 0 7 * * * (daily at 7:00)
```

This makes the Knowledge Base the central hub for:
- Tasks with due dates
- Project tracking
- Decision log
- Daily journal
- Morning briefing data source

## Open Questions

1. **Folder structure** — flat or hierarchical? Hierarchical (projects/, decisions/, tasks/) feels natural but needs convention.
2. **Auto-tagging** — Should the LLM auto-tag notes based on content? (Cost vs. accuracy)
3. **Conflict with Memory** — Should Memory entries migrate to KB, or coexist? Memory = quick facts, KB = structured content.
4. **Daily Journal** — Auto-generate from session summaries, or manual?
5. **Obsidian compatibility** — Full compatibility (vault format) or just Markdown?
