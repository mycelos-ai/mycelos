# Local Embeddings (WP2) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make local embeddings the reliable default — deterministic fail-closed provider selection, no implicit model download, automatic re-embed on provider/model change, and a switch to the multilingual `intfloat/multilingual-e5-small` model with correct E5 role prefixes. Per WP2 of `docs/superpowers/specs/2026-W32-hybrid-retrieval-design.md`.

**Architecture:** `embeddings.py` keeps the provider classes and gains a pure, testable selection function. The service's `_init_embedding_provider` shrinks to gathering facts (EU mode, credential presence, model presence, explicit setting) and delegating the decision. `_ensure_vec_table` gains a model stamp and triggers a backfill when any stamp changes. Model installation moves out of the request path into an explicit CLI command.

**Tech Stack:** Python 3.12, sentence-transformers (existing optional dependency `embeddings`), sqlite-vec, pytest.

## Global Constraints

- **Model:** `intfloat/multilingual-e5-small`, 384 dimensions. Runtime stays sentence-transformers — no fastembed/ONNX migration.
- **E5 prefixes:** documents `"passage: "`, queries `"query: "`. Interface: `compute(text, *, is_query: bool = False)` and `compute_batch(texts, *, is_query: bool = False)`. The OpenAI provider accepts and ignores the flag.
- **Fail-closed (Constitution Rule 3):** any uncertainty in provider selection resolves downward (openai → local → none). A missing model NEVER triggers a network download at request time; it degrades to `none`.
- **No migration burden:** single-user deployment. On any provider/model/dimension change, drop the vec table and re-embed everything. Old vectors are disposable — do not build a compatibility path.
- Model files live under `~/.mycelos/models/` via `SENTENCE_TRANSFORMERS_HOME`, never the global HF cache.
- **Tests must never download a model.** All provider tests mock the encoder or assert on selection logic only.
- All code/comments/log messages English. User-facing CLI strings via `t()` with en+de keys added in the same step (Rule 7). TDD per task. Commit messages English, conventional, NO Co-Authored-By/Generated-with footers. CHANGELOG entry under `## Week 32 (2026)` (folded into the last task).
- Environment: `export PYTHONPATH=<worktree>/src; python -m pytest ...` (prefix every command; env does not persist between calls). SecurityProxy unix-socket PermissionError = sandbox → rerun with sandbox disabled.

## Current state (verified 2026-08-12)

- `src/mycelos/knowledge/embeddings.py`: `EmbeddingProvider` base, `OpenAIEmbeddingProvider` (1536d, via proxy `http_post`), `LocalEmbeddingProvider` (sentence-transformers `all-MiniLM-L6-v2`, 384d, lazy `_load_model()` that downloads implicitly), `FallbackProvider` (0d), `get_embedding_provider(openai_key, proxy_client, eu_mode)`, `serialize_embedding`/`deserialize_embedding`.
- `src/mycelos/knowledge/service.py:145` `_init_embedding_provider`: sets `openai_key = "available"` whenever a proxy client exists — the fail-open defect.
- `src/mycelos/knowledge/service.py:164` `_ensure_vec_table`: loads sqlite-vec, compares `knowledge_config.embedding_dimension`, drops+recreates on change, stamps `embedding_dimension` and `embedding_provider`. No backfill.
- Write path embeds at `service.py:~377` (`write`) and `~647` (`update`) with `compute(f"{title} {content}")`.
- `pyproject.toml` optional group `embeddings = ["sentence-transformers>=3.0"]`.

---

### Task 1: Pure provider-selection logic

**Files:**
- Modify: `src/mycelos/knowledge/embeddings.py`
- Test: `tests/test_embedding_selection.py` (new)

**Interfaces:**
- Produces: `select_provider_name(explicit: str | None, eu_mode: bool, has_openai_credential: bool, local_model_present: bool) -> str` returning `"openai" | "local" | "none"`, and `EUModeViolation(Exception)` raised when `explicit == "openai"` and `eu_mode` is True. Task 3 consumes both.
- Consumes: nothing (pure).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_embedding_selection.py`:

```python
from __future__ import annotations

