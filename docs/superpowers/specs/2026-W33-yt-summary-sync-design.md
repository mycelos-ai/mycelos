# yt-summary → Mycelos Knowledge Sync — Design

Week 33 (2026). Status: approved in discussion, pending spec review.

## Goal

Stefan's YouTube-summary tool (`yt-summary`) feeds its summaries into the Mycelos Brain automatically and repeatably, so knowledge he produces daily lands where he searches for it. First real proof of the "everything flows into Mycelos" direction.

Transport is **MCP**, deliberately: yt-summary gains one generic sync tool that any MCP-speaking system can use, rather than a second interface built only for Mycelos.

## Non-Goals (YAGNI)

- No scoped/permissioned access, no MCP server on the Mycelos side, no egress gating — explicitly deferred (see the knowledge-gateway memory).
- No transcript import (decision below).
- No push direction: yt-summary does not call Mycelos. Mycelos pulls.
- No new auth model in yt-summary — the existing MCP auth applies.
- No ZIP/bundle transport over MCP (not possible; see Format).

## Decisions taken (2026-08-13)

| Question | Decision | Rationale |
|---|---|---|
| Transport | MCP tool in yt-summary | Makes yt-summary useful to other systems too, not just Mycelos; avoids a second interface. |
| Content | Summary + metadata, **no transcripts** | Keeps semantic search sharp and notes readable; also keeps MCP payloads small enough to paginate. Transcript stays reachable via the source URL. |
| Pagination | Cursor + fixed page size | Robust against concurrent edits, bounded response size, works for any consumer. |
| Re-import of changed items | Update the existing note | Brain always reflects yt-summary's current state; note identity, topic placement and links survive. |

## Part A — yt-summary changes

Three additions, each following an established pattern in that codebase. Repo layer is raw parametrized SQL via `aiosqlite`; services are pure functions; routes/tools stay thin; **every query is scoped by `user_id`**.

### A1. Repo: `list_updated_since`

`app/repos/videos.py`, next to `list_recent` (~line 336):

```python
async def list_updated_since(
    db, *, user_id: int, since: str | None, cursor: str | None, limit: int
) -> list[Video]:
    """Items changed at or after `since`, for incremental sync.

    Ordered by (updated_at ASC, id ASC) so a cursor can resume exactly.
    `cursor` is the last seen "<updated_at>|<id>" pair.
    """
```

Why `updated_at`, not `created_at`: summaries are updated **in place** (resummarize, highlight extraction, related-links backfill) without a new row. The existing bulk export filters on `created_at` (`app/routes/export.py:209-214`), so it would never re-emit an updated summary. This is a genuinely new query shape, not a parameter tweak — `list_recent` orders `created_at DESC, id DESC` for the UI feed.

Row mapping reuses `_row_to_video` with its guarded column access (`app/repos/videos.py:9-87`).

### A2. Service: OKF-shaped item renderer

`app/services/export.py`, next to `render_item_md` / `render_item_json`. Pure function, no I/O:

```python
def render_item_okf(video: Video, *, tags: list[str], playlists: list[str]) -> dict:
    """One sync item: OKF frontmatter fields + summary body, no transcript."""
```

Returns:

```python
{
  "id": "1:dQw4w9WgXcQ",          # stable external id (existing composite PK)
  "type": "note",                  # OKF's only required field
  "title": "...",
  "description": "...",            # first paragraph of the summary
  "resource": "https://www.youtube.com/watch?v=...",   # OKF 'resource' = source url
  "timestamp": "2026-08-13T09:12:00Z",                 # updated_at, the sync cursor
  "created": "2026-08-01T...",
  "tags": ["ai", "retrieval"],
  "kind": "youtube",
  "language": "de",
  "summary_model": "...",
  "playlists": ["..."],
  "duration_seconds": 1234,
  "highlights": [{"text": "...", "reason": "..."}],
  "content": "<summary markdown>",
}
```

**No `transcript` key.** The field names follow OKF's vocabulary (`type`, `title`, `description`, `tags`, `timestamp`, `resource`) so the Mycelos side maps them without a translation table.

Additionally: add `id` and `updated_at` to the frontmatter emitted by the existing `render_item_md` (currently absent — the id only appears in the JSON export and the filename). That makes the *file* export idempotently importable too, and costs two lines.

### A3. MCP tool: `export_since`

`app/routes/mcp.py`, registered in `build_mcp_server` following the `list_recent` / `ask_library` pattern:

```python
@mcp.tool()
async def export_since(since: str = "", cursor: str = "", limit: int = 50) -> dict:
    """Items created or updated since `since` (ISO 8601), for incremental sync.

    Returns {"items": [...], "next_cursor": str, "has_more": bool}.
    Summaries and metadata only — no transcripts. Call repeatedly with
    the returned next_cursor until has_more is false.
    """
```

