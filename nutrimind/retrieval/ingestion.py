"""PDF ingestion pipeline: validate → extract → chunk → embed → index.

State machine on ``Document.status`` (model owns the transitions):
``pending → processing → indexed | failed``. SHA-256 dedup prevents the same
file from being indexed twice.

The pipeline is split in two because the halves have very different costs and
very different failure modes:

``register_document``
    Cheap and synchronous: hash the file, reject a duplicate, create the row as
    ``pending``. Runs inside the HTTP request so the uploader gets an immediate,
    specific answer for the things that are knowable immediately.

``process_document``
    Slow and asynchronous: extract, chunk, embed, index. Measured at 12.0s for a
    400-page PDF on the lexical provider, and far longer on watsonx, where
    embeddings go out in batches of 16 — 125 sequential HTTP round-trips for the
    same document. This runs on a background thread (``services/jobs.py``) so it
    cannot hold a request open or hit a reverse proxy's timeout.

``ingest_pdf`` composes both synchronously and is kept for the seeding CLI,
where blocking is the correct behaviour.
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path

from nutrimind.config import Settings
from nutrimind.exceptions import DocumentProcessingError, ValidationError
from nutrimind.extensions import db
from nutrimind.models import Document
from nutrimind.retrieval.chunker import chunk_pages
from nutrimind.retrieval.vector_store import VectorStore
from nutrimind.utils.time import utcnow

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


def register_document(
    path: Path,
    *,
    original_filename: str,
    stored_name: str,
    user_id: int,
    condition_tags: list[str] | None = None,
) -> Document:
    """Hash, reject duplicates, and create the row as ``pending``.

    The synchronous half of ingestion. Everything here is cheap and everything
    here is knowable while the uploader is still waiting, so it stays in the
    request: a duplicate must be reported as a 400 the user can act on, not
    discovered on a background thread and shown as a mysterious ``failed`` row
    thirty seconds later.

    ``pending`` was already in ``STATUSES`` and was previously dead — every row
    was created as ``processing`` — so queued work needed no new state and no
    migration.

    :raises ValidationError: duplicate content (same SHA-256 already indexed).
    """
    digest = sha256_of(path)
    # Scoped to the uploader: the same public guideline PDF being uploaded by
    # two people is normal, and a global check would tell the second one their
    # file is 'already indexed' while showing them nothing they can open.
    existing = db.session.execute(
        db.select(Document).where(Document.sha256 == digest,
                                  Document.user_id == user_id,
                                  Document.status == "indexed")
    ).scalar_one_or_none()
    if existing:
        raise ValidationError(
            f"This document is already indexed as '{existing.filename}'.",
            hint="Delete it first if you want to replace it.",
        )

    document = Document(filename=original_filename, stored_name=stored_name,
                        sha256=digest, status="pending", user_id=user_id,
                        condition_tags=condition_tags or [])
    db.session.add(document)
    db.session.commit()  # ID needed for chunk metadata; status visible to UI
    return document


def process_document(document: Document, *, settings: Settings,
                     vector_store: VectorStore) -> Document:
    """Extract → chunk → embed → index one registered document.

    The asynchronous half. Serves both first-time indexing and re-indexing:
    they differed only in whether old vectors had to be cleared first, and
    clearing unconditionally is both simpler and safer. ``delete_document`` on a
    document with no vectors is a no-op, while a first index that died part-way
    through *does* leave chunks behind — so starting from a clean slate is what
    makes re-running a partially-completed ingest correct rather than duplicative.

    On failure the row is kept with ``status='failed'`` and the message stored
    for the knowledge page, then the exception is re-raised so a synchronous
    caller can still react to it.

    :raises DocumentProcessingError: extraction/embedding/indexing failures.
    """
    path = resolve_document_path(document, settings)
    if not path.exists():
        message = (f"The stored file for '{document.filename}' is missing.")
        document.mark_failed(message)
        db.session.commit()
        raise DocumentProcessingError(
            message, hint="Delete the entry and upload the PDF again.")

    vector_store.delete_document(document.id)
    document.status = "processing"
    document.chunk_count = 0
    db.session.commit()

    try:
        pages = extract_pages(path)
        chunks = chunk_pages(pages, chunk_size=settings.rag.chunk_size,
                             overlap=settings.rag.chunk_overlap)
        if not chunks:
            raise DocumentProcessingError("The PDF produced no usable text chunks.")
        embeddings = vector_store.provider.embed_documents([c.text for c in chunks])
        vector_store.add_chunks(
            document_id=document.id,
            filename=document.filename,
            uploaded_at=utcnow().isoformat(timespec="seconds"),
            chunks=chunks,
            embeddings=embeddings,
        )
        document.mark_indexed(pages=len(pages), chunk_count=len(chunks))
        db.session.commit()
        logger.info("indexed '%s': %d pages -> %d chunks (provider=%s)",
                    document.filename, len(pages), len(chunks),
                    vector_store.provider.name)
        return document
    except Exception as exc:  # noqa: BLE001 — record failure, then re-raise
        document.mark_failed(str(exc))
        db.session.commit()
        logger.warning("ingestion failed for '%s': %s", document.filename, exc)
        raise


def ingest_pdf(
    path: Path,
    *,
    original_filename: str,
    stored_name: str,
    settings: Settings,
    vector_store: VectorStore,
    user_id: int,
    condition_tags: list[str] | None = None,
) -> Document:
    """Register and index one PDF synchronously; returns the Document row.

    Kept for the seeding CLI (``scripts/seed_knowledge_base.py``), where the
    caller is a one-shot script that should block until the work is done and
    report the outcome as its exit status. Request handling uses the two halves
    separately so the slow one can run off the request thread.
    """
    document = register_document(path, original_filename=original_filename,
                                stored_name=stored_name, user_id=user_id,
                                condition_tags=condition_tags)
    return process_document(document, settings=settings, vector_store=vector_store)


# ``reindex_document`` used to live here. It is gone rather than deprecated:
# re-indexing is now queued like any other indexing run (RAGService.reindex ->
# queue_processing), so the only thing it did was call ``process_document`` with
# the same arguments. Leaving a second name for one behaviour is how two code
# paths drift apart.
