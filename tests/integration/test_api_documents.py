"""Integration tests: the real knowledge-base API (upload → search → delete).

A minimal multi-page PDF is generated in-memory (raw PDF syntax) so the suite
never depends on external files; the hash embedding provider makes retrieval
deterministic (identical text -> similarity 1.0).
"""

from __future__ import annotations

import io


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


def test_upload_indexes_document(client):
    response = _upload(client)
    assert response.status_code == 201
    document = response.get_json()["document"]
    assert document["status"] == "indexed"
    assert document["pages"] == 2
    assert document["chunk_count"] >= 2
    assert document["condition_tags"] == ["diabetes", "general"]

    listed = client.get("/api/documents").get_json()["documents"]
    assert len(listed) == 1 and listed[0]["filename"] == "nutrition-notes.pdf"


def test_duplicate_upload_rejected(client):
    assert _upload(client).status_code == 201
    duplicate = _upload(client, filename="same-content.pdf")
    assert duplicate.status_code == 400
    assert "already indexed" in duplicate.get_json()["error"]["message"]


def test_non_pdf_rejected(client):
    response = client.post("/api/documents", data={
        "file": (io.BytesIO(b"plain text"), "notes.txt"),
    }, content_type="multipart/form-data")
    assert response.status_code == 400


def test_corrupt_pdf_marked_failed(client):
    response = client.post("/api/documents", data={
        "file": (io.BytesIO(b"%PDF-1.4 garbage without structure"), "broken.pdf"),
    }, content_type="multipart/form-data")
    assert response.status_code == 422

    listed = client.get("/api/documents").get_json()["documents"]
    assert listed[0]["status"] == "failed"
    assert listed[0]["error"]


def test_search_returns_grounded_chunks_with_citations(client):
    _upload(client)
    # Hash provider: identical text -> similarity 1.0, so query with page text.
    response = client.get("/api/documents/search",
                          query_string={"q": PAGE_TWO, "k": 3})
    result = response.get_json()["result"]
    assert result["grounded"] is True
    assert result["citations"][0] == {"filename": "nutrition-notes.pdf", "page": 2}
    assert result["chunks"][0]["similarity"] >= 0.99

    unrelated = client.get("/api/documents/search",
                           query_string={"q": "rocket propulsion"}).get_json()["result"]
    assert unrelated["grounded"] is False and unrelated["citations"] == []


def test_reindex_document(client):
    document_id = _upload(client).get_json()["document"]["id"]
    response = client.post(f"/api/documents/{document_id}/reindex")
    assert response.status_code == 200
    assert response.get_json()["document"]["status"] == "indexed"


def test_delete_document_purges_everything(client):
    document_id = _upload(client).get_json()["document"]["id"]
    assert client.delete(f"/api/documents/{document_id}").status_code == 200
    assert client.get("/api/documents").get_json()["documents"] == []
    search = client.get("/api/documents/search",
                        query_string={"q": PAGE_ONE}).get_json()["result"]
    assert search["grounded"] is False


def test_missing_file_field_rejected(client):
    response = client.post("/api/documents", data={},
                           content_type="multipart/form-data")
    assert response.status_code == 400