import pytest

from mycelos.knowledge.embeddings import EUModeViolation, select_provider_name


def test_explicit_setting_wins() -> None:
    assert select_provider_name("local", False, True, True) == "local"
    assert select_provider_name("none", False, True, True) == "none"
    assert select_provider_name("openai", False, True, True) == "openai"


def test_explicit_openai_under_eu_mode_is_refused() -> None:
    with pytest.raises(EUModeViolation):
        select_provider_name("openai", True, True, True)


def test_explicit_local_without_model_falls_closed_to_none() -> None:
    # Asking for local when no model is installed must not download at
    # request time — it degrades.
    assert select_provider_name("local", False, False, False) == "none"


def test_eu_mode_prefers_local_never_openai() -> None:
    assert select_provider_name(None, True, True, True) == "local"
    assert select_provider_name(None, True, True, False) == "none"


def test_openai_only_with_real_credential() -> None:
    # The defect this closes: a proxy client existing is NOT a credential.
    assert select_provider_name(None, False, True, False) == "openai"
    assert select_provider_name(None, False, False, True) == "local"
    assert select_provider_name(None, False, False, False) == "none"


def test_credential_present_outranks_local() -> None:
    assert select_provider_name(None, False, True, True) == "openai"


def test_unknown_explicit_value_is_ignored_not_trusted() -> None:
    # Garbage in config must not select a provider by accident.
    assert select_provider_name("gpt9", False, False, True) == "local"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `export PYTHONPATH=$PWD/src; python -m pytest tests/test_embedding_selection.py -v`
Expected: FAIL with `ImportError: cannot import name 'EUModeViolation'`

- [ ] **Step 3: Implement**

In `src/mycelos/knowledge/embeddings.py`, above `get_embedding_provider`:

```python
class EUModeViolation(Exception):
    """Raised when configuration demands a non-EU provider under EU mode."""


_VALID_PROVIDERS = ("openai", "local", "none")


def select_provider_name(
    explicit: str | None,
    eu_mode: bool,
    has_openai_credential: bool,
    local_model_present: bool,
) -> str:
    """Decide which embedding provider to use. Pure, fail-closed.

    Order: explicit setting > EU mode > real OpenAI credential > local
    model > none. Every uncertainty resolves downward: an explicit choice
    whose prerequisite is missing degrades rather than reaching out to the
    network at request time.
    """
    if explicit in _VALID_PROVIDERS:
        if explicit == "openai":
            if eu_mode:
                raise EUModeViolation(
                    "embedding_provider=openai is not allowed while EU mode is on"
                )
            return "openai" if has_openai_credential else "none"
        if explicit == "local":
            return "local" if local_model_present else "none"
        return "none"
    if eu_mode:
        return "local" if local_model_present else "none"
    if has_openai_credential:
        return "openai"
    if local_model_present:
        return "local"
    return "none"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `export PYTHONPATH=$PWD/src; python -m pytest tests/test_embedding_selection.py -v`
Expected: 7/7 PASS

- [ ] **Step 5: Commit**

```bash
git add src/mycelos/knowledge/embeddings.py tests/test_embedding_selection.py
git commit -m "feat(knowledge): pure fail-closed embedding provider selection"
```

---

### Task 2: Local provider — e5-small, prefixes, offline-only load

**Files:**
- Modify: `src/mycelos/knowledge/embeddings.py` (`EmbeddingProvider`, `OpenAIEmbeddingProvider`, `LocalEmbeddingProvider`)
- Test: `tests/test_embedding_provider.py` (new)

**Interfaces:**
- Produces: module constants `LOCAL_MODEL_NAME = "intfloat/multilingual-e5-small"`, `LOCAL_MODEL_DIMENSION = 384`; `models_dir() -> Path` (returns `~/.mycelos/models`, honouring the app data dir convention used elsewhere — read how other modules resolve `~/.mycelos` and match it); `local_model_present() -> bool`; `LocalEmbeddingProvider.compute(text, *, is_query=False)`, `.compute_batch(texts, *, is_query=False)`, and `load()` which raises when the model is absent (never downloads). Task 3 consumes `local_model_present`, Task 4 consumes `models_dir` and the download entry point.
- Consumes: `select_provider_name` (Task 1) is NOT used here — wiring happens in Task 3.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_embedding_provider.py`:

