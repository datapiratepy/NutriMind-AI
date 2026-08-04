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


def _post(client, payload: bytes, filename: str = "innocent.pdf"):
    return client.post("/api/documents",
                       data={"file": (io.BytesIO(payload), filename)},
                       content_type="multipart/form-data")


#: Real file headers, each renamed to .pdf. These are the three the audit used.
DISGUISED = {
    "elf": b"\x7fELF\x02\x01\x01\x00" + b"\x00" * 600,
    "zip": b"PK\x03\x04\x14\x00\x00\x00" + b"\x00" * 600,
    "html": b"<!doctype html><html><script>alert(1)</script>" + b"A" * 600 + b"</html>",
    "png": b"\x89PNG\r\n\x1a\n" + b"\x00" * 600,
    "empty": b"",
}


@pytest.mark.parametrize("kind", sorted(DISGUISED))
def test_a_renamed_non_pdf_is_rejected(client, kind):
    """The name says PDF; the bytes decide."""
    response = _post(client, DISGUISED[kind])
    assert response.status_code == 400, (
        f"{kind} disguised as .pdf was accepted with {response.status_code}")
    assert "not a PDF" in response.get_json()["error"]["message"]


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
