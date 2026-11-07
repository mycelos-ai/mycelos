# Knowledge System v2 — Smart Zettelkasten

> **Week:** 13 (2026)
> **Status:** Draft
> **Priority:** High — core daily-use feature

## 1. Vision

A personal knowledge system that grows organically. User throws in notes via chat or web — the system auto-organizes by topic, reminds about tasks, and makes everything findable.

**Not a file manager.** Not Notion. Think: intelligent sticky notes that arrange themselves on a wall and tap you on the shoulder when something is due.

## 2. Core Principles

1. **Zero-friction input** — "Notier: Milch kaufen" → done. No forms, no categories to pick.
2. **Auto-organize on insert** — When a note comes in, immediately check: does it fit an existing topic? If yes, link it. If no, it's a new topic seed.
3. **Hierarchical but flexible** — Topics emerge from content, not from predefined folders. User can override.
4. **Meaningful titles** — LLM generates descriptive titles. Path encodes hierarchy: `projects/steuerberater-app/buchhaltung-modul`.
5. **Reminders that work** — Due dates + reminder flag → Telegram/Web notification.

## 3. Data Model Changes

### knowledge_notes (updated)
```sql
ALTER TABLE knowledge_notes ADD COLUMN reminder BOOLEAN DEFAULT FALSE;
ALTER TABLE knowledge_notes ADD COLUMN parent_path TEXT;  -- e.g., "projects/steuerberater-app"
ALTER TABLE knowledge_notes ADD COLUMN sort_order INTEGER DEFAULT 0;
```

- `reminder` — if TRUE and `due` is set, trigger notification
- `parent_path` — link to parent topic note (NULL = top-level)
- `sort_order` — manual ordering within a topic

### Topic Notes
A topic is just a note with `type = 'topic'`. Its path is the parent_path for child notes:
```
Path: "projects/steuerberater-app"
Type: topic
Title: "Steuerberater-App"
Content: Auto-generated index of child notes
```

## 4. Auto-Organization (on insert)

When a new note is created (via chat, workflow, or web):

### Step 1: Title Generation
If no title given, LLM generates one:
```
Input: "Morgen muss ich Milch und Brot kaufen"
→ Title: "Einkaufsliste: Milch und Brot"
→ Type: task
→ Due: tomorrow
```

### Step 2: Topic Matching
Tool `classify_note` (Haiku, ~$0.001):
```
Input: note title + content + existing topics list
Output: {
  "topic": "einkauf" | "projects/steuerberater-app" | "__new__",
  "new_topic_name": "Einkäufe",  // only if __new__
  "suggested_tags": ["einkauf", "lebensmittel"],
  "suggested_type": "task",
  "due_date": "2026-03-29",
  "reminder": true
}
```

The LLM gets the current topic list as context — so it knows what exists and can match or suggest new ones.

### Step 3: Link + Store
- If topic exists → set `parent_path`, add to `knowledge_links`
- If new topic → create topic note, then link
- Tags from LLM merged with any user-provided tags

## 5. Topic Structure

### How Topics Emerge
```
Day 1: "Idee: KI-gestütztes Steuerberatungsprogramm" → new topic "Steuerberatung"
Day 2: "Steuerberatungs-App Buchhaltungsmodul" → auto-linked to "Steuerberatung"
Day 3: "Meeting mit Steuerberater Müller" → auto-linked to "Steuerberatung"
```

### Topic Index (auto-generated)
Each topic note gets auto-updated content:
```markdown
# Steuerberatung

## Notes (3)
- [Idee: KI-gestütztes Steuerberatungsprogramm](notes/idee-ki-...)
- [Buchhaltungsmodul](notes/buchhaltung-modul)
- [Meeting mit Müller](notes/meeting-mueller)

## Tasks (1)
- [ ] Angebot erstellen (due: next week)

## Tags
steuerberatung, ki, app-entwicklung, buchhaltung
```

### Title Convention
Titles should be descriptive and encode context:
- Good: "Steuerberater-App: Buchhaltungsmodul Design"
- Bad: "Notiz 3"

The LLM is instructed to generate titles in the format:
`[Topic]: [Specific Subject]` when a topic is identified.

## 6. Tasks & Reminders

### Task Lifecycle
```
open → in_progress → done → archived
```