```python
from __future__ import annotations

import pytest

from mycelos.knowledge.embeddings import (
    LOCAL_MODEL_DIMENSION,
    LOCAL_MODEL_NAME,
    LocalEmbeddingProvider,
    OpenAIEmbeddingProvider,
)


class _FakeEncoder:
    """Stands in for SentenceTransformer — records what it was asked to encode."""

    def __init__(self) -> None:
        self.seen: list = []

    def encode(self, text, **kwargs):
        self.seen.append(text)
        if isinstance(text, list):
            return [[0.1] * LOCAL_MODEL_DIMENSION for _ in text]
        return [0.1] * LOCAL_MODEL_DIMENSION


def _provider_with_fake() -> tuple[LocalEmbeddingProvider, _FakeEncoder]:
    provider = LocalEmbeddingProvider()
    fake = _FakeEncoder()
    provider._model = fake  # bypass loading; no download in tests
    return provider, fake


def test_model_is_multilingual_e5_small() -> None:
    assert LOCAL_MODEL_NAME == "intfloat/multilingual-e5-small"
    assert LOCAL_MODEL_DIMENSION == 384
    assert LocalEmbeddingProvider.dimension == 384


def test_document_gets_passage_prefix() -> None:
    provider, fake = _provider_with_fake()
    provider.compute("Kaffee entkalken")
    assert fake.seen == ["passage: Kaffee entkalken"]


def test_query_gets_query_prefix() -> None:
    provider, fake = _provider_with_fake()
    provider.compute("Kaffee", is_query=True)
    assert fake.seen == ["query: Kaffee"]


def test_batch_prefixes_every_text() -> None:
    provider, fake = _provider_with_fake()
    provider.compute_batch(["a", "b"], is_query=True)
    assert fake.seen == [["query: a", "query: b"]]


def test_load_never_downloads_when_model_absent(tmp_path, monkeypatch) -> None:
    import mycelos.knowledge.embeddings as emb

    monkeypatch.setattr(emb, "models_dir", lambda: tmp_path)
    provider = LocalEmbeddingProvider()
    with pytest.raises(FileNotFoundError):
        provider.load()


def test_openai_provider_accepts_and_ignores_is_query() -> None:
    class _FakeProxy:
        def http_post(self, url, body, credential):
            self.body = body
            return {"status": 200, "body": '{"data": [{"embedding": [0.5]}]}'}

    proxy = _FakeProxy()
    provider = OpenAIEmbeddingProvider(proxy)
    assert provider.compute("hallo", is_query=True) == [0.5]
    # No prefix leaks into the OpenAI request.
    assert proxy.body["input"] == "hallo"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `export PYTHONPATH=$PWD/src; python -m pytest tests/test_embedding_provider.py -v`
Expected: FAIL — `ImportError` on `LOCAL_MODEL_NAME`

- [ ] **Step 3: Implement**

In `embeddings.py`:

```python
LOCAL_MODEL_NAME = "intfloat/multilingual-e5-small"
LOCAL_MODEL_DIMENSION = 384
_QUERY_PREFIX = "query: "
_PASSAGE_PREFIX = "passage: "


def models_dir():
    """Directory holding downloaded embedding models."""
    from pathlib import Path
    return Path.home() / ".mycelos" / "models"