- `limit` clamped to a sane maximum (suggest 100) regardless of what the caller asks for — MCP payload safety.
- Empty `since` means "from the beginning" (initial full sync).
- `next_cursor` is opaque to the caller (`"<updated_at>|<id>"`).
- Thin wrapper: delegates to `list_updated_since` + `render_item_okf`, exactly as other tools delegate to repo/service functions.

The module docstring states the MCP surface is deliberately smaller than REST. This tool earns its place: it is the sync surface for *any* consumer, which is precisely the argument for putting it in MCP rather than adding a Mycelos-only REST route.

## Part B — Mycelos changes

### B1. OKF import mapper

New `src/mycelos/knowledge/okf_import.py` — the mirror of `okf_export.py`, and equally the single place that knows OKF:

```python
def okf_item_to_note(item: dict) -> dict:
    """Map one OKF item to Mycelos note fields.

    Returns {title, content, type, tags, source, external_id, timestamp}.
    Unknown keys are ignored, never written blindly.
    """
```

Rules:
- `type` → note type, defaulting to `note`; only accept types Mycelos knows, else `note`.
- `resource` → `source.url`; `id` → `source.external_id`.
- `timestamp` → used for change detection, not written as `created_at`.
- Content is the summary body; a compact metadata header (channel/link/duration) is prepended so the note is readable standalone.
- **Untrusted-content stance:** item text is data, never instruction. It is never interpolated into a classifier prompt without the existing data-not-instructions framing the organizer already applies.

### B2. `yt_summary` ingest source

`src/mycelos/knowledge/connector_ingest.py`, registered in `INGEST_SOURCES` next to `gmail`, following `ingest_gmail` exactly (MCP call → `external_id` dedup → fail closed on error):

```python
def ingest_yt_summary(app, user_id="default", max_items=DEFAULT_MAX_ITEMS,
                      since=None, mcp=None) -> dict:
    """Pull summaries from yt-summary into the knowledge base.

    Returns {fetched, created, updated, skipped_unchanged} or {error: ...}.
    """
```

Behavior per item:
- `external_id_exists(storage, "yt_summary", external_id)` decides create vs. update.
- **New:** create the note with `created_by="import"`, `source={"kind": "connector", "connector": "yt_summary", "external_id": ..., "url": ...}`.
- **Existing:** compare the stored `source.timestamp` against the item's `timestamp`; unchanged → skip (counted separately), changed → `kb.update()` the content. Topic placement, links and organizer state survive because the note path is unchanged.
- Paginates via `next_cursor` until `has_more` is false or `max_items` is reached.
- The high-water mark (last successful `timestamp`) is persisted per source so the next run resumes there; a failed run does not advance it (fail closed — better to re-fetch than to skip).

### B3. Scheduled sync

Reuse the existing scheduler + `INGEST_SOURCES`; no new scheduling machinery. The sync is opt-in and configured like other scheduled tasks.

## Data flow

```
yt-summary                          Mycelos
  videos (SQLite)
    └─ list_updated_since ──┐
                            │
       render_item_okf      │
            │               │
    MCP tool export_since ──┼──► mcp_manager.call_tool
                            │        │
                            │   ingest_yt_summary
                            │        │
                            │   okf_item_to_note
                            │        │
                            │   kb.write / kb.update
                            │        │
                            └───► organizer → embeddings → hybrid search
```

## Error handling

- yt-summary unreachable / tool error → ingest returns `{"error": ...}`, writes nothing, does not advance the high-water mark (matches `ingest_gmail`).
- A malformed item (missing id or title) is skipped and counted, never aborts the batch.
- Partial page failure: the run stops at the last successfully processed item; the cursor resumes there.
- Every run logs a `knowledge.ingest` audit event with counts — no note content in the audit payload (privacy rule).

## Testing

**yt-summary:** `list_updated_since` ordering and cursor resume (incl. two items sharing an `updated_at`); `render_item_okf` field mapping and the absence of a transcript key; `export_since` pagination (`has_more`, `next_cursor`, limit clamping); user scoping (another user's items never appear). Follows the existing `tests/test_services_export.py` / `tests/test_routes_mcp.py` patterns.

**Mycelos:** `okf_item_to_note` mapping incl. unknown-type fallback and missing-field tolerance; ingest creates on first run, skips unchanged on second, updates on changed timestamp; note path/topic survives an update; error from the MCP layer writes nothing and leaves the high-water mark; pagination consumes multiple pages. Fake MCP object injected exactly as the Gmail ingest tests do.

## Rollout

yt-summary first (Part A is small and independently useful — any MCP client can then sync), Mycelos second. Two separate plans, two separate branches, two repos.
