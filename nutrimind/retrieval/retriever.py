"""Retriever: query embedding → top-k search → threshold → citations.

The ``grounded`` flag drives the honest-grounding policy (ARCHITECTURE.md
§4.4): when no chunk clears the similarity threshold, the Knowledge Agent
answers from general knowledge with an explicit label — never pretending weak
matches are evidence.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
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
    #: False when the active provider matches words rather than meaning. The
    #: UI needs this to explain an empty result honestly instead of blaming
    #: the threshold, which is what it used to do.
    semantic: bool = True
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
        """Numbered passages block for grounded prompts."""
        return "\n\n".join(
            f"[{index}] (from {c.filename}, page {c.page})\n{c.text}"
            for index, c in enumerate(self.chunks, start=1)
        )

    def to_dict(self) -> dict:
        return {
            "query": self.query,
            "grounded": self.grounded,
            "provider": self.provider,
            "semantic": self.semantic,
            "threshold": round(self.threshold, 4),
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

    def retrieve(self, query: str, *, top_k: int | None = None,
                 document_ids: Sequence[int] | None = None) -> RetrievalResult:
        """Search and apply the grounding threshold.

        :param document_ids: documents the caller is allowed to see; see
            :meth:`VectorStore.query`. Passed straight through so that the
            ownership filter is applied by the store rather than by discarding
            forbidden hits afterwards — filtering after the fact would silently
            shrink top-k and could return nothing while relevant owned passages
            existed just outside the window.
        """
        limit = top_k or self._top_k
        provider = self._store.provider
        embedding = provider.embed_query(query)
        hits = self._store.query(embedding, top_k=limit, document_ids=document_ids)
        # Two gates, not one. The threshold asks "is this close enough in the
        # vector space"; provider.matches() asks "does the space's answer survive
        # contact with the actual text". For semantic providers the second is a
        # no-op; for the approximate lexical space it removes hash collisions
        # that would otherwise be presented as cited evidence.
        kept = [h for h in hits
                if h.similarity >= self._threshold and provider.matches(query, h.text)]
        logger.info("retrieve %r: %d hits, %d above threshold %.2f (best %.3f)",
                    query[:60], len(hits), len(kept), self._threshold,
                    hits[0].similarity if hits else 0.0)
        return RetrievalResult(
            query=query,
            chunks=kept,
            grounded=bool(kept),
            provider=provider.name,
            semantic=provider.semantic,
            threshold=self._threshold,
        )
