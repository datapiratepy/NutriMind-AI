"""Retriever: query embedding → top-k search → threshold → citations.

The ``grounded`` flag drives the honest-grounding policy (ARCHITECTURE.md
§4.4): when no chunk clears the similarity threshold, the Knowledge Agent
answers from general knowledge with an explicit label — never pretending weak
matches are evidence.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from nutrimind.retrieval.vector_store import RetrievedChunk, VectorStore

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RetrievalResult:
    """Outcome of one retrieval pass."""

    query: str
    chunks: list[RetrievedChunk] = field(default_factory=list)
    grounded: bool = False
    provider: str = ""
    threshold: float = 0.0

    def citations(self) -> list[dict]:
        """Deduplicated (filename, page) pairs in relevance order."""
        seen: set[tuple[str, int]] = set()
        citations: list[dict] = []
        for chunk in self.chunks:
            key = (chunk.filename, chunk.page)
            if key not in seen:
                seen.add(key)
                citations.append(chunk.citation())
        return citations

    def context_text(self) -> str:
        """Numbered passages block for grounded prompts (Phase 6)."""
        return "\n\n".join(
            f"[{index}] (from {c.filename}, page {c.page})\n{c.text}"
            for index, c in enumerate(self.chunks, start=1)
        )

    def to_dict(self) -> dict:
        return {
            "query": self.query,
            "grounded": self.grounded,
            "provider": self.provider,
            "threshold": self.threshold,
            "chunks": [c.to_dict() for c in self.chunks],
            "citations": self.citations(),
        }


class Retriever:
    """Thin, configurable search facade over one vector store."""

    def __init__(self, vector_store: VectorStore, *, top_k: int,
                 similarity_threshold: float) -> None:
        self._store = vector_store
        self._top_k = top_k
        self._threshold = similarity_threshold

    def retrieve(self, query: str, *, top_k: int | None = None) -> RetrievalResult:
        """Search and apply the grounding threshold."""
        limit = top_k or self._top_k
        embedding = self._store.provider.embed_query(query)
        hits = self._store.query(embedding, top_k=limit)
        kept = [h for h in hits if h.similarity >= self._threshold]
        logger.info("retrieve %r: %d hits, %d above threshold %.2f (best %.3f)",
                    query[:60], len(hits), len(kept), self._threshold,
                    hits[0].similarity if hits else 0.0)
        return RetrievalResult(
            query=query,
            chunks=kept,
            grounded=bool(kept),
            provider=self._store.provider.name,
            threshold=self._threshold,
        )
