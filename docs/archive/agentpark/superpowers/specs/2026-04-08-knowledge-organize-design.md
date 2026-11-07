# Knowledge: Auto-Organize, Quick Capture, Smart Import

**Status:** Draft
**Date:** 2026-04-08
**Scope:** UI + lightweight backend on top of the existing knowledge subsystem. No changes to wikilink syntax, mermaid rendering, or the topic-index mechanism.

## Goal

Make the Knowledge subsystem feel alive and structured without requiring the user to manually file every note. Today every note ends up in "Uncategorized" because no topics exist yet and there is no mechanism that emerges them; quick capture is missing; imports do not respect existing structure; the lifecycle of done tasks is unmanaged.

## Non-Goals

These are explicitly out of scope and belong to follow-up specs:

- Backlinks view ("who links to this note")
- Global cross-topic Knowledge Map
- Tag cloud
- Daily recap / activity feed
- Telegram-side rendering of Mermaid diagrams (notes shown in Telegram skip mermaid blocks; PNG rendering via local `mmdc` is a future enhancement)
- Versioned per-note edit history / time travel
- Sync back to Obsidian / external tools
- Inline wikilink insertion into existing prose (the lazy linker only appends, see Section 5)

## Architecture Overview

Six mechanisms, one UI:

1. **Deterministic Bucketing** — when a note is created, choose its parent path from its own fields (sync, no LLM). `due` or `reminder=1` → `tasks/`, otherwise → `notes/`. This is the floor — most notes never need anything more clever than this.
2. **Quick Capture (Cmd+K)** — global hotkey, single text field, save-first. Server bucketing + inline refinement chip on the toast if the organizer is uncertain about type or topic.
3. **Smart Import** — drop a folder/zip of `.md` or `.txt` files. Detection chooses between *preserve* (≥3 folders → 1:1 mirror) or *suggest* (flat → ask the organizer to propose topics).
4. **Lazy Organizer** — background agent (1×/hour, or when ≥10 pending). Classifies, archives, and links notes that lack a parent. Splits into silent moves (high confidence + existing topic) vs. inbox suggestions (everything else).
5. **Lazy Linker** — part of the organizer. When a note is classified, the LLM may also identify thematically related existing notes; those become append-only `[[wikilink]]` suggestions in the inbox.
6. **Topic Map (auto-Mermaid)** — topic-index notes get a generated mermaid graph as a code block at the top, showing sub-notes and their wikilink connections. Emitted as part of `_build_topic_index` so it lives in the markdown itself.

What stays exactly as it is today:

- Wikilink syntax `[[path|alias]]` and `_extract_wikilinks` (`knowledge/service.py:660`)
- Mermaid rendering in chat and knowledge pages (`mermaid.min.js` + `<pre class="mermaid">` blocks)
- The `note_link` tool (`tools/knowledge.py:292`)
- Topic-index generation via `_build_topic_index` (`service.py:732+`) — extended, not replaced
- The Knowledge page layout in broad strokes — only specific sections are touched

## Data Model

Migration `v3` adds two columns to `knowledge_notes` and one new table:

```sql
ALTER TABLE knowledge_notes ADD COLUMN organizer_state TEXT
    NOT NULL DEFAULT 'pending';
    -- pending  : not yet seen by the organizer
    -- ok       : organizer has reviewed, current location stands
    -- archived : lifecycle removed (done > 7d, fired reminder)
ALTER TABLE knowledge_notes ADD COLUMN organizer_seen_at TEXT;

CREATE TABLE organizer_suggestions (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    note_path    TEXT NOT NULL REFERENCES knowledge_notes(path) ON DELETE CASCADE,
    kind         TEXT NOT NULL,
        -- 'move'        : place this note under target topic
        -- 'new_topic'   : create a topic with this name and place the note there
        -- 'link'        : append a wikilink to this related note (target_path)
        -- 'refine_type' : type is ambiguous, here are the alternatives
    payload      TEXT NOT NULL,    -- JSON {target, alternatives, reason}
    confidence   REAL NOT NULL,    -- 0.0 .. 1.0
    created_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    status       TEXT NOT NULL DEFAULT 'pending'
        -- pending | accepted | dismissed
);
CREATE INDEX idx_organizer_pending
    ON organizer_suggestions(status) WHERE status = 'pending';
```

