"""ChromaDB wrapper: persistent client, per-provider collections, metadata.

Each embedding provider owns collection ``kb_<provider>`` so incompatible
vector spaces never mix (ARCHITECTURE.md §4.3). Collections use cosine
space; Chroma returns *distances*, which we convert to ``similarity = 1 - d``
so thresholds read naturally (1.0 = identical).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from nutrimind.exceptions import ConfigurationError, RetrievalError
from nutrimind.retrieval.chunker import Chunk
from nutrimind.retrieval.embeddings import EmbeddingProvider

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RetrievedChunk:
    """A search hit with citation-grade metadata."""

    text: str
    similarity: float
    document_id: int
    filename: str
    page: int
    chunk_index: int

    def citation(self) -> dict:
        """The citation shape the chat UI renders (filename + page)."""
        return {"filename": self.filename, "page": self.page}

    def to_dict(self) -> dict:
        return {
            "text": self.text,
            "similarity": round(self.similarity, 3),
            "document_id": self.document_id,
            "filename": self.filename,
            "page": self.page,
            "chunk_index": self.chunk_index,
        }


class VectorStore:
    """Persistent Chroma collection bound to one embedding provider."""

    def __init__(self, chroma_dir: Path, provider: EmbeddingProvider) -> None:
        try:
            import chromadb
            from chromadb.config import Settings as ChromaSettings
        except ImportError:
            raise ConfigurationError(
                "ChromaDB is not installed.",
                hint="pip install -r requirements.txt",
            ) from None

        self.provider = provider
        self.collection_name = f"kb_{provider.name}"
        self._client = chromadb.PersistentClient(
            path=str(chroma_dir),
            settings=ChromaSettings(anonymized_telemetry=False),
        )
        self._collection = self._client.get_or_create_collection(
            name=self.collection_name, metadata={"hnsw:space": "cosine"}
        )
        logger.info("vector store ready: %s at %s (%d chunks)",
                    self.collection_name, chroma_dir, self.count())

    # -- writes ---------------------------------------------------------------

    def add_chunks(
        self,
        *,
        document_id: int,
        filename: str,
        uploaded_at: str,
        chunks: Sequence[Chunk],
        embeddings: Sequence[Sequence[float]],
    ) -> None:
        """Index chunks with the full metadata contract (6 fields per chunk)."""
        if len(chunks) != len(embeddings):
            raise RetrievalError("Chunk/embedding count mismatch during indexing.")
        if not chunks:
            return
        self._collection.add(
            ids=[f"doc{document_id}:chunk{c.chunk_index}" for c in chunks],
            documents=[c.text for c in chunks],
            embeddings=[list(vector) for vector in embeddings],
            metadatas=[{
                "document_id": document_id,
                "filename": filename,
                "page": c.page,
                "chunk_index": c.chunk_index,
                "uploaded_at": uploaded_at,
                "provider": self.provider.name,
            } for c in chunks],
        )

    def delete_document(self, document_id: int) -> None:
        """Remove every chunk belonging to a document."""
        self._collection.delete(where={"document_id": {"$eq": document_id}})

    # -- reads ----------------------------------------------------------------

    def query(self, embedding: Sequence[float], *, top_k: int) -> list[RetrievedChunk]:
        """Nearest chunks for a query vector, best first."""
        if self.count() == 0:
            return []
        try:
            result = self._collection.query(
                query_embeddings=[list(embedding)],
                n_results=min(top_k, self.count()),
                include=["documents", "metadatas", "distances"],
            )
        except Exception as exc:  # noqa: BLE001
            raise RetrievalError(f"Vector search failed: {exc}") from exc

        hits: list[RetrievedChunk] = []
        # strict=True: Chroma returns these three lists per query and they are
        # positionally paired. If they ever differ in length the pairing is
        # meaningless, so fail loudly rather than silently truncating results.
        for text, metadata, distance in zip(result["documents"][0],
                                            result["metadatas"][0],
                                            result["distances"][0], strict=True):
            hits.append(RetrievedChunk(
                text=text,
                similarity=1.0 - float(distance),
                document_id=int(metadata["document_id"]),
                filename=str(metadata["filename"]),
                page=int(metadata["page"]),
                chunk_index=int(metadata["chunk_index"]),
            ))
        return hits

    def count(self) -> int:
        return self._collection.count()

    def count_for_document(self, document_id: int) -> int:
        got = self._collection.get(where={"document_id": {"$eq": document_id}},
                                   include=[])
        return len(got["ids"])
