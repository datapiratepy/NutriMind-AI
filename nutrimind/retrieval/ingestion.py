"""PDF ingestion pipeline: validate → extract → chunk → embed → index.

State machine on ``Document.status`` (model owns the transitions):
``pending → processing → indexed | failed``. SHA-256 dedup prevents the same
file from being indexed twice. Ingestion is synchronous by design — typical
guideline PDFs index in seconds and the UI polls status regardless (see
docs/IMPLEMENTATION_NOTES.md).
"""

from __future__ import annotations

import datetime as dt
import hashlib
import logging
from pathlib import Path

from nutrimind.config import Settings
from nutrimind.exceptions import DocumentProcessingError, ValidationError
from nutrimind.extensions import db
from nutrimind.models import Document
from nutrimind.retrieval.chunker import chunk_pages
from nutrimind.retrieval.vector_store import VectorStore

logger = logging.getLogger(__name__)

_MIN_EXTRACTABLE_CHARS = 200  # below this the PDF is likely scanned images


def extract_pages(pdf_path: Path) -> list[str]:
    """Text per page via pypdf.

    :raises DocumentProcessingError: unreadable/encrypted/scanned PDFs.
    """
    try:
        from pypdf import PdfReader
    except ImportError:
        raise DocumentProcessingError(
            "pypdf is not installed.", hint="pip install -r requirements.txt"
        ) from None

    try:
        reader = PdfReader(str(pdf_path))
        if reader.is_encrypted:
            raise DocumentProcessingError(
                "The PDF is password-protected.",
                hint="Remove the password and upload again.",
            )
        pages = [(page.extract_text() or "") for page in reader.pages]
    except DocumentProcessingError:
        raise
    except Exception as exc:  # noqa: BLE001 — pypdf raises many types
        raise DocumentProcessingError(f"Could not read the PDF: {exc}") from exc

    if sum(len(p.strip()) for p in pages) < _MIN_EXTRACTABLE_CHARS:
        raise DocumentProcessingError(
            "No extractable text found — this looks like a scanned PDF.",
            hint="Scanned/OCR documents are out of scope (ARCHITECTURE.md §11); "
                 "please upload a text-based PDF.",
        )
    return pages


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(65536), b""):
            digest.update(block)
    return digest.hexdigest()


def resolve_document_path(document: Document, settings: Settings) -> Path:
    """Absolute path of a document's file.

    ``stored_name`` is repo-relative; the ``instance/`` prefix maps onto the
    (possibly overridden) instance directory so tests stay isolated.
    """
    if document.stored_name.startswith("instance/"):
        return settings.instance_dir / document.stored_name[len("instance/"):]
    return settings.base_dir / document.stored_name


def ingest_pdf(
    path: Path,
    *,
    original_filename: str,
    stored_name: str,
    settings: Settings,
    vector_store: VectorStore,
    condition_tags: list[str] | None = None,
) -> Document:
    """Run the full pipeline for one PDF; returns the Document row.

    On processing failure the Document is kept with ``status='failed'`` and
    the error stored for the knowledge page, then the exception re-raised so
    the API can return a meaningful response.

    :raises ValidationError: duplicate content (same SHA-256 already indexed).
    :raises DocumentProcessingError: extraction/embedding/indexing failures.
    """
    digest = sha256_of(path)
    existing = db.session.execute(
        db.select(Document).where(Document.sha256 == digest,
                                  Document.status == "indexed")
    ).scalar_one_or_none()
    if existing:
        raise ValidationError(
            f"This document is already indexed as '{existing.filename}'.",
            hint="Delete it first if you want to replace it.",
        )

    document = Document(filename=original_filename, stored_name=stored_name,
                        sha256=digest, status="processing",
                        condition_tags=condition_tags or [])
    db.session.add(document)
    db.session.commit()  # ID needed for chunk metadata; status visible to UI

    try:
        pages = extract_pages(path)
        chunks = chunk_pages(pages, chunk_size=settings.rag.chunk_size,
                             overlap=settings.rag.chunk_overlap)
        if not chunks:
            raise DocumentProcessingError("The PDF produced no usable text chunks.")
        embeddings = vector_store.provider.embed_documents([c.text for c in chunks])
        vector_store.add_chunks(
            document_id=document.id,
            filename=original_filename,
            uploaded_at=dt.datetime.utcnow().isoformat(timespec="seconds"),
            chunks=chunks,
            embeddings=embeddings,
        )
        document.mark_indexed(pages=len(pages), chunk_count=len(chunks))
        db.session.commit()
        logger.info("indexed '%s': %d pages -> %d chunks (provider=%s)",
                    original_filename, len(pages), len(chunks),
                    vector_store.provider.name)
        return document
    except Exception as exc:  # noqa: BLE001 — record failure, then re-raise
        document.mark_failed(str(exc))
        db.session.commit()
        logger.warning("ingestion failed for '%s': %s", original_filename, exc)
        raise


def reindex_document(document: Document, *, settings: Settings,
                     vector_store: VectorStore) -> Document:
    """Re-run extract→chunk→embed→index for an existing document."""
    path = resolve_document_path(document, settings)
    if not path.exists():
        raise DocumentProcessingError(
            f"The stored file for '{document.filename}' is missing.",
            hint="Delete the entry and upload the PDF again.",
        )
    vector_store.delete_document(document.id)
    document.status = "processing"
    document.chunk_count = 0
    db.session.commit()
    try:
        pages = extract_pages(path)
        chunks = chunk_pages(pages, chunk_size=settings.rag.chunk_size,
                             overlap=settings.rag.chunk_overlap)
        embeddings = vector_store.provider.embed_documents([c.text for c in chunks])
        vector_store.add_chunks(
            document_id=document.id,
            filename=document.filename,
            uploaded_at=dt.datetime.utcnow().isoformat(timespec="seconds"),
            chunks=chunks,
            embeddings=embeddings,
        )
        document.mark_indexed(pages=len(pages), chunk_count=len(chunks))
        db.session.commit()
        return document
    except Exception as exc:  # noqa: BLE001
        document.mark_failed(str(exc))
        db.session.commit()
        raise
