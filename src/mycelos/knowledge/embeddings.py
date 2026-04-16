"""Embedding providers for Knowledge Base semantic search."""

from __future__ import annotations
import logging
import struct
from typing import Any

logger = logging.getLogger("mycelos.knowledge")


class EmbeddingProvider:
    """Base class for embedding providers."""
    name: str = "none"
    dimension: int = 0

    def compute(self, text: str) -> list[float]:
        return []

    def compute_batch(self, texts: list[str]) -> list[list[float]]:
        return [self.compute(t) for t in texts]


class OpenAIEmbeddingProvider(EmbeddingProvider):
    """Uses OpenAI text-embedding-3-small via SecurityProxy."""
    name = "openai"
    dimension = 1536

    def __init__(self, proxy_client: Any):
        self._proxy = proxy_client

    def compute(self, text: str) -> list[float]:
        # Call OpenAI embeddings API via proxy /http endpoint
        result = self._proxy.http_post(
            "https://api.openai.com/v1/embeddings",
            body={"input": text, "model": "text-embedding-3-small"},
            credential="openai",
        )
        if isinstance(result, dict) and result.get("status") == 200:
            import json
            body = json.loads(result.get("body", "{}"))
            data = body.get("data", [])
            if data:
                return data[0].get("embedding", [])
        return []


class LocalEmbeddingProvider(EmbeddingProvider):
    """Uses sentence-transformers all-MiniLM-L6-v2 locally."""
    name = "local"
    dimension = 384

    def __init__(self):
        self._model = None

    def _load_model(self):
        if self._model is None:
            from sentence_transformers import SentenceTransformer
            self._model = SentenceTransformer("all-MiniLM-L6-v2")

    def compute(self, text: str) -> list[float]:
        self._load_model()
        return self._model.encode(text).tolist()


class FallbackProvider(EmbeddingProvider):
    """No embeddings available — search uses FTS5 only."""
    name = "none"
    dimension = 0


def get_embedding_provider(openai_key: str | None = None,
                            proxy_client: Any = None) -> EmbeddingProvider:
    """Get the best available embedding provider."""
    if openai_key and proxy_client:
        return OpenAIEmbeddingProvider(proxy_client)
    try:
        provider = LocalEmbeddingProvider()
        provider._load_model()
        return provider
    except Exception as e:
        logger.info("No embedding provider available (%s) — using FTS5 only", e)
        return FallbackProvider()


def serialize_embedding(embedding: list[float]) -> bytes:
    """Serialize float list to bytes for sqlite-vec."""
    return struct.pack(f"{len(embedding)}f", *embedding)


def deserialize_embedding(data: bytes, dimension: int) -> list[float]:
    """Deserialize bytes to float list."""
    return list(struct.unpack(f"{dimension}f", data))
