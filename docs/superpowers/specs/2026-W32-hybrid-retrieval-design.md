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

## Work Package 2: Local Embeddings — Repair + Model Switch

### Correction to this spec's first draft (2026-08-12)

The original WP2 text assumed no local provider existed. **It does:**
`embeddings.py` already ships `LocalEmbeddingProvider` (sentence-transformers,
`all-MiniLM-L6-v2`, 384d), `sentence-transformers>=3.0` is an optional
dependency group (`embeddings`) in pyproject.toml, and `get_embedding_provider`
already falls back to local when EU mode is on. WP2 is therefore a **repair +
model switch**, not a greenfield build. The four real defects:

1. **Fail-open provider selection** (`service.py:_init_embedding_provider`):
   `openai_key = "available"` is set whenever *any* proxy client exists — it
   never checks whether an OpenAI credential is actually present. Without a
   key the OpenAI provider is still selected and every `compute()` silently
   returns `[]`: semantic search is dead with no error anywhere. This is the
   June audit's P1-7 "fake provider detection", still open.
2. **No backfill on dimension change** (`service.py:_ensure_vec_table`): the
   vec table is correctly dropped on 384↔1536, but nothing re-embeds the
   notes, so the vector arm stays empty until each note happens to be
   rewritten.
3. **Implicit model download**: `LocalEmbeddingProvider._load_model()` pulls
   ~90 MB from HuggingFace on first use, without consent, inside a request.
4. **English-centric model**: `all-MiniLM-L6-v2` is weak on German notes.

### Model switch

- Target model: **`intfloat/multilingual-e5-small`** (384d — same dimension as
  today, but a different vector space, so a full re-embed is required anyway).
- Runtime stays **sentence-transformers** (already a dependency, already
  installed, works on the Pi). No fastembed/ONNX migration.

  **Why not fastembed (evaluated and rejected 2026-08-12).** The obvious
  objection to sentence-transformers is that it drags in PyTorch (~2 GB) for
  CPU-only inference. fastembed (ONNX, no torch) would avoid that — but its
  model catalog does not contain `multilingual-e5-small`. It offers only
  `multilingual-e5-large` (1024d, **2.24 GB** — larger than the torch stack
  it would save, and unusable on a Pi) or
  `paraphrase-multilingual-MiniLM-L12-v2` (384d, 220 MB, but a 2019 model
  that is clearly weaker at retrieval than E5, especially on the
  query-vs-document asymmetry E5's prefixes exist for). The trade was
  therefore "save ~2 GB of install, lose search quality", not "same quality,
  less weight". Decided against, because the deployment constraints that
  motivated it do not bite: the Pi has 8 GB RAM and its image is built
  natively on ARM (no cross-compile wheel problems). Image size grows by the
  torch stack — accepted knowingly. Revisit only if fastembed adds
  `multilingual-e5-small`, or if a smaller deployment target appears.
- E5 requires role prefixes: documents get `"passage: "`, queries get
  `"query: "`. The `EmbeddingProvider` interface gains
  `compute(text, *, is_query: bool = False)` and the batch equivalent; the
  OpenAI provider accepts and ignores the flag. Indexing call sites use the
  document default; `search`/`find_relevant`/`find_duplicates` query paths
  pass `is_query=True`.
- Model files live under `~/.mycelos/models/` (`SENTENCE_TRANSFORMERS_HOME`
  pinned there, not the global HF cache) so `mycelos doctor` can check them
  and backups are self-contained.

### Migration stance (decided 2026-08-12)

Single-user deployment: **no gradual migration path, no dual-vector-space
period**. On a provider/model/dimension change the vec table is dropped and
every note is re-embedded from scratch. Old vectors are disposable.

### Provider selection (deterministic, fail-closed)

Order, evaluated at app startup:

1. Explicit setting (`embedding_provider` = `openai` | `local` | `none`) — stored in `knowledge_config` next to the existing `embedding_provider`/`embedding_dimension` stamps, wins always. EU mode + `openai` = refused at startup with a clear error (fail closed, no silent override).
2. EU mode active → `local` if the model is present, else `none` (embeddings off; hybrid degrades to FTS — never a remote call, Constitution Rule 3 posture).
3. **Real** OpenAI credential present → `openai`. "Real" means asking the credential proxy whether the `openai` credential exists — not merely "a proxy client object exists" (defect 1). If the check cannot be performed, treat it as absent (fail closed).
4. Local model present on disk → `local`.
5. Otherwise → `none`.

The local provider runs in-process in the gateway — no credentials, no SecurityProxy involvement (Rule 4 untouched).

### Model download (explicit, consented)

- New CLI command `mycelos embeddings setup` (i18n via `t()`, en+de keys in the same step — Rule 7): states model name, size (~120 MB), target directory, asks for confirmation, downloads, verifies, reports.
- The gateway never downloads implicitly: `LocalEmbeddingProvider` loads from the pinned local directory only (offline load). If selection lands on `local` but the model is absent, it logs one warning and behaves as `none` — it must not reach out to HuggingFace at request time (defect 3).
- The web UI settings page is out of scope for v1 (CLI only); the doctor command learns an embeddings probe (provider chosen, model present, dimension matches, vector count vs. note count).

### Re-embed on provider/model change

- `knowledge_config` already stamps `embedding_provider` and `embedding_dimension` when the vec table is (re)created. Add a `embedding_model` stamp so a same-dimension model switch (MiniLM 384 → e5-small 384) is detected too — dimension alone is not enough.
- On mismatch: drop `knowledge_vec`, recreate (`distance_metric=cosine` pinned, as today), then **re-embed every note** via `compute_batch` in batches, logging progress. Fixes defect 2.
- Runs at first knowledge access, not inside a request handler's critical path; for this KB's size (hundreds of notes) it is seconds on CPU. While it has not run (e.g. model missing), vector arms return empty and hybrid degrades per the matrix above.

## Testing

- **Pure:** `rrf_fuse` — ordering, score math (1/(k+r+1)), dedup keeps first-seen dict, single-list passthrough, empty lists, limit truncation.
- **German:** index "Ernährung", search "ernahrung" → hit (FTS arm). Rebuild test: old-tokenizer index + version bump → rebuilt index matches.
- **Hybrid behavior:** with a stub embedding provider, a note matching only semantically and a note matching only by keyword both appear; RRF order sane. Degradation matrix rows each pinned.
- **E5 prefixes:** local provider prepends `passage: `/`query: ` correctly (assert on the text handed to the encoder, mocked — no model download in tests).
- **Selection matrix:** each row of the provider-selection order, incl. EU+explicit-openai refusal and the "proxy exists but no OpenAI credential → not openai" case that is defect 1 (goes in `tests/security/` next to the EU residency tests).
- **No implicit download:** with the model absent, provider selection yields `none` and nothing contacts HuggingFace.
- **Re-embed:** provider/model/dimension stamp mismatch → vec table rebuilt AND all notes re-embedded (assert vector row count equals note count); duplicate detection works after.
- **Pin:** `find_duplicates` never calls `rrf_fuse` and never touches FTS (fail-closed June decision).

## Rollout

Two implementation plans, WP1 first (pure win, no new dependency), WP2 second (depends on WP1's stamp mechanism). Both land behind no flag — behavior degrades to today's when nothing new is installed or configured.
