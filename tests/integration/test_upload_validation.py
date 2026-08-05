"""Upload validation by content, and what happens to the bytes when it fails.

Before Milestone 5 an upload was validated by two things the caller supplies:
the filename ending in ``.pdf`` and the ``Content-Type`` header. Measured
against the running API as an ordinary signed-in user:

    ELF binary        -> HTTP 202
    HTML w/ <script>  -> HTTP 202
    zip header        -> HTTP 202
    rows created: 3, all later 'failed'
    files persisted to instance/uploads: 3      <- and never removed

So arbitrary bytes were accepted, written to disk and left there. With no rate
limit on the endpoint, one account could fill the volume — taking the database
and the vector store with it.

These tests assert both halves: the rejection, and the absence of residue.
"""

from __future__ import annotations

import io

import pytest

from tests.integration.test_api_documents import PAGE_ONE, PAGE_TWO, _build_pdf


def _uploads(app) -> list:
    return sorted((app.config["NUTRIMIND_SETTINGS"].instance_dir / "uploads").glob("*"))


def _bulky_pdf(seed: int, *, kilobytes: int = 300) -> bytes:
    """A genuinely valid PDF of roughly ``kilobytes``, distinct per ``seed``.

    Padded *inside* the pages rather than appended after ``%%EOF``: bytes after
    the trailer break the cross-reference table, and the file would then be
    rejected by validation rather than counted against the quota — testing the
    wrong thing.
    """
    filler = f"pad{seed:03d} " + "nutrition guideline reference intake " * 40
    pages = [f"{PAGE_ONE} {filler}" for _ in range(max(1, kilobytes // 2))]
    return _build_pdf(pages)


def _shrink_quota(app, monkeypatch, *, megabytes: int) -> None:
    """Lower the storage quota for one test.

    ``Settings`` is a frozen dataclass, so it is replaced rather than mutated,
    and the replacement is put on the live RAGService — which captured its own
    reference at construction and would otherwise keep the original.
    """
    import dataclasses

    from nutrimind.services.rag_service import get_rag_service

    service = get_rag_service(app.config["NUTRIMIND_SETTINGS"])
    monkeypatch.setattr(
        service, "_settings",
        dataclasses.replace(service._settings, max_user_storage_mb=megabytes))


def _post(client, payload: bytes, filename: str = "innocent.pdf"):
    return client.post("/api/documents",
                       data={"file": (io.BytesIO(payload), filename)},
                       content_type="multipart/form-data")


#: Real file headers, each renamed to .pdf.
#:
#: The last four are the important ones and they are why this dictionary was
#: rewritten. The original set contained no file carrying the bytes ``%PDF-``,
#: so it could not detect that the magic check searched 1024 bytes and was
#: defeated by prepending nine of them. Measured against the running API before
#: the fix:
#:
#:     HTML with "%PDF-" in a comment  -> HTTP 202, 3,048 bytes stored
#:     ELF header + "%PDF-" at byte 8  -> HTTP 202, 6,064 bytes stored
#:     "%PDF-" then 2 MB of junk       -> HTTP 202, 2,103,225 bytes stored
#:
#: Test data that shares the implementation's assumption cannot falsify it.
DISGUISED = {
    "elf": b"\x7fELF\x02\x01\x01\x00" + b"\x00" * 600,
    "zip": b"PK\x03\x04\x14\x00\x00\x00" + b"\x00" * 600,
    "html": b"<!doctype html><html><script>alert(1)</script>" + b"A" * 600 + b"</html>",
    "png": b"\x89PNG\r\n\x1a\n" + b"\x00" * 600,
    "empty": b"",
    # -- carrying the magic bytes, which the previous check accepted ----------
    "html_with_magic": b"<!-- %PDF-1.4 --><html><script>alert(1)</script>" + b"J" * 900,
    "elf_with_magic": b"\x7fELF\x02\x01\x01\x00%PDF-1.4" + b"\x00" * 900,
    "magic_then_junk": b"%PDF-1.4\n" + b"Z" * 4000,
    # Header and trailer present, nothing valid between them.
    "magic_and_eof_only": b"%PDF-1.7\n" + b"Q" * 2000 + b"\n%%EOF\n",
}


@pytest.mark.parametrize("kind", sorted(DISGUISED))
def test_a_renamed_non_pdf_is_rejected(client, kind):
    """The name says PDF; the bytes decide."""
    response = _post(client, DISGUISED[kind])
    assert response.status_code == 400, (
        f"{kind} disguised as .pdf was accepted with {response.status_code}")
    assert "not a readable PDF" in response.get_json()["error"]["message"]


@pytest.mark.parametrize("kind", sorted(DISGUISED))
def test_a_rejected_upload_leaves_nothing_on_disk(client, app, kind):
    """Rejecting without cleaning up is how a disk fills.

    The file is written before it can be inspected — the check needs the bytes —
    so the deletion is the load-bearing half, not the validation.
    """
    _post(client, DISGUISED[kind])
    assert _uploads(app) == [], f"{kind} left a file behind after rejection"
    assert client.get("/api/documents").get_json()["documents"] == []


def test_a_real_pdf_is_still_accepted(client, app):
    """The obvious way to break this is to reject everything."""
    response = _post(client, _build_pdf([PAGE_ONE, PAGE_TWO]), "real.pdf")
    assert response.status_code == 202
    assert len(_uploads(app)) == 1

    from nutrimind.services.jobs import get_jobs
    assert get_jobs(app).wait_idle(30)
    assert client.get("/api/documents").get_json()["documents"][0]["status"] == "indexed"


def test_a_pdf_with_leading_junk_is_accepted(client):
    """The spec tolerates bytes before %PDF-, and real files sometimes have them.

    Requiring the marker at offset zero would reject valid documents, so the
    check searches a short prefix instead.
    """
    body = _build_pdf([PAGE_ONE, PAGE_TWO])
    assert _post(client, b"\n\n   " + body, "padded.pdf").status_code == 202


def test_duplicate_upload_still_cleans_up_its_file(client, app):
    """The pre-existing rejection path must keep behaving too."""
    from nutrimind.services.jobs import get_jobs

    body = _build_pdf([PAGE_ONE, PAGE_TWO])
    assert _post(client, body, "first.pdf").status_code == 202
    assert get_jobs(app).wait_idle(30)
    before = len(_uploads(app))

    assert _post(client, body, "second.pdf").status_code == 400
    assert len(_uploads(app)) == before, "the duplicate's file was left on disk"


# -- the endpoint is bounded ---------------------------------------------------

def test_upload_is_rate_limited(client, app):
    """MAX_UPLOAD_MB bounds one request; nothing bounded the sequence.

    This is the only endpoint that writes caller-controlled bytes to disk, so
    an unbounded one is the cheapest way to take the instance down.
    """
    from nutrimind.services.jobs import get_jobs

    codes = []
    for index in range(12):
        codes.append(_post(client, _build_pdf([f"{PAGE_ONE} number {index}", PAGE_TWO]),
                           f"doc-{index}.pdf").status_code)
    get_jobs(app).wait_idle(60)

    assert 429 in codes, f"no request was ever refused: {codes}"
    assert codes[0] == 202, "the limit fired immediately — nobody could upload anything"


def test_a_rate_limited_upload_creates_no_document(client, app):
    for index in range(12):
        _post(client, _build_pdf([f"{PAGE_ONE} n{index}", PAGE_TWO]), f"d{index}.pdf")
    from nutrimind.services.jobs import get_jobs
    get_jobs(app).wait_idle(60)

    listed = client.get("/api/documents").get_json()["documents"]
    assert len(listed) <= 10, "a refused upload still created a row"


# -- storage is bounded per account -------------------------------------------

def test_upload_is_refused_once_the_account_quota_is_reached(client, app, monkeypatch):
    """MAX_UPLOAD_MB bounds one request; nothing bounded accumulation.

    Measured before this existed: 15 MB per request at 10 requests per 10
    minutes is roughly 21 GB per day, per account, with no ceiling. A full
    volume takes the database and the vector store down together.
    """
    _shrink_quota(app, monkeypatch, megabytes=1)
    from nutrimind.services.jobs import get_jobs

    codes = []
    for index in range(5):
        codes.append(_post(client, _bulky_pdf(index), f"big-{index}.pdf").status_code)
        get_jobs(app).wait_idle(60)

    assert 202 in codes, f"the quota fired immediately: {codes}"
    assert 400 in codes, f"storage grew without a ceiling: {codes}"


def test_quota_message_names_the_limit_and_the_way_out(client, app, monkeypatch):
    _shrink_quota(app, monkeypatch, megabytes=1)
    from nutrimind.services.jobs import get_jobs

    for index in range(5):
        response = _post(client, _bulky_pdf(index), f"doc-{index}.pdf")
        get_jobs(app).wait_idle(60)
        if response.status_code == 400:
            error = response.get_json()["error"]
            assert "storage limit" in error["message"]
            assert "Delete a document" in error["hint"]
            return
    raise AssertionError("the quota never fired")


# -- a permanent processing failure must not keep its bytes -------------------

def test_a_pdf_that_parses_but_yields_no_text_is_discarded(client, app):
    """The gap the magic-byte check left open.

    A file can be a structurally valid PDF, pass validation, and still fail
    ingestion — a scanned document has no extractable text. That failure is
    permanent, so the bytes are worth nothing and must not be retained.
    """
    from nutrimind.services.jobs import get_jobs

    # Valid PDF structure, one page, effectively no text.
    blank = _build_pdf([" "])
    response = _post(client, blank, "scanned.pdf")
    assert response.status_code in (202, 400)
    get_jobs(app).wait_idle(30)

    listed = client.get("/api/documents").get_json()["documents"]
    if listed:
        assert listed[0]["status"] == "failed"
    assert _uploads(app) == [], "a permanently failed document kept its file"


def test_a_transient_failure_keeps_the_file_for_reindex(client, app, monkeypatch):
    """The other half, and the reason this is not just 'delete on any failure'.

    An unreachable embedding provider is temporary. The stored file is the only
    copy of the user's document and Re-index is the documented recovery, so
    deleting it here would turn an outage into permanent data loss.
    """
    import nutrimind.retrieval.ingestion as ingestion
    from nutrimind.services.jobs import get_jobs

    real = ingestion.chunk_pages

    def boom(*args, **kwargs):
        raise RuntimeError("embedding backend unreachable")

    monkeypatch.setattr(ingestion, "chunk_pages", boom)
    assert _post(client, _build_pdf([PAGE_ONE, PAGE_TWO]), "keep.pdf").status_code == 202
    get_jobs(app).wait_idle(30)
    monkeypatch.setattr(ingestion, "chunk_pages", real)

    listed = client.get("/api/documents").get_json()["documents"]
    assert listed[0]["status"] == "failed"
    assert len(_uploads(app)) == 1, "a transient failure destroyed the user's file"

    document_id = listed[0]["id"]
    assert client.post(f"/api/documents/{document_id}/reindex").status_code == 202
    get_jobs(app).wait_idle(30)
    assert client.get("/api/documents").get_json()["documents"][0]["status"] == "indexed"