Migration sets `organizer_state = 'pending'` for every existing note, so the organizer picks them up on its first run after upgrade. `confidence` is a float to allow tuning the silent-vs-inbox threshold (currently `0.8`) without a schema change.

## Section 1: Deterministic Bucketing

A new helper `bucket_note(note: dict) -> str` lives in `knowledge/service.py` and is called by every code path that creates a note (`note_write` tool, the new `/api/knowledge/notes` POST, the import pipeline). The rule is intentionally trivial:

```python
def bucket_note(note: dict) -> str:
    """Decide the parent_path for a fresh note. No LLM, no I/O."""
    if note.get("reminder"):
        return "tasks"
    if note.get("due"):
        return "tasks"
    return "notes"
```

Bucketing only fires when `parent_path` is empty. If the caller (or an import) already supplies a `parent_path`, that wins. Bucketing also flips `organizer_state` to `pending`, so the organizer revisits it later for finer placement.

## Section 2: Quick Capture (Cmd+K)

### UI

Cmd+K (Mac) / Ctrl+K (Win/Linux) opens a floating modal with a single text field, Esc closes it without saving. The component lives in a new `frontend/shared/quick-capture.html` partial, included by every page that uses the standard sidebar layout (chat, knowledge, workflows, connectors, settings, agents, dashboard).

While typing, a small inline chip row updates with what the client-side parser detected so far: type (`note` / `task` / `reminder`), parsed due-date phrase, hashtags, wikilinks. No API call during typing.

Enter posts the note to `POST /api/knowledge/notes` with `{title, content, parsed_due, parsed_tags}`. The server runs the same `bucket_note` helper as the tool path. The response includes the final path **and** any organizer suggestions with confidence ≤ 0.8 that were generated synchronously (only for the obvious cases — see below).

A toast appears bottom-right: "Saved to `tasks/social/coffee-with-lisa`". If the response carried refinement suggestions, the toast grows a chip row underneath: "Maybe → `projects/mycelos` · → new topic · ✕". Click adopts the suggestion via PATCH; ✕ dismisses it (the row is removed but the suggestion stays in the inbox in case the user changes their mind). Toast auto-dismisses after 8 s; the underlying suggestion lives on in the inbox until the user acts on it.

### Live parser (client-side, deterministic)

A small JS module in `frontend/shared/parse-note.js` exports `parseNoteText(text)` returning `{type, due, tags, wikilinks}`. **The parser must handle German and English equally** — every rule below is specified in both languages, and the unit tests cover parallel DE/EN inputs for each branch. Rules:

- **Date/time** — chrono-style regexes for German + English: "in 5 minuten" / "in 5 minutes", "morgen 14 uhr" / "tomorrow 2pm", "nächste woche dienstag" / "next thursday", "14.04. 15:00" / "04/14 3pm", "übermorgen" / "day after tomorrow", weekday names in both languages. When something matches, `due` is set as ISO and `type` defaults to `task`.
- **Reminder phrases** — "erinner mich" / "erinnere mich" / "remind me", "in N min/std/h" / "in N min/hours", set `reminder=true` and `type=task`.
- **Type heuristic** — leading "TODO" / "FIXME" / "AUFGABE" → task; otherwise → note (unless a date matched).
- **Tags** — `#word` chunks become tags.
- **Wikilinks** — `[[path]]` chunks are noted but stored verbatim in the body; the existing wikilink indexer takes care of them.

