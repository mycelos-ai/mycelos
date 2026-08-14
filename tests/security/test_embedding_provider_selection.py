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
    # Selection must reach LocalEmbeddingProvider without ever touching
    # sentence_transformers — no model is installed on the test machine.
    monkeypatch.setattr(emb.LocalEmbeddingProvider, "load", lambda self: object())
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
