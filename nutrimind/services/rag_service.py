"""RAGService: the service-layer facade over the retrieval package.

Routes and agents talk to this class only — they never touch
Chroma, pypdf or embedding providers directly. One instance per process,
bound to the resolved embedding provider and its collection.
"""

from __future__ import annotations

import logging
import shutil
import threading
import uuid
from collections.abc import Sequence
from pathlib import Path

from flask import current_app
from werkzeug.datastructures import FileStorage
from werkzeug.utils import secure_filename

from nutrimind.config import Settings, load_settings
from nutrimind.exceptions import ValidationError
from nutrimind.extensions import db
from nutrimind.models import Document
from nutrimind.retrieval.embeddings import resolve_embedding_provider
from nutrimind.retrieval.ingestion import (
    ingest_pdf,
    process_document,
    register_document,
    resolve_document_path,
)
from nutrimind.retrieval.retriever import RetrievalResult, Retriever
from nutrimind.retrieval.vector_store import VectorStore

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_service: "RAGService | None" = None


#: A PDF must begin with "%PDF-". The specification tolerates a little junk
#: before it and some real files have it, so a short prefix is searched rather
#: than requiring offset zero — but only a short one. 1024 bytes was too
#: generous: it let any file carrying "%PDF-" anywhere in its first kilobyte
#: through, which is a nine-byte bypass.
_PDF_MAGIC = b"%PDF-"
_MAGIC_SEARCH_BYTES = 32

#: The trailer marker every conforming PDF ends with. Searched in the tail
#: because writers append varying amounts of whitespace after it.
_PDF_EOF = b"%%EOF"
_EOF_SEARCH_BYTES = 4096


def _require_pdf_bytes(path: Path) -> None:
    """Reject a file whose *contents* are not a PDF, whatever it is named.

    Three checks, cheapest first, because this runs inside the request:

    1. ``%PDF-`` near the start;
    2. ``%%EOF`` near the end;
    3. the file parses as a PDF with at least one page.

    Check 3 is what makes the difference. The previous version did only a
    loosened form of check 1 — ``%PDF-`` anywhere in the first 1024 bytes — and
    was defeated by prepending nine bytes. Measured against the running API:

        HTML with "%PDF-" in a comment  -> HTTP 202, 3,048 bytes stored
        ELF header + "%PDF-" at byte 8  -> HTTP 202, 6,064 bytes stored
        "%PDF-" then 2 MB of junk       -> HTTP 202, 2,103,225 bytes stored

    all of which then failed to parse and left their bytes on disk forever.

    Structural parsing is done with pypdf's own reader and is deliberately
    *shallow*: the header, the trailer and the page count. Full text extraction
    stays on the background thread, because it is the expensive part and this
    has to be fast enough to run while the uploader waits.

    This is not a guarantee that the file is safe — a genuine PDF can still be
    malicious, and pypdf remains what parses it properly. It is a guarantee that
    the bytes are a PDF, which is what keeps arbitrary content off the disk.

    :raises ValidationError: the file is not a usable PDF.
    """
    generic = ValidationError(
        "That file is not a readable PDF.",
        hint="The name ends in .pdf but the contents are not a PDF document. "
             "Export or re-save it as a real PDF and try again.")
    try:
        size = path.stat().st_size
        with path.open("rb") as handle:
            prefix = handle.read(_MAGIC_SEARCH_BYTES)
            handle.seek(max(0, size - _EOF_SEARCH_BYTES))
            tail = handle.read()
    except OSError as exc:  # pragma: no cover — unreadable temp file
        raise ValidationError("The uploaded file could not be read.") from exc

    if _PDF_MAGIC not in prefix or _PDF_EOF not in tail:
        raise generic

    try:
        from pypdf import PdfReader

        reader = PdfReader(str(path))
        if reader.is_encrypted:
            raise ValidationError(
                "That PDF is password-protected.",
                hint="Remove the password and upload it again.")
        if len(reader.pages) < 1:
            raise generic
    except ValidationError:
        raise
    except Exception as exc:  # noqa: BLE001 — pypdf raises many types
        raise generic from exc