The same parser is used in the toast preview and on the server side for the parsed_due field — but the server is allowed to disagree (and overwrite) if the client misparsed. Server is the source of truth. The server-side port lives in `knowledge/parse_note.py` and must stay behaviourally in sync with the JS module — shared test vectors in `tests/fixtures/parse-note-vectors.json` are loaded by both the Python unit tests and the Playwright tests to guarantee that.

### Discoverability

The sidebar gets a small footer hint right under the Recent Sessions block: `⌘K · Quick Capture`, dimmed, no border. Discoverable but quiet. The first-run onboarding flow will mention it explicitly — that lives in a separate onboarding-refresh spec.

## Section 3: Smart Import

### Entry points

- **Drag & drop** of files or a folder onto the Knowledge page (and only that page)
- **Import button** in the top-right of the topic tree column

Both open the same modal: a file picker, a primary "Importieren" button, and a small `Andere Optionen…` link that expands to three radios (Smart default · Struktur 1:1 · Mycelos neu organisieren).

### Detection

```python
def detect_import_mode(uploaded_files: list[FileEntry]) -> str:
    folders = {f.relpath.rsplit('/', 1)[0]
               for f in uploaded_files if '/' in f.relpath}
    return "preserve" if len(folders) >= 3 else "suggest"
```

`uploaded_files` is the result of unzipping (if zip) or recursively walking a dropped folder.

### Mode `preserve`

- For each `.md` or `.txt` in the source: target path is `topics/<lowercased-folder-chain>/notes/<safe-filename>`.
- Folder chains become topic-index notes (created if missing) — `_build_topic_index` is run once per chain after the import finishes.
- Frontmatter (`title`, `tags`, `aliases`) is parsed and lifted out of the body for `.md` files. `.txt` files become notes with their content as-is, no parsing.
- `organizer_state = 'ok'` for everything imported in preserve mode (the source already had structure).
- No LLM calls. Pure file mapping. Path collisions append `-1`, `-2`, etc.

### Mode `suggest`

- Files land under `imports/<YYYY-MM-DD>/<safe-filename>` with `organizer_state='pending'`.
- Immediately after the file copy, a one-shot `Organizer.classify_batch(paths)` call runs synchronously: it sends the LLM a compact list (title + first 200 chars of each note) and asks for a topic-suggestion grouping.
- The result is written as `organizer_suggestions` rows (kind `move` or `new_topic`).
- The user is redirected to the Knowledge page where the inbox now shows: "Import 8. April: 47 Notes → 6 vorgeschlagene Topics" with `Alle akzeptieren` / `Einzeln verfeinern` / `Alle ablehnen`.

### What import does not do

- No image handling
- No conflict-merge (collisions get suffixes)
- No round-trip sync — Mycelos owns its copy after import
- No file watcher on the source
- No org-mode, RTF, or HTML conversion

## Section 4: Lazy Organizer

### Where it runs

A new system handler `agents/handlers/knowledge_organizer_handler.py` registered in the agent registry as `knowledge-organizer` (hidden from the sidebar like `evaluator-agent`). Model: Haiku — classification is a cheap task. It is invoked by the existing background-task scheduler:

- **Periodic trigger** — every 60 minutes
- **Pressure trigger** — whenever `COUNT(WHERE organizer_state='pending') >= 10`

Both triggers funnel into the same single-run function. A run never overlaps itself (mutex via storage flag).

### Per-run loop

```
for note in pending_notes_limited_to_30():
    # Lifecycle first — pure SQL, no LLM
    if is_done_task_older_than_7d(note):
        move_to_archive(note); set organizer_state='archived'; continue
    if is_fired_reminder_in_past(note):
        soft_delete(note); set organizer_state='archived'; continue

    # Classification
    result = llm_classify(note, current_topics, neighbors=5)
    # result = {topic_path, confidence, related_note_paths, new_topic_name?}

    if result.confidence >= 0.8 and topic_exists(result.topic_path):
        move_silently(note, result.topic_path)
        set organizer_state='ok'
        audit_log("organizer.move", from=note.path, to=...)
    elif result.confidence >= 0.8 and result.new_topic_name:
        suggestion(kind='new_topic', payload={name, members:[note.path]})
    else:
        suggestion(kind='move', payload={target, alternatives})

    # Lazy Linker — only when classification succeeded
    for related in result.related_note_paths or []:
        suggestion(kind='link', payload={from: note.path, to: related})

    set organizer_seen_at = now()
```

