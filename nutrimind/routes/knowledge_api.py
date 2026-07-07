"""Knowledge-base API: upload, list, search, re-index, delete.

Endpoints
    GET    /api/documents                  -> registry with status + chunk counts
    POST   /api/documents                  -> multipart upload (field "file",
                                              optional "condition_tags" CSV);
                                              synchronous indexing; 201 on success
    GET    /api/documents/search?q=&k=     -> retrieval preview: chunks,
                                              similarities, citations, grounded flag
    POST   /api/documents/<id>/reindex     -> re-run the pipeline for one document
    DELETE /api/documents/<id>             -> remove vectors + row (+ uploaded file)

Failures during processing keep the Document row with status='failed' and the
error message, so the knowledge page can display what went wrong.
"""

from __future__ import annotations

from flask import Blueprint, current_app, request

from nutrimind.exceptions import ValidationError
from nutrimind.extensions import db
from nutrimind.models import Document
from nutrimind.routes import ok
from nutrimind.services.rag_service import get_rag_service
from nutrimind.utils.validators import sanitize_text, validate_range

knowledge_api = Blueprint("knowledge_api", __name__, url_prefix="/api")


def _service():
    return get_rag_service(current_app.config["NUTRIMIND_SETTINGS"])


def _get_document_or_400(document_id: int) -> Document:
    document = db.session.get(Document, document_id)
    if document is None:
        raise ValidationError(f"Document {document_id} does not exist.")
    return document


@knowledge_api.get("/documents")
def list_documents():
    rows = db.session.execute(
        db.select(Document).order_by(Document.uploaded_at.desc())
    ).scalars()
    return ok({"documents": [d.to_dict() for d in rows]})


@knowledge_api.post("/documents")
def upload_document():
    file = request.files.get("file")
    if file is None:
        raise ValidationError("No file provided.",
                              hint="Send multipart/form-data with a 'file' field.")
    tags_raw = request.form.get("condition_tags", "")
    tags = [sanitize_text(t, max_chars=40, field="condition_tag")
            for t in tags_raw.split(",") if t.strip()]
    document = _service().ingest_upload(file, condition_tags=tags)
    return ok({"document": document.to_dict()}, 201)


@knowledge_api.get("/documents/search")
def search_documents():
    query = sanitize_text(request.args.get("q"), max_chars=300, field="q")
    top_k = int(validate_range(request.args.get("k", 5), "k", 1, 10))
    result = _service().retrieve(query, top_k=top_k)
    return ok({"result": result.to_dict()})


@knowledge_api.post("/documents/<int:document_id>/reindex")
def reindex(document_id: int):
    document = _get_document_or_400(document_id)
    document = _service().reindex(document)
    return ok({"document": document.to_dict()})


@knowledge_api.delete("/documents/<int:document_id>")
def delete_document(document_id: int):
    document = _get_document_or_400(document_id)
    _service().remove(document)
    return ok({"deleted": document_id})
