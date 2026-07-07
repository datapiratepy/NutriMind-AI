"""Embedding providers: watsonx (primary), sentence-transformers, hash fallback.

Vector spaces are incompatible across providers, so each gets its own Chroma
collection (``kb_<provider>``) — resolution here decides which one is active.

Resolution for ``EMBEDDINGS_PROVIDER``:

* ``watsonx`` — IBM Granite embeddings; requires credentials (fail fast).
* ``local``   — sentence-transformers MiniLM; requires the optional dependency.
* ``auto``    — watsonx if credentials exist, else sentence-transformers if
  installed, else the dependency-free **hash** provider so the whole pipeline
  (upload → index → search → citations) still works mechanically in demo
  environments. The active provider is always visible in /api/system/info.
"""

from __future__ import annotations

import hashlib
import logging
import random
from abc import ABC, abstractmethod
from typing import Sequence

from nutrimind.config import Settings
from nutrimind.exceptions import ConfigurationError

logger = logging.getLogger(__name__)

_EMBED_BATCH_SIZE = 16  # keeps individual watsonx requests small


class EmbeddingProvider(ABC):
    """Contract shared by all embedding backends."""

    #: short identifier — also the Chroma collection suffix.
    name: str = "unset"

    @abstractmethod
    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        """One vector per text (used at ingestion time)."""

    @abstractmethod
    def embed_query(self, text: str) -> list[float]:
        """Vector for a search query (must share the document vector space)."""


class WatsonxEmbeddingProvider(EmbeddingProvider):
    """IBM Granite embeddings via the existing WatsonxClient (768-dim)."""

    name = "watsonx"

    def __init__(self, settings: Settings) -> None:
        if not settings.watsonx.has_credentials:
            raise ConfigurationError(
                "EMBEDDINGS_PROVIDER=watsonx requires IBM credentials.",
                hint="Fill .env (docs/IBM_SETUP.md) or use EMBEDDINGS_PROVIDER=auto.",
            )
        from nutrimind.services.llm.watsonx_client import WatsonxClient

        self._client = WatsonxClient(settings.watsonx)

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for start in range(0, len(texts), _EMBED_BATCH_SIZE):
            vectors.extend(self._client.embed(texts[start:start + _EMBED_BATCH_SIZE]))
        return vectors

    def embed_query(self, text: str) -> list[float]:
        return self._client.embed([text])[0]


class LocalEmbeddingProvider(EmbeddingProvider):
    """sentence-transformers MiniLM (384-dim) — offline, optional dependency."""

    name = "local"
    _MODEL_ID = "sentence-transformers/all-MiniLM-L6-v2"

    def __init__(self) -> None:
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError:
            raise ConfigurationError(
                "EMBEDDINGS_PROVIDER=local requires the optional dependency.",
                hint="pip install sentence-transformers (large: pulls PyTorch).",
            ) from None
        self._model = SentenceTransformer(self._MODEL_ID)

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        return [vector.tolist() for vector in self._model.encode(list(texts))]

    def embed_query(self, text: str) -> list[float]:
        return self._model.encode([text])[0].tolist()


class HashEmbeddingProvider(EmbeddingProvider):
    """Deterministic pseudo-embeddings (384-dim unit vectors from SHA-256).

    Semantically naive by design: keeps the RAG pipeline mechanically
    functional (and testable) with zero credentials and zero heavy
    dependencies. Never silently selected — resolution logs it and
    /api/system/info reports it.
    """

    name = "hash"
    _DIM = 384

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        return [self._vector(text) for text in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._vector(text)

    @staticmethod
    def _vector(text: str) -> list[float]:
        seed = hashlib.sha256(text.encode("utf-8")).digest()
        rng = random.Random(seed)
        values = [rng.uniform(-1.0, 1.0) for _ in range(HashEmbeddingProvider._DIM)]
        norm = sum(v * v for v in values) ** 0.5 or 1.0
        return [v / norm for v in values]


def _local_available() -> bool:
    try:
        import sentence_transformers  # noqa: F401

        return True
    except ImportError:
        return False


def resolve_embedding_provider(settings: Settings) -> EmbeddingProvider:
    """Pick the embedding backend per configuration (see module docstring)."""
    requested = settings.embeddings_provider
    if requested == "watsonx":
        return WatsonxEmbeddingProvider(settings)
    if requested == "local":
        return LocalEmbeddingProvider()

    # auto
    if settings.watsonx.has_credentials:
        return WatsonxEmbeddingProvider(settings)
    if _local_available():
        logger.info("embeddings: auto -> local (no IBM credentials)")
        return LocalEmbeddingProvider()
    logger.warning("embeddings: auto -> hash fallback (no credentials, "
                   "sentence-transformers not installed) — retrieval quality "
                   "is mechanical only")
    return HashEmbeddingProvider()