def user_storage_bytes(user_id: int, settings: Settings) -> int:
    """Total bytes this account currently occupies under ``instance/uploads``.

    Computed from the files a user's rows point at rather than stored on the
    account. A stored counter is a second source of truth that drifts the first
    time a delete fails part-way, and the number of documents per account is
    small enough that stat-ing them is cheaper than keeping a counter honest.
    """
    total = 0
    rows = db.session.execute(
        db.select(Document).where(Document.user_id == user_id)).scalars()
    for document in rows:
        if not document.stored_name.startswith("instance/uploads/"):
            continue  # seeded files are shared repo content, not user storage
        path = resolve_document_path(document, settings)
        try:
            total += path.stat().st_size
        except OSError:
            continue  # already gone — not this account's problem
    return total


class RAGService:
    """Upload, index, re-index, delete and search knowledge documents."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self.provider = resolve_embedding_provider(settings)
        self.store = VectorStore(settings.chroma_dir, self.provider)
        # An explicit RAG_SIMILARITY_THRESHOLD wins; otherwise the provider
        # supplies the value calibrated for its own vector space.
        self.similarity_threshold = (
            settings.rag.similarity_threshold
            if settings.rag.similarity_threshold is not None
            else self.provider.default_similarity_threshold)
        self.retriever = Retriever(
            self.store,
            top_k=settings.rag.top_k,
            similarity_threshold=self.similarity_threshold,
        )

    # -- ingestion ------------------------------------------------------------

    def queue_upload(self, file: FileStorage, user_id: int,
                     condition_tags: list[str] | None = None) -> Document:
        """Validate and store an uploaded PDF, then queue it for indexing.

        Returns as soon as the row exists, with ``status='pending'``. Everything
        that can be judged immediately — file type, content type, duplicate
        content — is judged here so the uploader gets a real error instead of a
        row that fails silently later.
        """
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
        # Everything above trusts the client: the filename is theirs and so is
        # the Content-Type header. From here the file's own bytes are the
        # authority. Any rejection past this point must remove what was written,
        # or a caller can fill the disk with rejected uploads.
        try:
            _require_pdf_bytes(target)
            self._require_storage_headroom(user_id, target.stat().st_size)
            document = register_document(
                target, original_filename=original, stored_name=stored,
                user_id=user_id, condition_tags=condition_tags)
        except ValidationError:
            target.unlink(missing_ok=True)
            raise
        self.queue_processing(document)
        return document

    def _require_storage_headroom(self, user_id: int, incoming: int) -> None:
        """Refuse an upload that would take the account past its quota.

        ``MAX_UPLOAD_MB`` bounds one request and the rate limit bounds their
        frequency, but neither bounds the total. Without a quota an account can
        accumulate indefinitely — measured at roughly 21 GB per day within the
        existing limits — and a full volume takes the database and the vector
        store down with it.

        Per account rather than global, so one user cannot deny service to
        everyone else by filling the disk first.
        """
        limit = self._settings.max_user_storage_mb * 1024 * 1024
        used = user_storage_bytes(user_id, self._settings)
        if used + incoming > limit:
            raise ValidationError(
                f"That upload would exceed your {self._settings.max_user_storage_mb} MB "
                f"storage limit ({used / 1024 / 1024:.1f} MB currently used).",
                hint="Delete a document you no longer need and try again.")

    def queue_processing(self, document: Document) -> Document:
        """Hand one registered document to the background pool.

        A refused submission is recorded on the row rather than raised. The file
        is already stored and the row already exists, so the honest outcome is a
        document the user can retry with the Re-index button — not a 500 that
        leaves an invisible ``pending`` row behind.
        """
        from nutrimind.services.jobs import JobQueueFull, get_jobs

        document.status = "pending"
        db.session.commit()
        document_id = document.id
        try:
            get_jobs(current_app._get_current_object()).submit(
                _process_document_job, document_id)
        except JobQueueFull as exc:
            logger.warning("ingest queue full, refusing document %d: %s",
                           document_id, exc)
            document.mark_failed(
                "The server is busy indexing other documents. "
                "Use Re-index to try again shortly.")
            db.session.commit()
        return document

    def ingest_path(self, path: Path, user_id: int,
                    condition_tags: list[str] | None = None) -> Document:
        """Index a PDF from disk (seed script), giving the owner its own copy.

        The file is copied into ``instance/uploads`` under a fresh name rather
        than referenced where it lies. Two reasons, both about ownership:

        * ``stored_name`` is globally unique, so recording the same source path
          for two accounts would fail on an opaque IntegrityError — and two
          people seeding the same public guideline PDF is ordinary use.
        * Documents are per-account, so deleting one account's copy must not
          remove a file another account's row still points at.
        """
        name = f"{uuid.uuid4().hex}.pdf"
        target = self._settings.instance_dir / "uploads" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)
        try:
            return ingest_pdf(target, original_filename=path.name,
                              stored_name=f"instance/uploads/{name}",
                              settings=self._settings, vector_store=self.store,
                              user_id=user_id, condition_tags=condition_tags)
        except ValidationError:
            target.unlink(missing_ok=True)  # duplicate — keep disk clean
            raise

    def reindex(self, document: Document) -> Document:
        """Queue a re-index. Same cost as a first index, so same treatment."""
        return self.queue_processing(document)

    def document_ids_for(self, user_id: int) -> list[int]:
        """Ids of the documents a user owns, for the retrieval ownership filter."""
        return list(db.session.execute(
            db.select(Document.id).where(Document.user_id == user_id,
                                         Document.status == "indexed")
        ).scalars())

    def has_vectors(self, document: Document) -> bool:
        """Whether this document is searchable in the *live* collection.

        Derived on read rather than stored. Each embedding provider owns its own
        collection (``kb_<provider>``), so adding IBM credentials or installing
        sentence-transformers moves the app to a different one and leaves older
        documents still saying "indexed" while contributing nothing to search —
        indistinguishable from broken retrieval unless it is surfaced.

        Computed rather than persisted for three reasons: it needs no schema
        change, it cannot itself go stale, and it keeps a GET request from
        writing to the database.
        """
        return self.store.count_for_document(document.id) > 0


    def remove(self, document: Document) -> None:
        """Delete vectors, the DB row, and (for uploads) the stored file."""
        self.store.delete_document(document.id)
        path = resolve_document_path(document, self._settings)
        if document.stored_name.startswith("instance/uploads/"):
            path.unlink(missing_ok=True)  # seed files in knowledge_base/ stay
        db.session.delete(document)
        db.session.commit()

    # -- search ---------------------------------------------------------------

    def retrieve(self, query: str, *, top_k: int | None = None,
                 document_ids: Sequence[int] | None = None) -> RetrievalResult:
        """Threshold-filtered retrieval with citations.

        :param document_ids: the caller's own documents. Required in request
            handling; ``None`` (unrestricted) exists only for maintenance and
            tests. See :meth:`VectorStore.query`.
        """
        return self.retriever.retrieve(query, top_k=top_k,
                                       document_ids=document_ids)

    # -- diagnostics ----------------------------------------------------------

    def status(self) -> dict:
        return {
            "provider": self.provider.name,
            "semantic": self.provider.semantic,
            "capability": self.provider.capability,
            "similarity_threshold": self.similarity_threshold,
            "collection": self.store.collection_name,
            "chunks": self.store.count(),
        }


def _process_document_job(document_id: int) -> None:
    """Background entry point: index one document by id.

    Takes an **id**, not a Document. The row was loaded in the request thread's
    session, which is gone by the time this runs; passing the instance across
    would either detach it or share a session between threads. Re-loading is one
    primary-key lookup and removes the question entirely.

    Exceptions are logged and not re-raised: ``process_document`` has already
    recorded the failure on the row, which is what the user sees, and a job that
    raises into the pool would only set an exception on a Future nobody reads.
    """
    document = db.session.get(Document, document_id)
    if document is None:  # deleted while queued — nothing to do, and not an error
        logger.info("skipping ingest job for document %d: it no longer exists",
                    document_id)
        return
    settings = current_app.config["NUTRIMIND_SETTINGS"]
    service = get_rag_service(settings)
    try:
        process_document(document, settings=settings, vector_store=service.store)
    except Exception as exc:  # noqa: BLE001 — already recorded on the row
        logger.warning("background ingest of document %d failed: %s",
                       document_id, exc)


def get_rag_service(settings: Settings | None = None, *,
                    refresh: bool = False) -> RAGService:
    """Process-wide singleton (mirrors the LLM factory pattern)."""
    global _service
    with _lock:
        if _service is None or refresh:
            _service = RAGService(settings or load_settings())
        return _service
