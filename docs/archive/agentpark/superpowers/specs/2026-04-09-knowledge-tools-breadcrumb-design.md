# Knowledge Tools + Breadcrumb

**Status:** Draft
**Date:** 2026-04-09
**Scope:** New chat-agent tools for knowledge management (CRUD gaps + relationship queries), a breadcrumb path on the note detail view, and a 30-day archive lifecycle. All new tools integrate into the Lazy Tool Discovery category system.

## Goal

Mycelos can create and search notes but cannot rename, merge, or delete topics, cannot archive notes, and cannot show relationships between notes. The note detail view has no indication of where a note lives in the topic tree. This spec closes those gaps.

## Non-Goals

- Embedding-based semantic search (future — FTS5 is sufficient for V1)
- Weekly review / knowledge stats with growth trends (future)
- Automatic redirect resolution in the UI (future)
- Wikilink rewriting on merge (explicitly out of scope — soft-merge with redirect instead)
- Bulk operations (multi-select notes, batch move/archive)

## Section 1: Breadcrumb Path on Note Detail View

### UI Change

When a note is selected in the right panel of `pages/knowledge.html`, the content header bar (above the note title) shows a clickable breadcrumb derived from the note's `parent_path`:

```
📁 Knowledge / Kaffee / Rezepte
```

- Each segment is clickable — clicking a segment opens that topic in the left panel (`expandedTopic = segment_path`)
- The breadcrumb is built by splitting `parent_path` on `/` and title-casing each segment (replacing dashes with spaces)
- If `parent_path` is empty or `"notes"` / `"tasks"`, show just `📁 Notes` or `📋 Tasks` (the bucket name)
- The note's own title is NOT part of the breadcrumb (it's already shown as the `<h1>` below)

### Implementation

In `src/mycelos/frontend/pages/knowledge.html`, in the content header bar (around the `<h1>` that shows `selectedNote.title`), insert above it:

```html
<div x-show="selectedNote && selectedNote.parent_path"
     class="flex items-center gap-1 text-[11px] mb-1"
     style="color: var(--on-surface-variant)">
  <template x-for="(seg, i) in buildBreadcrumb(selectedNote)" :key="i">
    <span class="flex items-center gap-1">
      <span x-show="i > 0" class="opacity-40">/</span>
      <button @click="navigateToBreadcrumb(seg.path)"
              class="hover:text-[var(--primary)] transition-colors"
              x-text="seg.label"></button>
    </span>
  </template>
</div>
```

Add `buildBreadcrumb(note)` and `navigateToBreadcrumb(path)` methods to the `knowledgeApp()` component:

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
  this.toggleTopic({ path: topicPath });
},
```

## Section 2: New Tools — CRUD Gaps

All tools in this section are registered in the `knowledge_manage` Discovery category.

### `topic_rename`

Renames a topic: updates the topic note's title, renames the on-disk markdown file, updates `parent_path` of all children, and updates the DB path.

```
topic_rename(path="topics/kaffee", name="Getränke")
→ {status: "renamed", old_path: "topics/kaffee", new_path: "topics/getraenke"}
```

**Implementation:** New method `KnowledgeBase.rename_topic(path, new_name)`:
1. Compute new path from new name slug
2. Rename the .md file on disk
3. Update DB: `UPDATE knowledge_notes SET path=? WHERE path=?` (the topic note itself)
4. Update DB: `UPDATE knowledge_notes SET parent_path=? WHERE parent_path=?` (all children)
5. Rename children's .md files if their path contains the old topic path as prefix
6. Audit event: `knowledge.topic.renamed`

### `topic_merge`

Soft-merge: moves all children from source to target, then converts source into a redirect note.

```
topic_merge(source="topics/kaffee", target="topics/getraenke")
→ {status: "merged", moved: 5, redirect: "topics/kaffee"}
```

**Implementation:** New method `KnowledgeBase.merge_topics(source, target)`:
1. `list_children(source)` → for each child: `move_to_topic(child.path, target)`
2. Update the source topic note's content to `# <old title>\n\n→ Moved to [[<target>]]`
3. Source topic note stays in DB as type=topic (acts as a redirect for old wikilinks)
4. Audit event: `knowledge.topic.merged`

Wikilinks are NOT rewritten. The redirect note serves as a human-readable pointer.

### `topic_delete`

Deletes an empty topic (no children). Refuses if children exist — the user must move or delete them first.

```
topic_delete(path="topics/alte-ideen")
→ {status: "deleted"} or {error: "topic has 3 children — move them first"}
```

**Implementation:** New method `KnowledgeBase.delete_topic(path)`:
1. `list_children(path)` → if not empty, return error
2. Delete the .md file on disk
3. Delete the DB row: `DELETE FROM knowledge_notes WHERE path=?`
4. Audit event: `knowledge.topic.deleted`

### `note_archive`

Archives a note: sets `status='archived'`, disappears from all views and searches.

```
note_archive(path="notes/old-idea")
→ {status: "archived"}
```

**Implementation:** Uses existing `note_update` path or a new convenience method:
1. `UPDATE knowledge_notes SET status='archived', organizer_state='archived' WHERE path=?`
2. The note stays on disk and in DB — just hidden from queries
3. Audit event: `knowledge.note.archived`