def local_model_present() -> bool:
    """True when the local model is on disk (no network check, no download)."""
    target = models_dir() / LOCAL_MODEL_NAME.replace("/", "__")
    return target.is_dir() and any(target.iterdir())
```

Change the base class signatures to accept the flag:

```python
    def compute(self, text: str, *, is_query: bool = False) -> list[float]:
        return []

    def compute_batch(self, texts: list[str], *, is_query: bool = False) -> list[list[float]]:
        return [self.compute(t, is_query=is_query) for t in texts]
```

`OpenAIEmbeddingProvider.compute` gains `*, is_query: bool = False` and ignores it (docstring: "OpenAI embeddings are symmetric; the flag exists for interface parity").

`LocalEmbeddingProvider`:

```python
class LocalEmbeddingProvider(EmbeddingProvider):
    """Local sentence-transformers embeddings (multilingual E5).

    The model is loaded from the pinned local directory only — this class
    never downloads at request time. Use ``mycelos embeddings setup`` to
    install the model.
    """
    name = "local"
    dimension = LOCAL_MODEL_DIMENSION

    def __init__(self) -> None:
        self._model = None

    def load(self):
        """Load the model from disk. Raises FileNotFoundError when absent."""
        if self._model is not None:
            return self._model
        if not local_model_present():
            raise FileNotFoundError(
                f"Embedding model {LOCAL_MODEL_NAME} is not installed "
                f"in {models_dir()} — run 'mycelos embeddings setup'"
            )
        from sentence_transformers import SentenceTransformer
        target = models_dir() / LOCAL_MODEL_NAME.replace("/", "__")
        self._model = SentenceTransformer(str(target), local_files_only=True)
        return self._model

    def compute(self, text: str, *, is_query: bool = False) -> list[float]:
        model = self.load()
        prefix = _QUERY_PREFIX if is_query else _PASSAGE_PREFIX
        return list(model.encode(prefix + text))

    def compute_batch(self, texts: list[str], *, is_query: bool = False) -> list[list[float]]:
        model = self.load()
        prefix = _QUERY_PREFIX if is_query else _PASSAGE_PREFIX
        vectors = model.encode([prefix + t for t in texts])
        return [list(v) for v in vectors]
```

(`SentenceTransformer.encode` returns numpy arrays; `list(...)` on a 1-D array yields floats. If the existing `.tolist()` style is cleaner against the installed version, use it — but keep the fake-encoder tests passing, which return plain lists.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `export PYTHONPATH=$PWD/src; python -m pytest tests/test_embedding_provider.py tests/test_embedding_selection.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add src/mycelos/knowledge/embeddings.py tests/test_embedding_provider.py
git commit -m "feat(knowledge): multilingual e5 local embeddings, offline-only load"
```

---

### Task 3: Wire selection into the service + query-side prefixes

**Files:**
- Modify: `src/mycelos/knowledge/embeddings.py` (`get_embedding_provider` — rewrite on top of `select_provider_name`)
- Modify: `src/mycelos/knowledge/service.py` (`_init_embedding_provider`; query call sites)
- Test: `tests/security/test_embedding_provider_selection.py` (new — sits with the EU residency tests)

**Interfaces:**
- Consumes: `select_provider_name`, `EUModeViolation`, `local_model_present` (Tasks 1-2).
- Produces: `get_embedding_provider(*, explicit=None, eu_mode=False, has_openai_credential=False, proxy_client=None) -> EmbeddingProvider` (keyword-only, no more `openai_key` string sentinel). `_init_embedding_provider` gathers facts and delegates.

- [ ] **Step 1: Write the failing tests**

Create `tests/security/test_embedding_provider_selection.py`:

```python
"""Provider selection must never send note text to a non-EU provider by
accident, and must never claim embeddings it cannot compute."""
from __future__ import annotations

import pytest