Batch limit 30 per run keeps a single invocation under control. Anything left over waits for the next trigger.

### Lifecycle (always silent)

- **Done tasks > 7 days old** → move to `archive/<year>/<original-bucket>/<filename>`, `organizer_state='archived'`. Still in DB, hidden from default views.
- **Reminders that fired and whose `due` is more than 1 day in the past** → `status='archived'`. Same DB-soft-delete.

Both are pure deterministic SQL with `freezegun`-friendly time math, fully unit-testable.

### Threshold

`SILENT_CONFIDENCE = 0.8` is a constant in `knowledge/organizer.py`. Adjustable at code level; not exposed to users in this spec.

## Section 5: Organizer Inbox UI

A new collapsible card lives at the top of `/pages/knowledge.html`, **above** the topic tree and visible only when at least one suggestion has `status='pending'`. It is grouped by `kind`:

```
┌─ Organizer Inbox ─────────────────────────────────┐
│  📁 6 notes want a different topic                │
│     ▸ "Coffee with Lisa"  →  social/              │
│     ▸ "Mycelos UI Idea"   →  projects/mycelos/    │
│     ▸ … 4 more                                    │
│     [ Akzeptieren ]  [ Einzeln durchgehen ]       │
│                                                    │
│  ✨ 2 new topics proposed                          │
│     "Coffee Stuff"  ·  3 notes                    │
│     [ Topic anlegen ]  [ Verwerfen ]              │
│                                                    │
│  🔗 4 link suggestions                             │
│     "Lisa Note"  ↔  "Coffee with Lisa"            │
│     [ Akzeptieren ]  [ Verwerfen ]                │
└────────────────────────────────────────────────────┘
```

`Einzeln durchgehen` opens a small modal wizard that walks one suggestion at a time with `accept` / `dismiss` / `customize` (customize lets the user pick a different target from a dropdown).

Lazy-linker accept inserts a wikilink into the source note's body. Insertion is **append-only** under a `## Verwandt` heading at the bottom of the note. The user's existing prose is never edited inline. If the heading already exists, the new link is appended below it as a bullet.

The inbox does not push notifications, does not auto-apply on a timer, and does not nag if ignored. It is a passive surface.

## Section 6: Topic Map (auto-Mermaid)

`_build_topic_index(topic_path)` is extended to emit a fenced mermaid block before the existing list. The graph is generated by a sibling helper `_build_topic_mermaid(topic_path)`:

```python
def _build_topic_mermaid(topic_path: str) -> str:
    """Return a mermaid graph of sub-notes and their wikilink edges.
    Returns '' for topics with no sub-notes (caller skips the block)."""
```

Layout:

- Topic node in the centre, labelled with the topic title
- Each sub-note becomes a child node
- For each wikilink between sub-notes, an edge between the two child nodes
- ≤15 sub-notes: graph is open by default
- ≥16 sub-notes: graph is wrapped in a `<details>` collapsed by default to reduce noise

Because the graph lives in the topic-index markdown, Mycelos sees it when it reads the topic via `note_read` — the LLM can reason about structure. The browser renders it via the existing `mermaid.run()` path in `knowledge.html`. Manually-authored mermaid blocks in any other note are unaffected.

Telegram does not render mermaid; the bot strips the block when it shows a topic note and replaces it with `📊 Diagramm — Web öffnen`. Local PNG rendering via `mmdc` is future work, not part of this spec.

