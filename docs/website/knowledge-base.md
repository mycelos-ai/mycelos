---
title: Knowledge Base
description: Automatic and manual notes with full-text search and scoped memory across sessions.
order: 6
icon: database
---

## Automatic Notes

Conversations automatically create notes in the knowledge base. Important information, decisions, and context are extracted and stored for future reference.

## Manual Notes

Write notes directly from chat or the Knowledge page:

```bash
/note write "Project deadline is April 15th"
```

## Search

Full-text search powered by SQLite FTS5:

```bash
/note search "deadline"
```

## Memory

Mycelos remembers your preferences across sessions. Memory has four scopes:

- **System** — shared across all agents (read-only for agents)
- **Agent** — isolated per agent
- **Shared** — configurable cross-agent access
- **Session** — temporary, cleared when the session ends
