# Hybrid Ranking (WP1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `search()` and `find_relevant()` fuse FTS5 (BM25) and vector KNN via Reciprocal Rank Fusion, and the FTS index becomes diacritics-insensitive (`remove_diacritics 2`) with a self-detecting rebuild — per WP1 of `docs/superpowers/specs/2026-W32-hybrid-retrieval-design.md`.

**Architecture:** A new pure module `knowledge/ranking.py` holds `rrf_fuse()` (mirrors `organizer.py`'s testable-core pattern). `KnowledgeBase.search()` and `find_relevant()` call both arms and fuse. The indexer's `ensure_fts()` learns the new tokenizer and detects an outdated index by inspecting the stored DDL in `sqlite_master` (self-describing — no separate version stamp), rebuilding from note files on mismatch. `find_duplicates` is untouched (vector-only, June P0-3 decision).

**Tech Stack:** Python 3.12, SQLite FTS5, sqlite-vec, pytest.

## Global Constraints

- `rrf_fuse` uses K=60, score `1/(k + rank + 1)` summed across lists, dedup by `path` keeping the first-seen dict, result ordered by descending `rrf_score`, truncated to `limit`. K is NOT configurable in v1.
- Degradation matrix (spec): no embedding provider → `search()`/`find_relevant()` behave exactly as today (FTS only, LIKE fallback for search); `find_duplicates` stays vector-only and MUST NOT call `rrf_fuse` or FTS.
- Vector arm for both hybrid paths: `top_k = limit * 2`, `threshold = 0.25` (noise filter only; ranking is RRF's job).
- Type/tag filters apply to the vector arm's results before fusion (same contract as the FTS arm).
- FTS tokenizer: `unicode61 remove_diacritics 2`. Rebuild is idempotent, logged, automatic at startup, and re-reads note content from the markdown files (files are the content source of truth).
- No API/tool signature changes. No audit events, no config generations (read-only paths, content tables).
- All code/comments/log messages English. TDD per task. Commit messages English, conventional style, NO Co-Authored-By/Generated-with footers. CHANGELOG.md entry under `## Week 32 (2026)` (folded into the last task).
- Environment: run tests as `export PYTHONPATH=<worktree>/src; python -m pytest ...` (editable install points elsewhere; prefix every command). If a run hits a SecurityProxy unix-socket PermissionError, that is the sandbox — rerun with sandbox disabled.

---

### Task 1: `rrf_fuse` in new pure module `knowledge/ranking.py`

**Files:**
- Create: `src/mycelos/knowledge/ranking.py`
- Test: `tests/test_ranking.py` (new)

**Interfaces:**
- Consumes: nothing.
- Produces: `rrf_fuse(ranked_lists: list[list[dict]], k: int = 60, limit: int = 10) -> list[dict]` — Tasks 3 and 4 import it from `mycelos.knowledge.ranking`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_ranking.py`:

```python
from __future__ import annotations

from mycelos.knowledge.ranking import rrf_fuse


def _r(path: str, **extra) -> dict:
    return {"path": path, "title": path, **extra}


def test_single_list_preserves_order_and_scores() -> None:
    fused = rrf_fuse([[_r("a"), _r("b"), _r("c")]], limit=10)
    assert [x["path"] for x in fused] == ["a", "b", "c"]
    assert fused[0]["rrf_score"] == 1 / 61  # k=60, rank 0
    assert fused[1]["rrf_score"] == 1 / 62


def test_result_in_both_lists_outranks_single_list_results() -> None:
    fts = [_r("only-fts"), _r("both")]
    vec = [_r("both"), _r("only-vec")]
    fused = rrf_fuse([fts, vec], limit=10)
    # "both": 1/62 + 1/61 > "only-fts": 1/61 > "only-vec": 1/62
    assert [x["path"] for x in fused] == ["both", "only-fts", "only-vec"]


def test_dedup_keeps_first_seen_dict() -> None:
    fts = [_r("x", origin="fts")]
    vec = [_r("x", origin="vec")]
    fused = rrf_fuse([fts, vec], limit=10)
    assert len(fused) == 1
    assert fused[0]["origin"] == "fts"  # earlier list wins


def test_empty_lists_are_skipped() -> None:
    assert rrf_fuse([[], []], limit=10) == []
    fused = rrf_fuse([[], [_r("a")]], limit=10)
    assert [x["path"] for x in fused] == ["a"]


def test_limit_truncates() -> None:
    fused = rrf_fuse([[_r(f"n{i}") for i in range(20)]], limit=5)
    assert len(fused) == 5


def test_input_dicts_are_not_mutated() -> None:
    original = _r("a")
    rrf_fuse([[original]], limit=10)
    assert "rrf_score" not in original
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `export PYTHONPATH=$PWD/src; python -m pytest tests/test_ranking.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'mycelos.knowledge.ranking'`

- [ ] **Step 3: Implement**

Create `src/mycelos/knowledge/ranking.py`:

```python
"""Pure ranking logic — Reciprocal Rank Fusion.

No storage, no LLM, no I/O. This module is the testable core, mirroring
``organizer.py``'s pattern. Spec: docs/superpowers/specs/
2026-W32-hybrid-retrieval-design.md (WP1).
"""
from __future__ import annotations

RRF_K = 60


def rrf_fuse(
    ranked_lists: list[list[dict]],
    k: int = RRF_K,
    limit: int = 10,
) -> list[dict]:
    """Reciprocal Rank Fusion over result lists.

    Each result dict must carry "path". A result at zero-based rank r in
    one list contributes 1/(k + r + 1); contributions sum across lists.
    Returns the first-seen dict per path (earlier lists win) with
    "rrf_score" added, ordered by descending score, truncated to limit.
    Input dicts are not mutated.
    """
    scores: dict[str, float] = {}
    first_seen: dict[str, dict] = {}
    for results in ranked_lists:
        for rank, result in enumerate(results):
            path = result.get("path")
            if not path:
                continue
            scores[path] = scores.get(path, 0.0) + 1.0 / (k + rank + 1)
            if path not in first_seen:
                first_seen[path] = result
    fused = []
    for path, score in sorted(scores.items(), key=lambda kv: kv[1], reverse=True):
        entry = dict(first_seen[path])
        entry["rrf_score"] = score
        fused.append(entry)
    return fused[:limit]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `export PYTHONPATH=$PWD/src; python -m pytest tests/test_ranking.py -v`
Expected: 6/6 PASS

- [ ] **Step 5: Commit**

```bash
git add src/mycelos/knowledge/ranking.py tests/test_ranking.py
git commit -m "feat(knowledge): pure rrf_fuse ranking module"
```

---

### Task 2: Diacritics-insensitive FTS with self-detecting rebuild

**Files:**
- Modify: `src/mycelos/knowledge/indexer.py` (`ensure_fts`, lines ~18-31)
- Modify: `src/mycelos/knowledge/service.py` (startup wiring — find where the service constructs the indexer / calls `ensure_fts` and add the rebuild call; read the `__init__` first)
- Test: `tests/test_knowledge_base.py`

**Interfaces:**
- Consumes: existing `index_note(...)` (re-used for re-indexing), `parse_frontmatter` from `mycelos.knowledge.note`.
- Produces: `KnowledgeIndexer.ensure_fts(rebuild_content_provider: Callable[[str], str] | None = None) -> bool` — returns True when it (re)built the table. `KnowledgeBase` passes a content provider that reads a note's markdown body by path. Tokenizer constant `FTS_TOKENIZER = "unicode61 remove_diacritics 2"`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_knowledge_base.py` (reuse its existing `app`/`kb` fixture pattern — read the file first and adapt fixture names):

```python
def test_search_is_diacritics_insensitive(kb) -> None:
    kb.write(title="Ernährung", content="Gemüse und Obst täglich", topic="notes")
    hits = kb.search("ernahrung")
    assert any(h["title"] == "Ernährung" for h in hits)
    hits = kb.search("gemuse")
    assert any(h["title"] == "Ernährung" for h in hits)


def test_outdated_fts_index_is_rebuilt(app, kb) -> None:
    # Simulate a pre-existing index built with the old tokenizer.
    kb.write(title="Ernährung", content="Gemüse", topic="notes")
    app.storage.execute("DROP TABLE knowledge_fts")
    app.storage.executescript(
        "CREATE VIRTUAL TABLE knowledge_fts USING fts5(title, content, tags);"
    )
    # Old-tokenizer index is empty and diacritics-sensitive. Re-running the
    # service bootstrap must detect the DDL mismatch and rebuild from files.
    from mycelos.knowledge.service import KnowledgeBase
    kb2 = KnowledgeBase(app)
    hits = kb2.search("gemuse")
    assert any(h["title"] == "Ernährung" for h in hits)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `export PYTHONPATH=$PWD/src; python -m pytest tests/test_knowledge_base.py -v -k "diacritics or rebuilt"`
Expected: both FAIL (no diacritics folding; no rebuild logic)

- [ ] **Step 3: Implement**

In `src/mycelos/knowledge/indexer.py`, replace `ensure_fts`:

```python
FTS_TOKENIZER = "unicode61 remove_diacritics 2"


    def ensure_fts(self, rebuild_content_provider=None) -> bool:
        """Create the FTS5 table, upgrading the tokenizer when outdated.

        The stored DDL in sqlite_master is self-describing: when it lacks
        the current tokenizer clause, the table is dropped and recreated.
        Returns True when a (re)build happened, so the caller can re-index.
        ``rebuild_content_provider(path) -> str`` supplies note body text
        during re-indexing (files are the content source of truth).
        """
        row = self._storage.fetchone(
            "SELECT sql FROM sqlite_master WHERE name = 'knowledge_fts'"
        )
        if row and "remove_diacritics 2" in (row["sql"] or ""):
            return False
        self._storage.execute("DROP TABLE IF EXISTS knowledge_fts")
        self._storage.executescript(f"""
            CREATE VIRTUAL TABLE knowledge_fts USING fts5(
                title, content, tags,
                tokenize = '{FTS_TOKENIZER}'
            );
        """)
        if rebuild_content_provider is not None:
            notes = self._storage.fetchall(
                "SELECT id, path, title, tags FROM knowledge_notes"
            )
            for n in notes:
                content = ""
                try:
                    content = rebuild_content_provider(n["path"]) or ""
                except Exception:
                    logger.warning("FTS rebuild: could not read %s", n["path"])
                self._storage.execute(
                    "INSERT INTO knowledge_fts(rowid, title, content, tags) "
                    "VALUES (?, ?, ?, ?)",
                    (n["id"], n["title"], content, n["tags"] or ""),
                )
            logger.info("FTS index rebuilt (%d notes, tokenizer: %s)",
                        len(notes), FTS_TOKENIZER)
        return True
```

(Adapt to the file's actual structure: `FTS_TOKENIZER` at module level; keep the existing try/except probe only if the sqlite_master query proves insufficient; add a `logger` if the module has none, following the package's `logging.getLogger("mycelos.knowledge")` pattern.)

In `src/mycelos/knowledge/service.py`, where the service wires the indexer (find the `ensure_fts()` call site in `__init__`/bootstrap — read first), pass a content provider that reads the note body:

```python
        def _note_body(path: str) -> str:
            file_path = self._safe_path(path)
            if not file_path.exists():
                return ""
            from mycelos.knowledge.note import parse_frontmatter
            return parse_frontmatter(file_path.read_text(encoding="utf-8")).content

        self._indexer.ensure_fts(rebuild_content_provider=_note_body)
```

If `ensure_fts` is currently called somewhere without file access (e.g. bare indexer usage in tests), the parameter default `None` preserves those call sites: they still create the new-tokenizer table, just without re-indexing content.

- [ ] **Step 4: Run tests to verify they pass, then the file's full suite**

Run: `export PYTHONPATH=$PWD/src; python -m pytest tests/test_knowledge_base.py -v`
Expected: all PASS (existing search tests must stay green — the new tokenizer only folds diacritics)

- [ ] **Step 5: Commit**

```bash
git add src/mycelos/knowledge/indexer.py src/mycelos/knowledge/service.py tests/test_knowledge_base.py
git commit -m "feat(knowledge): diacritics-insensitive FTS with self-detecting rebuild"
```

---

### Task 3: Hybrid `search()`

**Files:**
- Modify: `src/mycelos/knowledge/service.py` (`search`, lines ~503-515)
- Test: `tests/test_knowledge_base.py`

**Interfaces:**
- Consumes: `rrf_fuse` (Task 1), existing `self._indexer.search_fts(...)`, `self._find_relevant_by_vector(text, top_k, threshold)` (returns dicts incl. `path`, `type`, `tags` JSON string, `score`).
- Produces: same `search()` signature; results may carry `rrf_score`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_knowledge_base.py`. The file already has a `_StubEmbeddingProvider` (used around line 348) — reuse it; it must produce deterministic vectors so two texts about the same thing land close. Read its implementation first; if it embeds by hashing words, craft the test data accordingly:

```python
def test_search_fuses_fts_and_vector_results(app, kb) -> None:
    kb.write(title="Kaffeemaschine entkalken", content="Essig und Wasser", topic="notes")
    kb.write(title="Espresso Bohnen", content="Kaffee Röstung dunkel", topic="notes")
    kb._embedding_provider = _StubEmbeddingProvider()  # match existing usage at ~line 348
    # re-index so vectors exist for both notes (mirror how the existing
    # stub-provider tests trigger embedding computation — read them first)
    ...
    hits = kb.search("Kaffee")
    paths = [h["path"] for h in hits]
    # FTS hit (title/content contains Kaffee) present:
    assert any("espresso-bohnen" in p for p in paths)
    # fused results carry the rrf score:
    assert all("rrf_score" in h for h in hits)


def test_search_without_provider_behaves_like_today(kb) -> None:
    # dimension == 0 → FTS-only, no rrf_score requirement, LIKE fallback intact
    kb.write(title="Solitaire", content="Kartenspiel", topic="notes")
    hits = kb.search("Solitaire")
    assert hits and hits[0]["title"] == "Solitaire"
    # typo → LIKE fallback path still works
    hits = kb.search("Solitair")
    assert hits and hits[0]["title"] == "Solitaire"


def test_search_type_filter_applies_to_vector_arm(app, kb) -> None:
    # a vector-armed search with type="task" must not return notes of other
    # types even if they are semantically close (filter before fusion)
    ...  # implementer: mirror the stub-provider setup; assert every hit has type "task"
```

The `...` sections are implementer-completed setup that must mirror the existing stub-provider tests in this file (the exact re-index invocation depends on how the file's existing vector tests do it — copy their pattern). The assertions shown are the contract.

- [ ] **Step 2: Run tests to verify they fail**

Run: `export PYTHONPATH=$PWD/src; python -m pytest tests/test_knowledge_base.py -v -k "fuses or behaves_like_today or vector_arm"`
Expected: fusion test FAILS (no rrf_score); no-provider test PASSES already (guards the refactor); type-filter test FAILS or errors

- [ ] **Step 3: Implement**

Replace `search()` in `service.py`:

```python
    def search(
        self,
        query: str,
        type: str | None = None,
        tags: list[str] | None = None,
        limit: int = 10,
    ) -> list[dict]:
        """Hybrid search: FTS5 and vector KNN fused via RRF.

        Degrades to FTS-only when no embedding provider is configured, and
        to the LIKE fallback when both arms come back empty (typo net).
        """
        from mycelos.knowledge.ranking import rrf_fuse

        fts_results = self._indexer.search_fts(
            query, type=type, tags=tags, limit=limit * 2
        )
        vector_results: list[dict] = []
        if self._embedding_provider.dimension > 0:
            candidates = self._find_relevant_by_vector(
                query, top_k=limit * 2, threshold=0.25
            )
            vector_results = _filter_results(candidates, type=type, tags=tags)
        if not fts_results and not vector_results:
            return self._indexer.search_like(query, type=type, limit=limit)
        if not vector_results:
            return fts_results[:limit]
        return rrf_fuse([fts_results, vector_results], limit=limit)
```

Add module-level helper `_filter_results(results, type, tags)` in `service.py`: keeps results whose `type` matches (when given) and whose parsed `tags` JSON contains all requested tags (when given) — replicate exactly the tag semantics `search_fts` implements (read `search_fts`'s tag handling first and match it; if `search_fts` ignores tags in SQL and post-filters, share that helper instead of duplicating).

Note: when only the vector arm has results, `rrf_fuse([[], vector_results], ...)` naturally handles it — the explicit `if not vector_results` branch above keeps FTS-only results byte-identical to today's shape (no `rrf_score`), which `test_search_without_provider_behaves_like_today` pins.

- [ ] **Step 4: Run tests to verify they pass, then the file's full suite**

Run: `export PYTHONPATH=$PWD/src; python -m pytest tests/test_knowledge_base.py tests/test_ranking.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add src/mycelos/knowledge/service.py tests/test_knowledge_base.py
git commit -m "feat(knowledge): hybrid FTS+vector search with RRF fusion"
```

---

### Task 4: Hybrid `find_relevant()` + fail-closed pins

**Files:**
- Modify: `src/mycelos/knowledge/service.py` (`find_relevant`, lines ~1122-1139)
- Test: `tests/test_knowledge_base.py`

**Interfaces:**
- Consumes: `rrf_fuse`, both arms as in Task 3.
- Produces: same `find_relevant(text, top_k=5, threshold=0.7)` signature. The `threshold` parameter continues to bound the VECTOR arm only (documented in the docstring) — FTS results are not similarity-scored and join via fusion.

- [ ] **Step 1: Write the failing tests**

```python
def test_find_relevant_includes_keyword_only_matches(app, kb) -> None:
    # a note matching only by keyword must appear in fused results
    # (today it is invisible whenever the vector arm returns anything)
    ...  # stub-provider setup as in Task 3; craft one semantic-only and one keyword-only note
    results = kb.find_relevant("Kaffee")
    paths = [r["path"] for r in results]
    assert <keyword-only note path> in paths


def test_find_relevant_without_provider_is_fts_only(kb) -> None:
    kb.write(title="Backup Strategie", content="Restic und Hetzner", topic="notes")
    results = kb.find_relevant("Backup")
    assert results and results[0]["title"] == "Backup Strategie"


def test_find_duplicates_never_uses_fts_or_fusion(kb, monkeypatch) -> None:
    # Pin the June P0-3 decision: duplicate detection is vector-only.
    import mycelos.knowledge.ranking as ranking
    import mycelos.knowledge.service as service_mod

    def _boom(*args, **kwargs):
        raise AssertionError("find_duplicates must not use FTS/fusion")

    monkeypatch.setattr(ranking, "rrf_fuse", _boom)
    monkeypatch.setattr(kb._indexer, "search_fts", _boom)
    monkeypatch.setattr(kb._indexer, "search_like", _boom)
    path = kb.write(title="Doppelt", content="inhalt", topic="notes")
    assert kb.find_duplicates(path) == []  # no provider → fail closed, no FTS
```

(`...` = stub-provider setup mirroring the file's existing pattern; the assertions are the contract. Note the fusion import in `search()`/`find_relevant()` must be `from mycelos.knowledge.ranking import rrf_fuse` resolved at call time or module level such that the monkeypatch in the pin test actually intercepts — if Task 3 used a function-local import, switch both call sites to a module-level `from mycelos.knowledge import ranking` + `ranking.rrf_fuse(...)` so `monkeypatch.setattr(ranking, "rrf_fuse", ...)` bites. Adjust Task 3's code accordingly when implementing this task.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `export PYTHONPATH=$PWD/src; python -m pytest tests/test_knowledge_base.py -v -k "keyword_only or fts_only or never_uses"`
Expected: keyword-only test FAILS (either/or behavior today); the other two document current behavior — verify they pass before the change and still pass after

- [ ] **Step 3: Implement**

Replace `find_relevant()`:

```python
    def find_relevant(
        self,
        text: str,
        top_k: int = 5,
        threshold: float = 0.7,
    ) -> list[dict]:
        """Find notes relevant to the given text — hybrid FTS+vector via RRF.

        ``threshold`` bounds the vector arm's cosine similarity only; FTS
        matches join through rank fusion regardless. Degrades to FTS-only
        when no embedding provider is available. Relevance is
        non-destructive, so keyword participation is appropriate here
        (unlike duplicate detection, which stays vector-only).
        """
        vector_results: list[dict] = []
        if self._embedding_provider.dimension > 0:
            vector_results = self._find_relevant_by_vector(
                text, top_k=top_k * 2, threshold=threshold
            )
        fts_results = self._indexer.search_fts(text, limit=top_k * 2)
        if not vector_results:
            return fts_results[:top_k]
        if not fts_results:
            return vector_results[:top_k]
        return ranking.rrf_fuse([fts_results, vector_results], limit=top_k)
```

`find_duplicates` is NOT modified — verify by reading it after the change.

- [ ] **Step 4: Run tests to verify they pass, then the affected suites**

Run: `export PYTHONPATH=$PWD/src; python -m pytest tests/test_knowledge_base.py tests/test_ranking.py tests/test_knowledge_organizer_handler.py -v`
Expected: all PASS (organizer handler consumes find_duplicates/find_relevant — its suite pins no behavior change for duplicates)

- [ ] **Step 5: Commit**

```bash
git add src/mycelos/knowledge/service.py tests/test_knowledge_base.py
git commit -m "feat(knowledge): hybrid find_relevant with RRF; pin vector-only duplicates"
```

---

### Task 5: CHANGELOG + verification

**Files:**
- Modify: `CHANGELOG.md`

- [ ] **Step 1: Add the CHANGELOG entry** under the existing `## Week 32 (2026)` heading (below the organizer entry, same week):

```markdown
### Hybrid search (FTS5 + vector + RRF)

- `search()` and `find_relevant()` now fuse full-text (BM25) and vector
  KNN results via Reciprocal Rank Fusion (K=60) instead of using one
  signal or falling back — keyword-only and semantic-only matches both
  surface, ranked sanely. Without an embedding provider, behavior is
  unchanged (FTS with LIKE fallback).
- The FTS index is diacritics-insensitive (`remove_diacritics 2`):
  "ernahrung" finds "Ernährung". Outdated indexes are detected via their
  stored DDL and rebuilt automatically at startup from the note files.
- Duplicate detection deliberately remains vector-only (fail-closed
  June decision) — now pinned by a regression test.
```

- [ ] **Step 2: Run the security suite**

Run: `export PYTHONPATH=$PWD/src; python -m pytest tests/security/ -q`
Expected: all PASS

- [ ] **Step 3: Run the knowledge/organizer test files**

Run: `export PYTHONPATH=$PWD/src; python -m pytest tests/test_knowledge_base.py tests/test_ranking.py tests/test_organizer_api.py tests/test_knowledge_organizer_handler.py tests/test_organizer_inbox.py tests/test_slugify.py -q`
Expected: all PASS

- [ ] **Step 4: Commit**

```bash
git add CHANGELOG.md
git commit -m "docs: changelog for hybrid search (W32)"
```