## Section 7: Knowledge Page UI changes

Concrete diffs to `/pages/knowledge.html`:

1. **Organizer Inbox** card at the top, visible when `pending suggestions > 0` (Section 5)
2. **Topic tree** — `Uncategorized` is renamed to **Pending** with a count badge. Pending lists notes where `organizer_state='pending'`. As soon as the organizer classifies a note (silently or via accepted suggestion) it leaves Pending.
3. **Empty state** — when nothing is pending and no topics exist yet, show a friendly hint: "Drücke `⌘K` für deine erste Notiz" — not "Uncategorized (0)".
4. **`⌘K · Quick Capture` hint** in the sidebar footer (Section 2)
5. **`## Verwandt` section** appears in note views when the user has accepted lazy-linker suggestions
6. **Import** button top-right of the topic tree column. Drag-drop on the page triggers the same modal (Section 3)

Nothing else on the page is restructured. Search, filters, the existing detail pane, and the existing markdown editor stay as they are.

## Section 8: API surface

New endpoints (added to `gateway/routes.py`):

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/api/knowledge/notes` | Create note via bucketing. Returns final path + any inline suggestions. |
| `POST` | `/api/knowledge/import` | Multipart upload. Body field `mode` ∈ `auto/preserve/suggest`. Returns import summary + redirect target. |
| `GET` | `/api/organizer/suggestions` | List `status='pending'` suggestions, grouped by `kind`. |
| `POST` | `/api/organizer/suggestions/{id}/accept` | Apply suggestion (move note, create topic, append wikilink). Marks suggestion `accepted`. |
| `POST` | `/api/organizer/suggestions/{id}/dismiss` | Marks suggestion `dismissed`. No data change. |
| `POST` | `/api/organizer/run` | Force a run now (used by the doctor command and tests). |

Existing endpoints keep working unchanged: `/api/knowledge/notes` GET (search), `/api/knowledge/notes/{path}` GET (read), `/api/notifications/pending`, `/api/reminders/upcoming`.

## Section 9: Testing strategy

### Unit tests (`tests/test_knowledge_*.py`)

- `bucket_note` — every branch (reminder, due, neither, both, parent_path already set)
- `_extract_wikilinks` — keep existing tests green
- `parse_note` — shared DE/EN vectors from `tests/fixtures/parse-note-vectors.json`: every date/reminder/type rule has a parallel German and English case, plus one "neither matches" fallback per rule
- `detect_import_mode` — 0/1/3/10/mixed folders
- Lifecycle helpers under `freezegun`: done-task >7d → archive, fired reminder past → soft-delete, neither boundary off-by-one
- `_build_topic_mermaid` — empty, 1 sub-note, 5 sub-notes with cross-wikilinks; result is valid mermaid syntax
- Confidence branching on `organizer.classify_one(...)`: silent vs inbox

### Integration tests (`tests/integration/test_organizer_live.py`)

- End-to-end run against real Haiku, recorded into the existing cassette infrastructure (the organizer goes through `LLMBroker.complete`, so it just works)
- 5-note seed → expected `organizer_state` per note + at least one suggestion of each kind that is supposed to fire
- Smart Import end-to-end with two fixtures: `tests/fixtures/obsidian-vault/` (3 folders, structured) → preserve; `tests/fixtures/flat-notes/` (10 .md flat) → suggest with pending suggestions

### API tests (`tests/test_gateway.py` new class `TestOrganizerEndpoints`)

- POST `/api/knowledge/notes` returns final path
- POST `/api/knowledge/import` honours `mode` body field; preserve vs suggest branches both produce the right counts
- GET `/api/organizer/suggestions` returns grouped pending list
- POST `/api/organizer/suggestions/{id}/accept` moves the note and flips status
- POST `/api/organizer/suggestions/{id}/dismiss` only flips status, leaves note alone

### Frontend tests (Playwright `tests/e2e/test_quick_capture.py`)

- Cmd+K opens the modal from chat, knowledge, workflows, connectors pages
- Type + Enter shows the toast with a path and posts to the API
- Esc closes without saving
- Drag-drop a `.md` file on knowledge.html opens the import modal

### Migration test (`tests/test_storage.py`)

- v3 migration runs cleanly on a v2 fixture DB
- All existing notes get `organizer_state='pending'`
- `organizer_suggestions` table exists with the expected schema and index

### What we don't test

- LLM classification quality as a property test (drift-prone). One smoke cassette is enough.
- Mermaid render output (handled by mermaid.js)
- Concurrency for two simultaneous note writes (out of scope)

## Section 10: Migration plan

1. Add the v3 migration to `storage/database.py` (append, never edit older migrations).
2. Run migration on startup; existing notes flip to `organizer_state='pending'`.
3. The first organizer run after upgrade processes them in batches of 30 every hour or whenever pressure builds, until the queue drains.
4. The user sees the inbox fill up with suggestions (as expected — they have notes that were never classified before). The empty state of "Uncategorized" goes away as soon as the first batch is processed.

No data loss is possible because the migration only adds columns and a table, and the organizer never hard-deletes anything. The doctor command gets a new check `--check organizer` that reports the size of the pending queue and the last run timestamp.

## Section 11: Constitution compliance

- **Audit everything** — every silent move, archive, and accepted/dismissed suggestion writes an `audit_event` (`organizer.move`, `organizer.archive`, `organizer.suggestion.accepted`, `organizer.suggestion.dismissed`)
- **Config generation on state change** — none needed; this spec touches user data, not declarative config. No new connectors, agents (the organizer is internal), policies, mounts, or channels are introduced as configurable.
- **Security fail-closed** — the organizer runs as a system handler with the same scoped tools as today's reminder service. No new credentials, no new outbound network. LLM calls go through the broker (which goes through the proxy) — no widening.
- **Credentials never visible** — no credential surface here.
- **Capability scoping** — the new `knowledge-organizer` agent declares its capabilities (`knowledge.read`, `knowledge.write`, `llm.complete`) and is bound to them.
- **English everywhere in code** — all class names, comments, audit event types, log messages, and tests are English. User-facing strings go through `t()`.

## Follow-up work (explicitly out of scope)

- Backlinks panel and per-note "incoming links" view
- Global Knowledge Map across topics
- Tag cloud and tag-based filtering
- Daily digest / weekly recap
- Telegram-side mermaid PNG rendering via local `mmdc`
- Versioned note history
- Onboarding refresh that introduces Cmd+K, the inbox, and the import flow
- Inline wikilink insertion in existing prose (currently append-only)
- Confidence threshold UI and per-user tuning
- Knowledge sync to Obsidian/external tools
- **Image and PDF capture via vision models** — drop a photo, screenshot, or PDF into Quick Capture / Smart Import; a vision model (e.g. Sonnet vision) extracts text + structure and turns it into a note with attachments. Blocked on: attachment storage layout, vision-aware classification in the organizer, Telegram-side image forwarding. To be designed as its own spec once the text-only flow is stable.

## Risks and mitigations

- **The organizer mis-classifies frequently** → mitigation: pessimistic threshold (0.8), inbox-by-default, every action is auditable, dismissed suggestions stay reviewable.
- **Inbox grows faster than the user can review** → mitigation: `Akzeptieren` bulk actions per group, batch import suggestions are grouped in one card, no nagging.
- **Lazy linker pollutes notes with bad links** → mitigation: append-only at the bottom, separate `## Verwandt` heading, accept-per-link granularity.
- **Smart import misjudges the structure** → mitigation: the `Andere Optionen…` escape hatch in the import modal, and the suggest mode is reversible (every move is in the audit log).
- **Performance with thousands of notes** → mitigation: batch limit of 30 per organizer run, indexed `pending` query, suggestions table has a partial index on `status='pending'`.