from mycelos.knowledge.embeddings import (
    EUModeViolation,
    FallbackProvider,
    LocalEmbeddingProvider,
    OpenAIEmbeddingProvider,
    get_embedding_provider,
)


class _FakeProxy:
    pass


def test_proxy_without_credential_does_not_select_openai(monkeypatch) -> None:
    """The June P1-7 defect: a proxy object is not a credential."""
    import mycelos.knowledge.embeddings as emb
    monkeypatch.setattr(emb, "local_model_present", lambda: False)
    provider = get_embedding_provider(
        has_openai_credential=False, proxy_client=_FakeProxy(), eu_mode=False
    )
    assert isinstance(provider, FallbackProvider)
    assert provider.dimension == 0


def test_credential_selects_openai(monkeypatch) -> None:
    provider = get_embedding_provider(
        has_openai_credential=True, proxy_client=_FakeProxy(), eu_mode=False
    )
    assert isinstance(provider, OpenAIEmbeddingProvider)


def test_eu_mode_never_selects_openai(monkeypatch) -> None:
    import mycelos.knowledge.embeddings as emb
    monkeypatch.setattr(emb, "local_model_present", lambda: True)
    provider = get_embedding_provider(
        has_openai_credential=True, proxy_client=_FakeProxy(), eu_mode=True
    )
    assert isinstance(provider, LocalEmbeddingProvider)


def test_eu_mode_with_explicit_openai_raises(monkeypatch) -> None:
    with pytest.raises(EUModeViolation):
        get_embedding_provider(
            explicit="openai", has_openai_credential=True,
            proxy_client=_FakeProxy(), eu_mode=True,
        )


def test_missing_model_degrades_without_download(monkeypatch) -> None:
    import mycelos.knowledge.embeddings as emb
    monkeypatch.setattr(emb, "local_model_present", lambda: False)

    def _explode(*args, **kwargs):
        raise AssertionError("must not touch sentence_transformers")

    monkeypatch.setattr(emb.LocalEmbeddingProvider, "load", _explode)
    provider = get_embedding_provider(has_openai_credential=False, eu_mode=True)
    assert isinstance(provider, FallbackProvider)
```

Plus, in `tests/test_knowledge_base.py`, one test that the query side asks for query embeddings (mirror the existing `_StubEmbeddingProvider` pattern — extend the stub to record `is_query`):

```python
def test_search_requests_query_side_embedding(app) -> None:
    kb, stub = _kb_with_recording_stub(app)  # implementer: extend existing helper
    kb.search("Kaffee")
    assert stub.query_flags and all(stub.query_flags), (
        "search must embed the query with is_query=True"
    )
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `export PYTHONPATH=$PWD/src; python -m pytest tests/security/test_embedding_provider_selection.py -v`
Expected: FAIL — `get_embedding_provider` has the old signature

- [ ] **Step 3: Implement**

Rewrite `get_embedding_provider`:

```python
def get_embedding_provider(
    *,
    explicit: str | None = None,
    eu_mode: bool = False,
    has_openai_credential: bool = False,
    proxy_client: Any = None,
) -> EmbeddingProvider:
    """Build the provider the configuration actually allows.

    Raises EUModeViolation when the configuration explicitly demands a
    non-EU provider under EU mode. Any other unmet prerequisite degrades
    to FallbackProvider (FTS-only search) — never a network download.
    """
    choice = select_provider_name(
        explicit, eu_mode, bool(has_openai_credential and proxy_client),
        local_model_present(),
    )
    if choice == "openai":
        return OpenAIEmbeddingProvider(proxy_client)
    if choice == "local":
        provider = LocalEmbeddingProvider()
        try:
            provider.load()
        except Exception as e:
            logger.warning("Local embedding model unavailable (%s) — FTS5 only", e)
            return FallbackProvider()
        return provider
    logger.info("No embedding provider selected — search uses FTS5 only")
    return FallbackProvider()
```

In `service.py`, `_init_embedding_provider` becomes fact-gathering:

