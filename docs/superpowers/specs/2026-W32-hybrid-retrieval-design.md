# Hybrid Retrieval + Local Embeddings — Design

Week 32 (2026). Status: approved in discussion, pending spec review.

## Goal

Knowledge search that ranks well and works everywhere — offline, EU mode, Raspberry Pi:

1. **Hybrid ranking:** `search()` and `find_relevant()` fuse FTS5 (BM25) and vector KNN results via Reciprocal Rank Fusion instead of using one signal or falling back.
2. **Local embedding provider:** semantic search no longer requires an OpenAI key; EU mode enforces the local provider instead of disabling embeddings.

Origin: June audit item P2-12 (diacritics, hybrid RRF) + W32 competitive research (both gbrain and TencentDB-Agent-Memory treat hybrid BM25+vector+RRF as table stakes; Tencent defaults to a local embedding model).

## Non-Goals (YAGNI)

- No trigram index for German compounds (revisit only if real searches fail on compounds).
- No cross-encoder reranker, no LLM query expansion.
- `find_duplicates` stays **vector-only** — the June P0-3 decision stands: keyword overlap must never produce merge suggestions. A test pins that it never calls the fusion path.
- No changes to OKF, import, or MCP surfaces (separate roadmap items).

## Work Package 1: Hybrid Ranking

### New module: `src/mycelos/knowledge/ranking.py` (pure)

No storage, no LLM, no I/O — mirrors `organizer.py`'s testable-core pattern.

```python
def rrf_fuse(
    ranked_lists: list[list[dict]],
    k: int = 60,
    limit: int = 10,
) -> list[dict]:
    """Reciprocal Rank Fusion over result lists.

    Each result dict must carry "path". Score for a result appearing at
    zero-based rank r in a list is 1/(k + r + 1); scores sum across lists.
    The returned dicts are the first-seen dict per path (earlier lists win),
    with "rrf_score" added, ordered by descending score, truncated to limit.
    Empty input lists are skipped; fusing one list preserves its order.
    """
```

K=60 is the standard constant (used by gbrain, Tencent, and the original RRF paper). Not configurable in v1.

### `KnowledgeBase.search()` (service.py)

Today: FTS5, else LIKE fallback. New behavior:

1. FTS arm: `self._indexer.search_fts(query, type=..., tags=..., limit=limit * 2)`.
2. Vector arm (only when `self._embedding_provider.dimension > 0`): `self._find_relevant_by_vector(query, top_k=limit * 2, threshold=0.25)`. The low threshold only filters noise; ranking is RRF's job.
3. `rrf_fuse([fts_results, vector_results], limit=limit)`.
4. Both arms empty → existing LIKE fallback (typo net) unchanged.
5. Type/tag filters: the vector arm returns unfiltered candidates; apply the same type/tags filter to vector results before fusion so filters keep their contract.

No API/tool signature changes — `/api/knowledge/search`, the chat search tool, and the UI keep working unchanged; only ordering improves.

### `KnowledgeBase.find_relevant()` (service.py)

Today: vector, else FTS fallback (either/or). New: same two arms, fused with `rrf_fuse`, same degradation. Callers (organizer related-notes, briefing context) see one ranked list where keyword-only matches appear with lower scores instead of not at all.

### German-friendly FTS: `remove_diacritics 2`

- `knowledge_fts` is created with tokenizer `unicode61 remove_diacritics 2` — "ernahrung" matches "Ernährung".
- **Rebuild migration:** the indexer stamps an FTS schema version (`PRAGMA user_version` on the DB, or a row in an existing meta table if one exists — implementation plan decides after inspecting storage). On startup, a version mismatch triggers: drop `knowledge_fts`, recreate with the new tokenizer, re-index all notes by reading each note file (files are the content source of truth; DB rows carry title/tags). Idempotent, logged, no user action.
- LIKE fallback and `search_like` are unaffected.

### Degradation matrix

