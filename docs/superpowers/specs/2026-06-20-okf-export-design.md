# OKF Export (Proof-of-Concept) — Design

**Date:** 2026-06-20
**Status:** Approved — implementing
**Builds on:** `docs/local-reviews/` OKF evaluation (D1–D4)

## Goal

Emit the Mycelos knowledge tree as a conformant Open Knowledge Format (OKF v0.1)
bundle. Export only (D2). OKF is a **boundary format**: the internal `Note` +
SQLite index stay authoritative (D1). Field mapping is additive (D3). Reserved
filenames are emitted at the boundary, with no internal renames (D4).

## Decisions (from brainstorming)

- **Surfaces:** CLI `mycelos knowledge export --okf <dir>` **and** API
  `GET /api/knowledge/export?format=okf`. Shared serializer; thin wrappers.
- **Scope:** all non-archived notes (matches `get_graph_data` default).
- **Reserved files:** synthesize `index.md` (navigation) at bundle root and per
  topic directory. Do **not** synthesize `log.md` — journal notes export as
  ordinary files.
- **Delivery:** CLI writes a directory tree; API streams a `.zip`.

## Architecture — three units

### 1. `src/mycelos/knowledge/okf_export.py` — serializer (surface-agnostic)

Pure functions. No Click/FastAPI/network imports. All OKF knowledge lives here,
so a spec bump touches one file.

- `note_to_okf_frontmatter(note: dict) -> dict`
  Maps a Mycelos note dict to OKF frontmatter.
  - `type` required (always present).
  - Additive aliases (D3): `title`, `description` (first non-heading paragraph
    of body, truncated; empty if body is heading-only), `tags`,
    `timestamp` (← `updated_at` or `created_at`), `resource`
    (← `source.url` / `source.filename` when present).
  - Existing Mycelos keys pass through unchanged (`status`, `priority`,
    `parent_path`, `links`, `created_by`, `source`) so the bundle can round-trip
    back into Mycelos later. No keys removed.

- `build_okf_bundle(notes: list[dict], read_fn) -> dict[str, str]`
  Returns an in-memory `{relative_path: file_contents}` map.
  - Directory layout derives from each note's `path` (already
    `topics/.../slug`); each note → `<path>.md`.
  - Synthesize `index.md` at bundle root and one per topic directory listing
    child notes as markdown links.
  - `log.md` not synthesized.
  - Single map → both surfaces emit identical bytes.

### 2. CLI — `src/mycelos/cli/knowledge_cmd.py`

New `knowledge` Click group, registered in `main.py`.
`export --okf <dir>` writes each `{path: content}` entry under the target dir.
All user-facing strings via `t()` (Rule 7); new keys in `en.yaml` + `de.yaml`.
If target dir exists and is non-empty, require `--force` (no silent clobber).

### 3. API — `src/mycelos/gateway/routers/knowledge.py`

`GET /api/knowledge/export?format=okf`. Builds bundle, zips in-memory, returns
`Response`/`StreamingResponse` with
`Content-Disposition: attachment; filename=mycelos-okf-<date>.zip`.
Non-archived only. `format` validates `okf`; anything else → 422.

## Data flow

```
list_notes(limit=5000) → filter status != "archived"
  → read(path) per note (full frontmatter + content)
  → note_to_okf_frontmatter(note) + body → markdown
  → {path+".md": contents}
  → synthesize index.md (root + per topic dir)
  → bundle dict {relpath: contents}
       ├─ CLI: write under <dir>
       └─ API: zip in-memory → download
  → audit.log("knowledge.export", {format, count})
```

## Constitution check

- **Rule 1 (Audit):** export mutates nothing, so an audit event is not strictly
  required; we log `knowledge.export` anyway as provenance ("data left the
  system"). Not overclaimed as mandatory.
- **Rule 2 (Config gen):** export is read-only and touches no declarative-state
  table → no `apply_from_state`.
- **Rule 7 (i18n):** all CLI strings via `t()`, keys mirrored in `de.yaml`.
- **Rule 9 (English in code):** code/comments/docs English.

## Scope & edge cases

- Archived notes excluded.
- `documents/` binary blobs are **not** included (text-only PoC); noted as a
  known limitation in the root `index.md` and docstring rather than silently
  dropped.
- API zip writer defensively rejects `..`/absolute entries (paths come from the
  DB and are already slugified, but guard anyway).
- Empty KB → bundle with only the root `index.md` (valid, not an error).

## Testing (TDD)

- `tests/test_okf_export.py` (unit, bulk):
  - required `type`; additive aliases present; existing keys preserved
  - `description`/`timestamp`/`resource` derivation incl. empties
  - bundle layout mirrors note paths; root + per-topic `index.md` with correct
    child links
  - archived excluded; empty KB → root index only
  - round-trip: bundle re-parses via `parse_frontmatter` into equivalent `Note`
- `tests/test_cli.py`: `knowledge export --okf` writes files; `--force`; i18n keys resolve
- API test: `GET /api/knowledge/export?format=okf` → zip with expected members;
  bad format → 422
- i18n parity stays green.

## Known limitations (PoC)

- Export only (import is a later extension of `run_preserve_import`).
- No binary document blobs.
- No `log.md` synthesis.
