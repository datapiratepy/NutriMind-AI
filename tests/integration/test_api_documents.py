"""Integration tests: the real knowledge-base API (upload → search → delete).

A minimal multi-page PDF is generated in-memory (raw PDF syntax) so the suite
never depends on external files; the lexical embedding provider makes retrieval
deterministic (identical text -> similarity 1.0).

**Ingestion is asynchronous.** Upload returns 202 with the document ``pending``;
indexing happens on a background thread. These tests therefore wait for the job
runner to go idle instead of assuming the work is finished when the response
arrives. They wait on the runner rather than sleeping or polling the status
column, because a sleep long enough to be reliable on a loaded CI machine is
also long enough to make the suite unpleasant, and a poll loop hides how long
the work really took.
"""

from __future__ import annotations

import io


def _drain(client):
    """Block until queued indexing has finished. Fails loudly if it does not."""
    from nutrimind.services.jobs import get_jobs

    assert get_jobs(client.application).wait_idle(30), "ingest job never finished"


def _build_pdf(page_texts: list[str]) -> bytes:
    """Hand-assembled valid PDF with one Helvetica text line per page."""
    objects: list[bytes] = []
    page_count = len(page_texts)
    font_obj = 3 + 2 * page_count  # catalog(1), pages(2), N x (page, content), font

    kids = " ".join(f"{3 + 2 * i} 0 R" for i in range(page_count))
    objects.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    objects.append(f"<< /Type /Pages /Kids [{kids}] /Count {page_count} >>".encode())
    for index, text in enumerate(page_texts):
        content = f"BT /F1 12 Tf 50 700 Td ({text}) Tj ET".encode()
        page_obj = 3 + 2 * index
        objects.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            f"/Contents {page_obj + 1} 0 R "
            f"/Resources << /Font << /F1 {font_obj} 0 R >> >> >>".encode())
        objects.append(b"<< /Length " + str(len(content)).encode() + b" >>\nstream\n"
                       + content + b"\nendstream")
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")

    out = io.BytesIO()
    out.write(b"%PDF-1.4\n")
    offsets = [0]
    for number, body in enumerate(objects, start=1):
        offsets.append(out.tell())
        out.write(f"{number} 0 obj\n".encode() + body + b"\nendobj\n")
    xref_at = out.tell()
    out.write(f"xref\n0 {len(objects) + 1}\n".encode())
    out.write(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        out.write(f"{offset:010d} 00000 n \n".encode())
    out.write(f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
              f"startxref\n{xref_at}\n%%EOF".encode())
    return out.getvalue()


PAGE_ONE = ("Bananas are rich in potassium and provide quick natural energy. "
            "People with diabetes can usually enjoy small portions in moderation.")
PAGE_TWO = ("Paneer is an excellent vegetarian source of protein and calcium. "
            "One hundred grams of paneer provides about eighteen grams of protein.")


def _upload(client, filename: str = "nutrition-notes.pdf"):
    return client.post("/api/documents", data={
        "file": (io.BytesIO(_build_pdf([PAGE_ONE, PAGE_TWO])), filename),
        "condition_tags": "diabetes, general",
    }, content_type="multipart/form-data")


def test_upload_is_accepted_immediately_and_not_yet_indexed(client):
    """202 means accepted, not done.

    The response must not claim an outcome it cannot know: indexing has not run
    when this returns. Reporting 'indexed' here is what the old synchronous
    contract did, and it is what made the polling path in knowledge.js dead code.
    """
    response = _upload(client)
    assert response.status_code == 202
    document = response.get_json()["document"]
    assert document["status"] == "pending"
    assert document["chunk_count"] == 0
    assert document["condition_tags"] == ["diabetes", "general"]


def test_queued_document_reaches_indexed_in_the_background(client):
    _upload(client)
    _drain(client)

    listed = client.get("/api/documents").get_json()["documents"]
    assert len(listed) == 1
    assert listed[0]["status"] == "indexed"
    assert listed[0]["filename"] == "nutrition-notes.pdf"
    assert listed[0]["pages"] == 2
    assert listed[0]["chunk_count"] >= 2


def test_duplicate_upload_rejected(client):
    """Dedup stays synchronous: it is knowable now, so it is answered now."""
    assert _upload(client).status_code == 202
    _drain(client)
    duplicate = _upload(client, filename="same-content.pdf")
    assert duplicate.status_code == 400
    assert "already indexed" in duplicate.get_json()["error"]["message"]


def test_non_pdf_rejected(client):
    response = client.post("/api/documents", data={
        "file": (io.BytesIO(b"plain text"), "notes.txt"),
    }, content_type="multipart/form-data")
    assert response.status_code == 400


def test_structurally_broken_pdf_is_rejected_at_the_request(client):
    """This used to be accepted with 202 and fail later on the worker.

    Validation now parses the file rather than sniffing for ``%PDF-``, so a
    header with no valid structure behind it is caught while the uploader is
    still waiting — a specific 400 instead of a mysterious 'failed' row.
    """
    response = client.post("/api/documents", data={
        "file": (io.BytesIO(b"%PDF-1.4 garbage without structure"), "broken.pdf"),
    }, content_type="multipart/form-data")
    assert response.status_code == 400
    assert "not a readable PDF" in response.get_json()["error"]["message"]
    assert client.get("/api/documents").get_json()["documents"] == []


def test_a_document_is_never_left_non_terminal(client):
    """The property the UI depends on: polling must always stop."""
    _upload(client)
    _drain(client)
    statuses = {d["status"] for d in client.get("/api/documents").get_json()["documents"]}
    assert statuses.isdisjoint({"pending", "processing"})


def test_search_returns_grounded_chunks_with_citations(client):
    _upload(client)
    _drain(client)
    # Lexical provider: identical text -> similarity 1.0, so query with page text.
    response = client.get("/api/documents/search",
                          query_string={"q": PAGE_TWO, "k": 3})
    result = response.get_json()["result"]
    assert result["grounded"] is True
    assert result["citations"][0] == {"filename": "nutrition-notes.pdf", "page": 2}
    assert result["chunks"][0]["similarity"] >= 0.99

    unrelated = client.get("/api/documents/search",
                           query_string={"q": "rocket propulsion"}).get_json()["result"]
    assert unrelated["grounded"] is False and unrelated["citations"] == []


def test_reindex_is_queued_and_completes(client):
    """Re-indexing costs exactly what indexing costs, so it is queued too."""
    document_id = _upload(client).get_json()["document"]["id"]
    _drain(client)

    response = client.post(f"/api/documents/{document_id}/reindex")
    assert response.status_code == 202
    assert response.get_json()["document"]["status"] == "pending"

    _drain(client)
    listed = client.get("/api/documents").get_json()["documents"]
    assert listed[0]["status"] == "indexed"
    assert listed[0]["chunk_count"] >= 2


def test_reindex_does_not_duplicate_chunks(client):
    """``process_document`` clears old vectors first; without that, every
    re-index would add a second copy of every chunk and inflate retrieval."""
    document_id = _upload(client).get_json()["document"]["id"]
    _drain(client)
    first = client.get("/api/documents").get_json()["documents"][0]["chunk_count"]

    client.post(f"/api/documents/{document_id}/reindex")
    _drain(client)
    second = client.get("/api/documents").get_json()["documents"][0]["chunk_count"]
    assert second == first


def test_delete_document_purges_everything(client):
    document_id = _upload(client).get_json()["document"]["id"]
    _drain(client)
    assert client.delete(f"/api/documents/{document_id}").status_code == 200
    assert client.get("/api/documents").get_json()["documents"] == []
    search = client.get("/api/documents/search",
                        query_string={"q": PAGE_ONE}).get_json()["result"]
    assert search["grounded"] is False


def test_missing_file_field_rejected(client):
    response = client.post("/api/documents", data={},
                           content_type="multipart/form-data")
    assert response.status_code == 400


def test_seeding_from_disk_stays_synchronous(app, user, tmp_path):
    """``RAGService.ingest_path`` is the seeding CLI's entry point and must block.

    A one-shot script has to know whether it worked before it exits, so this path
    deliberately did *not* move to the queue. It is covered here because it is now
    the only caller of ``ingest_pdf``, whose internals changed when ingestion was
    split into register/process — a break here would surface as a seed script
    that silently indexes nothing.
    """
    from nutrimind.services.rag_service import get_rag_service

    source = tmp_path / "seed.pdf"
    source.write_bytes(_build_pdf([PAGE_ONE, PAGE_TWO]))

    with app.app_context():
        document = get_rag_service(app.config["NUTRIMIND_SETTINGS"]).ingest_path(
            source, user["id"])
        assert document.status == "indexed", "seeding must complete before it returns"
        assert document.pages == 2
        assert document.chunk_count >= 2
