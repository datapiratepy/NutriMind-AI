"""Unit tests for embedding providers and resolution."""

import math

from nutrimind.config import load_settings
from nutrimind.retrieval.embeddings import (
    HashEmbeddingProvider,
    resolve_embedding_provider,
)


def test_hash_provider_deterministic_unit_vectors():
    provider = HashEmbeddingProvider()
    first = provider.embed_query("paneer protein content")
    second = provider.embed_query("paneer protein content")
    assert first == second
    assert len(first) == 384
    assert math.isqrt(1) and abs(sum(v * v for v in first) - 1.0) < 1e-9


def test_hash_documents_match_queries():
    provider = HashEmbeddingProvider()
    docs = provider.embed_documents(["alpha", "beta"])
    assert docs[0] == provider.embed_query("alpha")
    assert docs[0] != docs[1]


def test_auto_resolution_falls_back_to_hash(monkeypatch):
    """No credentials + no sentence-transformers -> hash provider."""
    monkeypatch.setenv("EMBEDDINGS_PROVIDER", "auto")
    monkeypatch.delenv("WATSONX_APIKEY", raising=False)
    monkeypatch.delenv("WATSONX_PROJECT_ID", raising=False)
    settings = load_settings(ensure_dirs=False)
    provider = resolve_embedding_provider(settings)
    assert provider.name in ("hash", "local")  # local only if optional dep installed