```python
    def _init_embedding_provider(self):
        """Gather configuration facts and let embeddings.py decide."""
        from mycelos.knowledge.embeddings import (
            EUModeViolation, FallbackProvider, get_embedding_provider,
        )
        proxy = getattr(self._app, "proxy_client", None)
        has_credential = self._has_openai_credential(proxy)
        eu_mode = False
        try:
            from mycelos.llm.eu_mode import get_eu_mode
            eu_mode = get_eu_mode(self._app, "default")
        except Exception:
            pass
        explicit = None
        try:
            row = self._app.storage.fetchone(
                "SELECT value FROM knowledge_config WHERE key = 'embedding_provider_setting'"
            )
            explicit = row["value"] if row else None
        except Exception:
            pass
        try:
            return get_embedding_provider(
                explicit=explicit, eu_mode=eu_mode,
                has_openai_credential=has_credential, proxy_client=proxy,
            )
        except EUModeViolation as e:
            logger.error("Embedding configuration refused: %s", e)
            return FallbackProvider()
```

Add `_has_openai_credential(proxy)`: ask the credential proxy whether the `openai` credential exists. **Read how other call sites query credential presence** (`security/proxy_client.py` / how `_PROVIDER_MAP` consumers check) and use that mechanism; on any exception return `False` (fail closed). Do NOT infer presence from the proxy object existing.

Note the config key is `embedding_provider_setting` — distinct from the existing `embedding_provider` stamp written by `_ensure_vec_table` (which records what was *used*, not what was *asked for*). Do not conflate them.

Query-side prefixes: in `service.py`, `_find_relevant_by_vector` computes the query embedding — change that call to `compute(text, is_query=True)`. The write paths (`write`, `update`) keep the document default. `find_duplicates` builds a query text from a note; it also passes `is_query=True`.

- [ ] **Step 4: Run tests to verify they pass, then the affected suites**

Run: `export PYTHONPATH=$PWD/src; python -m pytest tests/security/test_embedding_provider_selection.py tests/test_embedding_provider.py tests/test_embedding_selection.py tests/test_knowledge_base.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add src/mycelos/knowledge/embeddings.py src/mycelos/knowledge/service.py tests/security/test_embedding_provider_selection.py tests/test_knowledge_base.py
git commit -m "fix(knowledge): fail-closed provider selection, query-side E5 prefixes"
```

---

### Task 4: `mycelos embeddings setup` CLI + doctor probe

**Files:**
- Create or modify: the CLI module holding subcommands (`src/mycelos/cli/` — read the existing command registration pattern first and follow it; add `embeddings_cmd.py` alongside its siblings)
- Modify: `src/mycelos/doctor/` (embeddings probe — follow the existing check pattern)
- Modify: `src/mycelos/i18n/` locale files (en + de keys in the same step)
- Test: `tests/test_embeddings_cli.py` (new)

**Interfaces:**
- Consumes: `models_dir()`, `LOCAL_MODEL_NAME`, `local_model_present()` (Task 2).
- Produces: `mycelos embeddings setup [--yes]` and `mycelos embeddings status`; a doctor check reporting provider, model presence, dimension, and vector-vs-note count.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_embeddings_cli.py` (mirror the invocation style of the existing CLI tests — read one first, e.g. `tests/test_knowledge_cli_export.py`):

```python
def test_setup_refuses_without_confirmation(monkeypatch, tmp_path) -> None:
    """Download is explicit: no --yes and no interactive confirm → no download."""
    ...  # implementer: assert the downloader was never invoked


def test_setup_downloads_to_models_dir(monkeypatch, tmp_path) -> None:
    """--yes downloads into models_dir(), not the global HF cache."""
    ...  # monkeypatch the SentenceTransformer constructor/save; assert target path


def test_status_reports_absent_model(monkeypatch, tmp_path) -> None:
    ...  # assert exit code 0 and a message naming the model and the directory
