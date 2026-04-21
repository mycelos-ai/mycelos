---
title: Knowledge Base
description: Automatic and manual notes with full-text search and scoped memory across sessions.
order: 6
icon: database
---

## Automatic notes

Conversations automatically create notes in the knowledge base. Important information, decisions, and context are extracted and stored for future reference — you don't need to ask Mycelos to remember things.

## Writing notes

Ask Mycelos in chat. The assistant has a `note_write` tool it will use when you say things like:

- "Remember that my project deadline is April 15th."
- "Take a note: our staging DB credentials rotated last Friday."
- "Save this snippet as a reference."

Notes are Markdown files under `data/knowledge/` with YAML front-matter (type, status, due, priority, tags). You can also browse, edit, and create them in the web UI under **Knowledge**.

## Reminders

Setting a reminder is a note with `reminder: true`:

- "Remind me tomorrow at 9 to call the accountant."
- "Ping me in 15 minutes about the oven."

Mycelos converts your local time into a stored UTC timestamp (browser timezone is captured once at login) so "tomorrow 9am" fires when you expect, not at UTC midnight.

## Search

Full-text search (SQLite FTS5) plus a semantic layer (sqlite-vec) over the note bodies. Ask in chat — "find my notes about the migration plan" — or use the search bar on the Knowledge page. The `note_search` tool handles both full-text and vector queries.

## Memory scopes

Mycelos keeps state across sessions in four scopes:

- **System** — shared across all agents (read-only for agents)
- **Agent** — isolated per agent (the agent writes its own preferences)
- **Shared** — configurable cross-agent access
- **Session** — temporary, cleared when the session ends

Inspect / edit memory from chat with the `/memory` slash command (see [Slash Commands](/docs/slash-commands)) or from **Settings → Memory** in the web UI.