The Organizer lifecycle sweep (Task in Milestone B) already handles archived notes. This spec extends it: **archived > 30 days → hard delete** (see Section 4).

### `note_delete`

Not implemented as a separate tool. Archiving is the only user-facing delete. Hard deletion happens automatically after 30 days via the Organizer sweep. This prevents accidental data loss.

## Section 3: New Tools — Relationship Queries

### `find_related`

Finds semantically similar notes using the existing `KnowledgeBase.find_relevant(text, top_k)` method (FTS5-based).

```
find_related(path="notes/espresso-tuning")
→ {related: [{path: "notes/grinder-settings", title: "Grinder Settings", score: 0.85}, ...]}
```

**Implementation:**
1. Read the source note's title + first 200 chars of body from disk
2. Call `kb.find_relevant(title + " " + body_snippet, top_k=5)`
3. Filter out the source note itself from results
4. Return ranked list

Registered in `knowledge_read` category (basis-set candidate, not Discovery).

### `topic_overview`

Returns a structured overview of a topic: title, child count, list of children with types and statuses, and any sub-topics.

```
topic_overview(path="topics/kaffee")
→ {title: "Kaffee", children: [...], sub_topics: [...], open_tasks: 2, total: 8}
```

**Implementation:**
1. `kb.list_children(path)` → group by type
2. Count open tasks, done tasks, notes
3. Check for sub-topics (children that are themselves type=topic)
4. Return structured dict

Registered in `knowledge_read` category.

## Section 4: Archive Lifecycle — 30-Day TTL

### Organizer Sweep Extension

The existing lifecycle sweep in `KnowledgeOrganizerHandler.run()` gets a new rule after the "fired reminder > 1d → archive" check:

```python
# Hard-delete notes archived for more than 30 days
if note.get("status") == "archived":
    archived_at = _parse_iso(note.get("organizer_seen_at") or note.get("updated_at"))
    if archived_at and (now - archived_at) >= timedelta(days=30):
        hard_delete(note)  # Remove from DB + disk
        audit("organizer.hard_delete", path=note["path"])
        continue
```

The `hard_delete` helper:
1. Delete the .md file from `_knowledge_dir`
2. `DELETE FROM knowledge_notes WHERE path=?`
3. `DELETE FROM organizer_suggestions WHERE note_path=?` (cleanup)

**Important:** This only runs on notes with `status='archived'`. Active notes are never touched.

### Query Filter

All existing note queries (`note_list`, `note_search`, `list_children`, `list_topics`) already filter by `status != 'archived'` or `status = 'active'`. Verify this during implementation; if any query shows archived notes, fix it.

## Section 5: Tool Registration Summary

### knowledge_manage (Discovery)

| Tool | Permission | New? |
|---|---|---|
| `topic_create` | STANDARD | Exists (Milestone A) |
| `topic_rename` | STANDARD | New |
| `topic_merge` | STANDARD | New |
| `topic_delete` | STANDARD | New |
| `note_archive` | STANDARD | New |
| `knowledge_stats` | OPEN | New (placeholder — returns basic counts for now) |

### knowledge_read (Basis-set candidate)

| Tool | Permission | New? |
|---|---|---|
| `note_search` | OPEN | Exists |
| `note_read` | OPEN | Exists |
| `note_list` | OPEN | Exists |
| `topic_list` | OPEN | Exists |
| `find_related` | OPEN | New |
| `topic_overview` | OPEN | New |

### Category update in categories.py

Update the `TOOL_CATEGORIES` dict to include the new tool names.

## Section 6: Testing Strategy

### Unit tests

- `topic_rename`: rename changes path + title + children parent_path + on-disk filename
- `topic_merge`: children moved, source becomes redirect, wikilinks untouched
- `topic_delete`: empty topic → deleted; non-empty → error
- `note_archive`: status flipped, note hidden from searches
- `find_related`: returns ranked results excluding the source note
- `topic_overview`: structured output with correct counts
- `buildBreadcrumb()`: JS unit test via Playwright or manual verification
- Lifecycle: archived > 30d → hard delete; archived < 30d → kept

### Integration tests

- Full flow: create topic → add notes → rename → verify children updated
- Merge flow: two topics → merge → redirect note exists → children in target
- Archive lifecycle: archive a note → fast-forward 31 days (freezegun) → organizer sweep → note gone from DB + disk

## Section 7: Constitution Compliance

- **Audit Everything**: all destructive operations (rename, merge, delete, archive, hard_delete) log audit events
- **Security Fail-Closed**: `topic_delete` refuses if children exist. `note_archive` is the only user-facing "delete" — no accidental data loss
- **Capability Scoping**: all new tools in `knowledge_manage` (STANDARD permission). Discovery required for access. Custom agents with OPEN-only permissions cannot rename/delete topics
- **English Everywhere**: all code, tool names, audit events in English

## Follow-up Work (explicitly out of scope)

- Embedding-based semantic search replacing FTS5 in `find_related`
- Weekly review tool (`weekly_summary`) aggregating tasks + reminders + recent notes
- Knowledge stats with growth trends over time
- Automatic redirect resolution in UI (wikilink click on redirect → navigate to target)
- Wikilink rewriting on merge (only if we find the soft-merge insufficient)
- Bulk archive / bulk move operations
