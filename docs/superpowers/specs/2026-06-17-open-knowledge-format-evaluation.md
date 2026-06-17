# Open Knowledge Format (OKF) — Evaluation & Decision

**Date:** 2026-06-17
**Status:** Draft — recommendation pending sign-off
**Scope:** Evaluate whether Mycelos should adopt Google Cloud's Open Knowledge Format (OKF v0.1) for its Knowledge Base. Decide *if* and *how* to support it without disrupting the existing markdown + SQLite-index architecture.

**Reference:** [How the Open Knowledge Format can improve data sharing](https://cloud.google.com/blog/products/data-analytics/how-the-open-knowledge-format-can-improve-data-sharing) (Google Cloud, 2026-06-12)

---

## TL;DR

**Recommendation: adopt OKF as an interoperability layer (import/export), not as an architecture change.**

Mycelos independently converged on ~90% of the OKF design — markdown files with YAML frontmatter, a directory hierarchy of concepts, and a markdown-link knowledge graph where the files are the source of truth. Supporting OKF is therefore a thin interop layer over what already exists, not a rewrite. It closes a real gap (Mycelos has no export today), fits the "your data stays local, portable, git-versionable" pitch, and gives a legitimate "among the first to support OKF" positioning. The main risk — OKF is v0.1 and Google-led — is contained by keeping OKF a *boundary format*, never the internal model.

---

## Problem

Mycelos stores personal knowledge (notes, tasks, topics) as markdown-on-disk with a SQLite search index. There is currently **no export path** other than copying the `knowledge/` directory by hand, and **no defined interchange format** for moving a knowledge bundle between Mycelos instances or handing it to an external agent/tool.

OKF proposes exactly such an interchange format. The question from the team: is it a sensible concept to adopt for our Knowledge Area, and does early support buy us anything?

---

## What OKF is

OKF v0.1 is a deliberately minimal, vendor-neutral specification:

- **Markdown files with YAML frontmatter**, one file per concept.
- **Directory hierarchy** representing the concept tree; **file path is identity**.
- **Markdown links between files form the knowledge graph** (richer than the folder tree alone).
- **`type` is the only required frontmatter field.** Everything else (`title`, `description`, `resource`, `tags`, `timestamp`) is convention, producer-defined.
- **Reserved filenames:** `index.md` (navigation) and `log.md` (chronological history).
- It is a **format, not a platform** — just files, readable in any editor, renderable on GitHub, shippable as a tarball, versionable in git. No accounts, no SDK, no mandatory integration.

Design intent: a *lingua franca* so different producers (teams, tools) and consumers (agents, viewers, search) can exchange curated context without lock-in. Google's own reference material is data-catalog-flavored (BigQuery tables, metrics, join paths), but the spec itself is domain-agnostic by virtue of `type` being producer-defined.

---

## Current Mycelos architecture (the fit)

Source of truth is the markdown tree under `~/.mycelos/knowledge/`; SQLite is a *computed index* for full-text (FTS5) and vector search. Key files: `src/mycelos/knowledge/note.py`, `service.py`, `indexer.py`, `import_pipeline.py`; schema in `src/mycelos/storage/schema.sql`.

| OKF v0.1 | Mycelos today | Fit |
|---|---|---|
| Markdown + YAML frontmatter, one file per concept | `Note` dataclass, persisted as `.md` with frontmatter (`note.py`) | ✅ identical |
| Directory hierarchy of concepts | Type folders (`topics/`, `notes/`, `tasks/`, …) + `parent_path` nesting | ✅ present |
| Markdown links form the graph | Wikilinks `[[path]]` + `knowledge_links` table with typed edges | ✅ present |
| `type` required | `Note.type` (`note`/`task`/`topic`/`decision`/`reference`/`fact`/`journal`) | ✅ present |
| `index.md` reserved (navigation) | Topic index files auto-generated (`regenerate_topic_indexes`) | ✅ effectively present |
| `log.md` reserved (history) | `type="journal"` notes / chronological entries | ⚠️ conceptually present, not named `log.md` |
| `title`, `tags`, `timestamp` | `title`, `tags`, `created_at` / `updated_at` | ✅ name-mapping only |
| `description` | (none — summary lives in body) | ⚠️ optional field to add |
| `resource` (link to source system) | `source` (JSON provenance) + `source_file` | ⚠️ modeled differently |

The markdown files already *being* the source of truth is the exact OKF philosophy ("just files, just markdown"). SQLite stays an implementation detail behind the boundary.

---

## Gaps to close

1. **Field mapping.** Mycelos frontmatter keys ↔ OKF conventions. Add/alias `description`, `resource`, `timestamp`. Low effort — `type` is already mandatory and the rest is producer-defined, so this is additive, not breaking.
2. **Export.** No export exists today. Needs a `mycelos knowledge export --okf` command / endpoint that emits the `knowledge/` tree as a conformant OKF bundle, including `index.md` navigation files.
3. **Import.** `import_pipeline.py::run_preserve_import` already parses frontmatter and mirrors a source folder tree into the topic hierarchy. An OKF importer is an extension of that path, not a new subsystem.

Rough effort: **a few days**, not weeks, precisely because the data model already converged.

---

## Risks & how we contain them

- **v0.1, not a finished standard.** Google states it is a starting point meant to evolve (backward-compatibly). Early adoption buys influence but risks field/convention churn. **Containment:** implement OKF strictly as a *boundary format* (import/export). The internal `Note` model and SQLite index stay authoritative, so a spec change touches one serializer, not the core.
- **Domain mismatch.** OKF's examples are enterprise data-catalog metadata (BigQuery tables, metrics, joins); Mycelos is personal notes/tasks/topics. The format carries both (that is what producer-defined `type` is for), but the spec's center of gravity is data sharing. **Our** killer use case is making knowledge bundles portable/versionable *between Mycelos instances* and consumable by external agents — frame it that way, don't chase the data-catalog framing.
- **Governance.** Open on GitHub, no lock-in (it is just files), but Google-driven for now. Acceptable given the boundary-format containment above.

---

## Decisions

### D1: Adopt OKF as an interoperability layer, not an internal model

OKF becomes an import/export interchange format. `Note` + SQLite remain the source of truth and search index. Rationale: maximizes the converged-design payoff, closes the export gap, and isolates spec-churn risk to a single serializer.

### D2: Ship export first

Export is the bigger gap (none exists today) and the lower-risk half (read-only, no ingestion edge cases). A `mycelos knowledge export --okf <dir>` command / `GET /api/knowledge/export?format=okf` endpoint emits a conformant bundle. Import follows by extending `run_preserve_import`.

### D3: Keep the field mapping additive

Add `description` and `resource`/`timestamp` aliases to the frontmatter serializer without removing or renaming existing keys. Existing notes stay valid; OKF-conformant output is produced at the export boundary.

### D4: Don't rename internal conventions to match OKF reserved names

Keep generating topic index files; emit them *as* `index.md` at the export boundary. Map `journal`-type notes to `log.md` on export. No internal renames — the mapping lives in the serializer only (consistent with D1).

---

## Recommendation

Proceed with **D1–D4**. The cost is low because Mycelos already built the pattern; the upside (portable, git-versionable, agent-consumable knowledge bundles + a credible "first to support OKF" story) fits the project's "build in the open, you own your data" stance. Risk is bounded by keeping OKF a boundary format.

**Suggested next step:** a proof-of-concept `mycelos knowledge export --okf` that emits the current `knowledge/` tree as a conformant OKF bundle, validated against Google's published sample bundles.