### Reminder System
- Note has `due` date + `reminder = true`
- Daily scheduled workflow "check-reminders":
  1. Query: `SELECT * FROM knowledge_notes WHERE due <= ? AND reminder = TRUE AND status != 'done'`
  2. For each due/overdue: send notification via Telegram + Web push
  3. Mark as notified (don't spam — once per due date)

### Lists (Shopping, Todo, etc.)
A list is just a topic note with child tasks:
```
Topic: "Einkaufsliste" (type: topic)
  ├── Milch kaufen (type: task, status: open)
  ├── Brot kaufen (type: task, status: open)
  └── Butter (type: task, status: done)
```

Checked off via: "Milch erledigt" in chat → status = done.

## 7. Search Improvements

### Current: FTS5 + Prefix
Works for: "steuer" → finds "Steuerberatung"

### Add: Trigram Index
For fuzzy/typo search: "eliah" → finds "Elias"
```sql
-- SQLite doesn't have trigrams natively.
-- Option A: Use LIKE with wildcards (current fallback)
-- Option B: Build trigram index in Python at insert time
-- Option C: Use sqlite-vec for semantic search (already partially implemented)
```

### Search via Tools
The `note_search` tool should support:
```
note_search("steuerberatung")           → FTS5 prefix match
note_search("meeting letzte woche")     → semantic / date-based
note_search(type="task", status="open") → filtered list
note_search(due="overdue")              → tasks past due date
```

## 8. Web UI Changes

### Knowledge Page Layout
```
┌─────────────────────┬──────────────────────────────┐
│ Topics              │ Note Content                 │
│                     │                              │
│ 🔍 Search...       │ # Steuerberater-App          │
│                     │                              │
│ 📁 Steuerberatung  │ ## Buchhaltungsmodul         │
│   📄 Idee          │ Das Modul soll...            │
│   📄 Buchhaltung   │                              │
│   📄 Meeting       │                              │
│                     │                              │
│ 📁 Reisen          │                              │
│   ☐ Elias Reise    │                              │
│                     │                              │
│ 📁 Einkäufe        │                              │
│   ☐ Milch          │                              │
│   ☑ Butter         │                              │
│                     │                              │
│ 📋 Uncategorized   │                              │
│   📄 Summarize     │                              │
└─────────────────────┴──────────────────────────────┘
```

### Task Views
- "My Tasks" filter: shows only open tasks, sorted by due date
- Overdue highlighted in red
- Checkbox to mark done inline
- Reminder bell icon to toggle notifications

## 9. Tools (Chat Integration)

### Updated Tools
```
note_write(title, content, type?, tags?, due?, reminder?, topic?)
note_search(query?, type?, status?, due?)
note_done(path)          — mark task as done
note_remind(path, when)  — set reminder
note_move(path, topic)   — move to different topic
```

### Smart Input Examples
```
User: "Notier: Morgen Milch kaufen"
→ note_write(title="Milch kaufen", type="task", due="2026-03-29",
   reminder=true, topic="einkauf")

User: "Idee: Man könnte einen Chatbot für Steuerberater bauen"
→ note_write(title="Steuerberater-App: Chatbot-Idee", type="note",
   topic="projects/steuerberater-app", tags=["idee", "chatbot"])

User: "Milch erledigt"
→ note_done("tasks/milch-kaufen")

User: "Erinner mich am Freitag an das Angebot"
→ note_remind("tasks/angebot-erstellen", "2026-03-28")
```

## 10. Built-in Workflows

### knowledge-organizer (daily)
1. Scan notes without parent_path
2. LLM: classify each into existing topic or suggest new
3. Update links + parent_path
4. Regenerate topic index notes
5. Report: "Organized 2 new notes, created 1 new topic"

### check-reminders (every 5 min)
1. Query due+reminder notes
2. Send notification via Telegram/Web
3. Mark as notified

## 11. Note-Intake Workflow (built-in, customizable)

The core workflow that runs every time a note is created. Ships as default,
user can customize in Settings or via Builder.

### Workflow: `note-intake`
```yaml
id: note-intake
name: Note Intake
description: Classify, organize, and store incoming notes
steps:
  - id: classify
    action: classify_note   # LLM: extract type, due, topic, tags, title
    description: Analyze the raw input and extract metadata
  - id: store
    action: note_write      # Save with all extracted metadata
    description: Store the note with title, type, tags, due, topic
  - id: link
    action: link_to_topic   # Find or create topic, set parent_path
    description: Auto-link to existing topic or create new one
  - id: notify
    action: confirm         # Tell the user what was stored
    description: Confirm to user with summary
```

### Execution Flow
```
User: "Notier: Morgen Milch kaufen"
  → Mycelos recognizes note-taking intent
  → Mycelos calls run_workflow("note-intake", {text: "Morgen Milch kaufen"})
  → WorkflowRunner executes steps:
    1. classify_note → {type: "task", due: "2026-03-29", topic: "einkauf",
                        title: "Milch kaufen", tags: ["einkauf"], reminder: true}
    2. note_write → saved to DB
    3. link_to_topic → linked to "Einkäufe" topic (created if new)
    4. confirm → "Notiert! 'Milch kaufen' unter Einkäufe, fällig morgen."
```

### Customization
User can modify the workflow:
- Remove `link` step → flat notes, no auto-organization
- Remove `classify` step → manual metadata only
- Add steps: e.g., `search_existing` to check for duplicates first

## 12. Web UI — Browse by Topic + Tags

### Rule: Every note belongs to a topic
Top-level in the Knowledge browser is always a topic, never a loose note.
New notes without a topic go to "Uncategorized" until the organizer runs.

### Topic Tree (left panel)
```
📁 Topics
  📁 Steuerberatung (3)
  📁 Einkäufe (2)
  📁 Reisen (1)
  📁 Workflows (2)
  📁 Uncategorized (1)

📋 Tasks (overdue: 1)
  ☐ Milch kaufen — morgen
  ☐ Elias Reise — 29.3.

🏷️ Tags
  steuerberatung (3)  ki (2)  einkauf (2)  reise (1)  ...
```

### Tag Navigation
- Click a tag → filter notes by that tag (across all topics)
- Tag cloud or tag list in the sidebar (sorted by frequency)
- Tags are clickable in note detail view too

### Task View
- Dedicated "Tasks" section in sidebar: shows all open tasks sorted by due
- Overdue tasks highlighted red
- Checkbox to mark done inline
- Bell icon to toggle reminder

## 13. Implementation Priority

1. **Schema changes** — parent_path, reminder, sort_order columns
2. **Auto-classify on insert** — LLM topic matching in note_write tool
3. **Topic index generation** — auto-generated topic notes
4. **Reminder workflow** — scheduled check + Telegram notification
5. **Web UI** — topic tree in left panel, task checkbox, reminder toggle
6. **Search improvements** — trigram fallback, semantic search
7. **Smart input** — LLM extracts due dates, types, topics from free text

## 14. Non-Goals (v2)

- Collaborative editing (single-user for now)
- File attachments (separate feature)
- Calendar integration (future)
- Export to Obsidian/Notion format (future, but path structure is compatible)
