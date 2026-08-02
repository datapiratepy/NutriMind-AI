"""RAGService: the service-layer facade over the retrieval package.

Routes and agents talk to this class only — they never touch
Chroma, pypdf or embedding providers directly. One instance per process,
bound to the resolved embedding provider and its collection.
"""

from __future__ import annotations

import logging
import threading
import uuid
from pathlib import Path

from werkzeug.datastructures import FileStorage
from werkzeug.utils import secure_filename

from nutrimind.config import Settings, load_settings
from nutrimind.exceptions import ValidationError
from nutrimind.extensions import db
from nutrimind.models import Document
from nutrimind.retrieval.embeddings import resolve_embedding_provider
from nutrimind.retrieval.ingestion import (
    ingest_pdf,
    reindex_document,
    resolve_document_path,
)
from nutrimind.retrieval.retriever import RetrievalResult, Retriever
from nutrimind.retrieval.vector_store import VectorStore

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_service: "RAGService | None" = None


class RAGService:
    """Upload, index, re-index, delete and search knowledge documents."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self.provider = resolve_embedding_provider(settings)
        self.store = VectorStore(settings.chroma_dir, self.provider)
        self.retriever = Retriever(
            self.store,
            top_k=settings.rag.top_k,
            similarity_threshold=settings.rag.similarity_threshold,
        )

    # -- ingestion ------------------------------------------------------------

    def ingest_upload(self, file: FileStorage,
                      condition_tags: list[str] | None = None) -> Document:
        """Validate and index an uploaded PDF (stored under instance/uploads)."""
        original = secure_filename(file.filename or "")
        if not original or not original.lower().endswith(".pdf"):
            raise ValidationError("Only PDF files are accepted.",
                                  hint="Upload a .pdf document.")
        if file.mimetype not in ("application/pdf", "application/octet-stream"):
            raise ValidationError(f"Unexpected content type '{file.mimetype}'.")

        name = f"{uuid.uuid4().hex}.pdf"
        stored = f"instance/uploads/{name}"
        target = self._settings.instance_dir / "uploads" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        file.save(target)
        try:
            return ingest_pdf(target, original_filename=original,
                              stored_name=stored, settings=self._settings,
                              vector_store=self.store,
                              condition_tags=condition_tags)
        except ValidationError:
            target.unlink(missing_ok=True)  # duplicate — keep disk clean
            raise

    def ingest_path(self, path: Path,
                    condition_tags: list[str] | None = None) -> Document:
        """Index a PDF already on disk (seed script; path kept in place)."""
        stored = str(path.relative_to(self._settings.base_dir)).replace("\\", "/")
        return ingest_pdf(path, original_filename=path.name, stored_name=stored,
                          settings=self._settings, vector_store=self.store,
                          condition_tags=condition_tags)

    def reindex(self, document: Document) -> Document:
        return reindex_document(document, settings=self._settings,
                                vector_store=self.store)

    def remove(self, document: Document) -> None:
        """Delete vectors, the DB row, and (for uploads) the stored file."""
        self.store.delete_document(document.id)
        path = resolve_document_path(document, self._settings)
        if document.stored_name.startswith("instance/uploads/"):
            path.unlink(missing_ok=True)  # seed files in knowledge_base/ stay
        db.session.delete(document)
        db.session.commit()

    # -- search ---------------------------------------------------------------

    def retrieve(self, query: str, *, top_k: int | None = None) -> RetrievalResult:
        """Threshold-filtered retrieval with citations (used by Phase-6 agents)."""
        return self.retriever.retrieve(query, top_k=top_k)

    # -- diagnostics ----------------------------------------------------------

    def status(self) -> dict:
        return {
            "provider": self.provider.name,
            "collection": self.store.collection_name,
            "chunks": self.store.count(),
        }


def get_rag_service(settings: Settings | None = None, *,
                    refresh: bool = False) -> RAGService:
    """Process-wide singleton (mirrors the LLM factory pattern)."""
    global _service
    with _lock:
        if _service is None or refresh:
            _service = RAGService(settings or load_settings())
        return _service
