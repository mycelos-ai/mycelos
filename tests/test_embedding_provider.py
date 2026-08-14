from __future__ import annotations

import pytest

from mycelos.knowledge.embeddings import (
    LOCAL_MODEL_DIMENSION,
    LOCAL_MODEL_NAME,
    FallbackProvider,
    LocalEmbeddingProvider,
    OpenAIEmbeddingProvider,
    get_embedding_provider,
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


def test_get_embedding_provider_loads_local_when_model_present(monkeypatch) -> None:
    # Regression: get_embedding_provider() must call the current load()
    # entry point. It previously called the removed _load_model(), which
    # raised AttributeError, got swallowed by the bare except, and silently
    # downgraded every caller to FallbackProvider even with the model
    # installed.
    import mycelos.knowledge.embeddings as emb

    monkeypatch.setattr(emb, "local_model_present", lambda: True)

    fake = _FakeEncoder()
    calls: list = []

    def _fake_load(self):
        calls.append(self)
        self._model = fake
        return fake

    monkeypatch.setattr(LocalEmbeddingProvider, "load", _fake_load)

    provider = get_embedding_provider(
        has_openai_credential=False, proxy_client=None, eu_mode=False
    )

    assert isinstance(provider, LocalEmbeddingProvider)
    assert not isinstance(provider, FallbackProvider)
    assert calls, "LocalEmbeddingProvider.load() was not called"


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