| Situation | search() | find_relevant() | find_duplicates() |
|---|---|---|---|
| Embeddings available | FTS+vector RRF | FTS+vector RRF | vector only |
| No provider (no key, no local model) | FTS only (today's behavior) | FTS only | `[]` (fail closed) |
| FTS empty, vector empty | LIKE fallback | `[]` | `[]` |

## Work Package 2: Local Embedding Provider

### Provider: `LocalEmbeddingProvider` (embeddings.py)

- Runtime: **fastembed** (ONNX, CPU — no torch, ARM/Pi-compatible), optional dependency: `pip install "mycelos[local-embeddings]"`.
- Model: **`intfloat/multilingual-e5-small`**, 384 dimensions, quantized ONNX ~120 MB. Good German/English quality at note length.
- E5 requires role prefixes for quality: the provider prefixes documents with `"passage: "` and queries with `"query: "`. The `EmbeddingProvider` interface gains `compute(text, *, is_query: bool = False)` (and batch equivalent); the OpenAI provider accepts and ignores the flag. All indexing call sites pass documents (default); `search`/`find_relevant`/`find_duplicates` query paths pass `is_query=True` where the text is a query. (For the OpenAI provider nothing changes.)
- Model files live in `~/.mycelos/models/` (fastembed cache dir pinned there — not the global HF cache), so `mycelos doctor` can check them and backups are self-contained.

### Provider selection (deterministic, fail-closed)

Order, evaluated at app startup:

1. Explicit setting (`embedding_provider` = `openai` | `local` | `none`) — stored via the service layer like other config, wins always. EU mode + `openai` = refused at startup with a clear error (fail closed, no silent override).
2. EU mode active → `local` if the model is installed, else `none` (embeddings off; hybrid degrades to FTS — never a remote call, Constitution Rule 3 posture).
3. OpenAI credential available → `openai` (today's behavior).
4. Local model installed → `local`.
5. Otherwise → `none`.

The local provider runs in-process in the gateway — no credentials, no SecurityProxy involvement (Rule 4 untouched).

### Model download (explicit, consented)

- New CLI command `mycelos embeddings setup` (i18n via `t()`, en+de keys in the same step — Rule 7): states model name, size (~120 MB), target directory, asks for confirmation, downloads, verifies, reports.
- The gateway never downloads implicitly. If selection lands on `local` but the model is absent, it logs one warning and behaves as `none`.
- The web UI settings page is out of scope for v1 (CLI only); the doctor command learns a `--check embeddings` probe (provider chosen, model present, dimension matches).

### Dimension migration

Provider or dimension changes (e.g. OpenAI 1536 → local 384):

- The active provider name + dimension are stamped alongside the vec table (same mechanism as the FTS version stamp).
- On startup mismatch: drop `knowledge_vec`, recreate with the new dimension (`distance_metric=cosine` pinned, as today), re-embed all notes in batches (`compute_batch`), log progress. Synchronous at startup; for typical KB sizes (hundreds to low thousands of notes) this is seconds-to-a-minute on CPU.
- While a backfill has not run (e.g. model missing), vector arms return empty and hybrid degrades per the matrix above.

## Testing

- **Pure:** `rrf_fuse` — ordering, score math (1/(k+r+1)), dedup keeps first-seen dict, single-list passthrough, empty lists, limit truncation.
- **German:** index "Ernährung", search "ernahrung" → hit (FTS arm). Rebuild test: old-tokenizer index + version bump → rebuilt index matches.
- **Hybrid behavior:** with a stub embedding provider, a note matching only semantically and a note matching only by keyword both appear; RRF order sane. Degradation matrix rows each pinned.
- **E5 prefixes:** local provider prepends `passage: `/`query: ` correctly (assert on the text handed to the ONNX runner, mocked).
- **Selection matrix:** each row of the provider-selection order, incl. EU+explicit-openai refusal (goes in `tests/security/` next to the EU residency tests).
- **Migration:** dimension change → vec table rebuilt, all notes re-embedded, duplicate detection works after.
- **Pin:** `find_duplicates` never calls `rrf_fuse` and never touches FTS (fail-closed June decision).

## Rollout

Two implementation plans, WP1 first (pure win, no new dependency), WP2 second (depends on WP1's stamp mechanism). Both land behind no flag — behavior degrades to today's when nothing new is installed or configured.