```

The `...` blocks are implementer-completed to match the project's CLI test conventions; the assertions named in the docstrings are the contract. No test may perform a real download.

- [ ] **Step 2: Run tests to verify they fail**

Run: `export PYTHONPATH=$PWD/src; python -m pytest tests/test_embeddings_cli.py -v`
Expected: FAIL — command does not exist

- [ ] **Step 3: Implement**

- `mycelos embeddings status`: prints selected provider, model name, whether the model is present, `models_dir()`, dimension, and (when the KB is reachable) vector row count vs. note count.
- `mycelos embeddings setup [--yes]`: prints model name, approximate size (~120 MB), and target directory; asks for confirmation unless `--yes`; sets `SENTENCE_TRANSFORMERS_HOME` to `models_dir()`; downloads via `SentenceTransformer(LOCAL_MODEL_NAME)` and saves to `models_dir() / LOCAL_MODEL_NAME.replace("/", "__")`; verifies with a probe encode; reports success and reminds that the next knowledge access re-embeds existing notes.
- All user-facing strings via `t()`; add the keys to **both** en and de locale files in this step.
- Doctor probe: reports the same facts as `status` in the doctor's result shape; warns when the selected provider is `none` while notes exist, and when vector count is far below note count (backfill pending).

- [ ] **Step 4: Run tests to verify they pass**

Run: `export PYTHONPATH=$PWD/src; python -m pytest tests/test_embeddings_cli.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add src/mycelos/cli src/mycelos/doctor src/mycelos/i18n tests/test_embeddings_cli.py
git commit -m "feat(cli): mycelos embeddings setup/status and doctor probe"
```

---

### Task 5: Re-embed on provider/model change

**Files:**
- Modify: `src/mycelos/knowledge/service.py` (`_ensure_vec_table` + new `_backfill_embeddings`)
- Test: `tests/test_knowledge_base.py`

**Interfaces:**
- Consumes: `compute_batch` (Task 2), the existing `knowledge_config` stamps.
- Produces: `_backfill_embeddings() -> int` (number of notes embedded). New stamp key `embedding_model`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_knowledge_base.py` (reuse the stub-provider helpers):

```python
def test_model_change_triggers_full_reembed(app) -> None:
    """Same dimension, different model → vectors must be rebuilt."""
    kb, stub = _kb_with_recording_stub(app)  # 384d stub, model name "stub-a"
    kb.write(title="Eins", content="inhalt", topic="notes")
    kb.write(title="Zwei", content="inhalt", topic="notes")
    ...  # switch the stub's model name to "stub-b", construct a new KnowledgeBase
    rows = app.storage.fetchone("SELECT COUNT(*) AS c FROM knowledge_vec")
    notes = app.storage.fetchone(
        "SELECT COUNT(*) AS c FROM knowledge_notes WHERE type != 'topic'"
    )
    assert rows["c"] == notes["c"]


def test_unchanged_stamps_do_not_reembed(app) -> None:
    """Steady state: no re-embedding work on every startup."""
    ...  # construct twice with identical stamps; assert compute_batch not called the second time
```

The stub provider needs a `name`/model identity the service can stamp — extend the existing stub minimally rather than inventing a new fixture.

- [ ] **Step 2: Run tests to verify they fail**

Run: `export PYTHONPATH=$PWD/src; python -m pytest tests/test_knowledge_base.py -v -k "reembed"`
Expected: FAIL — model change unnoticed, vec table empty after rebuild

- [ ] **Step 3: Implement**

In `_ensure_vec_table`: read the stored `embedding_dimension`, `embedding_provider`, and new `embedding_model` stamps; treat a mismatch in ANY of them as a rebuild trigger (drop + recreate + backfill). Write all three stamps after creating the table. The model identity comes from the provider — add a `model_id` attribute to the providers (`LOCAL_MODEL_NAME` for local, `"text-embedding-3-small"` for OpenAI, `""` for fallback) rather than guessing in the service.

