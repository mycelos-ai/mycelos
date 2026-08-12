"""Embedding providers for Knowledge Base semantic search."""

from __future__ import annotations
import logging
import struct
from pathlib import Path
from typing import Any

logger = logging.getLogger("mycelos.knowledge")

LOCAL_MODEL_NAME = "intfloat/multilingual-e5-small"
LOCAL_MODEL_DIMENSION = 384
_QUERY_PREFIX = "query: "
_PASSAGE_PREFIX = "passage: "


def models_dir() -> Path:
    """Directory holding downloaded embedding models.

    Lives under the same Mycelos data directory as everything else
    (``$MYCELOS_DATA_DIR`` override, else ``~/.mycelos``).
    """
    from mycelos.cli import default_data_dir

    return default_data_dir() / "models"


def local_model_present() -> bool:
    """True when the local model is on disk (no network check, no download)."""
    target = models_dir() / LOCAL_MODEL_NAME.replace("/", "__")
    return target.is_dir() and any(target.iterdir())


class EmbeddingProvider:
    """Base class for embedding providers."""
    name: str = "none"
    dimension: int = 0

    def compute(self, text: str, *, is_query: bool = False) -> list[float]:
        return []

    def compute_batch(self, texts: list[str], *, is_query: bool = False) -> list[list[float]]:
        return [self.compute(t, is_query=is_query) for t in texts]


class OpenAIEmbeddingProvider(EmbeddingProvider):
    """Uses OpenAI text-embedding-3-small via SecurityProxy."""
    name = "openai"
    dimension = 1536

    def __init__(self, proxy_client: Any):
        self._proxy = proxy_client

    def compute(self, text: str, *, is_query: bool = False) -> list[float]:
        # OpenAI embeddings are symmetric; the flag exists for interface
        # parity with LocalEmbeddingProvider. No prefix is added.
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
        # SentenceTransformer.encode declares list[Tensor] | ndarray | Tensor;
        # with the default convert_to_numpy=True (unchanged here) a single
        # string input always yields a 1-D ndarray, so list(...) is floats.
        return list(model.encode(prefix + text))

    def compute_batch(self, texts: list[str], *, is_query: bool = False) -> list[list[float]]:
        model = self.load()
        prefix = _QUERY_PREFIX if is_query else _PASSAGE_PREFIX
        # Same convert_to_numpy=True default: a list input yields a 2-D
        # ndarray, so each row converts cleanly to list[float].
        vectors = model.encode([prefix + t for t in texts])
        return [list(v) for v in vectors]


class FallbackProvider(EmbeddingProvider):
    """No embeddings available — search uses FTS5 only."""
    name = "none"
    dimension = 0


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


def serialize_embedding(embedding: list[float]) -> bytes:
    """Serialize float list to bytes for sqlite-vec."""
    return struct.pack(f"{len(embedding)}f", *embedding)


def deserialize_embedding(data: bytes, dimension: int) -> list[float]:
    """Deserialize bytes to float list."""
    return list(struct.unpack(f"{dimension}f", data))