Add:

```python
    def _backfill_embeddings(self) -> int:
        """Re-embed every note after a provider/model/dimension change.

        Single-user deployment: old vectors are disposable, so this is a
        full rebuild rather than an incremental migration.
        """
        if self._embedding_provider.dimension == 0:
            return 0
        notes = self._app.storage.fetchall(
            "SELECT id, path, title FROM knowledge_notes WHERE type != 'topic'"
        )
        if not notes:
            return 0
        from mycelos.knowledge.embeddings import serialize_embedding
        done = 0
        batch_size = 32
        for start in range(0, len(notes), batch_size):
            chunk = notes[start:start + batch_size]
            texts = [f"{n['title']} {self._note_body(n['path'])}" for n in chunk]
            try:
                vectors = self._embedding_provider.compute_batch(texts)
            except Exception as e:
                logger.warning("Embedding backfill failed at offset %d: %s", start, e)
                break
            for note, vector in zip(chunk, vectors):
                if not vector:
                    continue
                self._app.storage.execute(
                    "INSERT OR REPLACE INTO knowledge_vec(rowid, embedding) VALUES (?, ?)",
                    (note["id"], serialize_embedding(vector)),
                )
                done += 1
        logger.info("Re-embedded %d notes with %s", done, self._embedding_provider.name)
        return done
```

(`_note_body` was added by the WP1 FTS rebuild — reuse it. If its name differs after review, use the actual helper.)

- [ ] **Step 4: Run tests to verify they pass, then the affected suites**

Run: `export PYTHONPATH=$PWD/src; python -m pytest tests/test_knowledge_base.py tests/test_ranking.py tests/test_embedding_provider.py tests/test_embedding_selection.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add src/mycelos/knowledge/service.py src/mycelos/knowledge/embeddings.py tests/test_knowledge_base.py
git commit -m "feat(knowledge): re-embed all notes when provider or model changes"
```

---

### Task 6: CHANGELOG + verification

**Files:**
- Modify: `CHANGELOG.md`

- [ ] **Step 1: Add the CHANGELOG entry** under the existing `## Week 32 (2026)` heading:

```markdown
### Local embeddings are the reliable default

- **Provider selection is deterministic and fail-closed.** Previously any
  proxy client counted as an OpenAI key, so a setup without an OpenAI
  credential still selected the OpenAI provider and every embedding call
  silently returned nothing — semantic search was dead without a single
  error. Selection now asks for a real credential and degrades
  openai → local → none, with EU mode never reaching a US provider.
- **Multilingual model.** Local embeddings use
  `intfloat/multilingual-e5-small` with correct E5 role prefixes
  (`query:` / `passage:`) instead of the English-centric MiniLM — noticeably
  better recall on German notes.
- **No implicit downloads.** The model is installed explicitly via
  `mycelos embeddings setup`; a missing model degrades to FTS-only search
  instead of pulling ~120 MB inside a request. `mycelos embeddings status`
  and `mycelos doctor` report provider, model, and backfill state.
- **Automatic re-embedding.** Changing provider, model, or dimension now
  rebuilds every note's vector instead of leaving the vector index silently
  empty.
```

- [ ] **Step 2: Run the security suite**

Run: `export PYTHONPATH=$PWD/src; python -m pytest tests/security/ -q`
Expected: all PASS

- [ ] **Step 3: Run the knowledge + CLI suites**

Run: `export PYTHONPATH=$PWD/src; python -m pytest tests/test_knowledge_base.py tests/test_ranking.py tests/test_embedding_provider.py tests/test_embedding_selection.py tests/test_embeddings_cli.py tests/test_organizer_api.py tests/test_knowledge_organizer_handler.py -q`
Expected: all PASS

- [ ] **Step 4: Commit**

```bash
git add CHANGELOG.md
git commit -m "docs: changelog for local embeddings (W32)"
```
