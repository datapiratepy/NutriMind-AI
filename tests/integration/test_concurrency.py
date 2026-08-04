"""Behaviour under concurrent use — the properties Milestone 4 exists to provide.

Three claims are tested here, each of which was false before this milestone:

* an upload **returns while indexing is still running**, rather than holding the
  request open for the duration;
* **concurrent uploads from different accounts** both complete, stay isolated,
  and both end up searchable;
* a document is **never left in a non-terminal state**, whatever happens to the
  job.

The blocking tests use an event rather than a sleep. A sleep long enough to be
reliable on a loaded CI machine is long enough to be irritating, and a sleep
short enough to be pleasant is a flake waiting for a slow day.
"""

from __future__ import annotations

import io
import threading

import pytest

from tests.conftest import sign_in
from tests.integration.test_api_documents import PAGE_ONE, PAGE_TWO, _build_pdf


def _post(client, filename: str, pages: list[str] | None = None):
    return client.post("/api/documents", data={
        "file": (io.BytesIO(_build_pdf(pages or [PAGE_ONE, PAGE_TWO])), filename),
    }, content_type="multipart/form-data")


def _jobs(app):
    from nutrimind.services.jobs import get_jobs

    return get_jobs(app)


@pytest.fixture()
def blocked_ingest(monkeypatch):
    """Hold every ingest job open until the test releases it."""
    release = threading.Event()
    started = threading.Event()
    real = None

    import nutrimind.retrieval.ingestion as ingestion
    import nutrimind.services.rag_service as rag_service

    real = ingestion.process_document

    def slow(document, **kwargs):
        started.set()
        release.wait(30)
        return real(document, **kwargs)

    monkeypatch.setattr(rag_service, "process_document", slow)
    yield started, release
    release.set()


# -- the request no longer waits for the work ---------------------------------

def test_upload_returns_while_indexing_is_still_running(client, app, blocked_ingest):
    """The core of the milestone, made deterministic.

    With the job pinned open, the upload must still answer. Before this change
    the response could not exist until indexing had finished — measured at 12.0s
    for a 400-page PDF on the lexical provider, and 125 sequential watsonx
    round-trips on the credentialed one.
    """
    started, release = blocked_ingest

    response = _post(client, "slow.pdf")
    assert response.status_code == 202
    assert response.get_json()["document"]["status"] == "pending"

    assert started.wait(10), "the ingest job never started"
    # Still in flight, and the request has already been answered.
    assert _jobs(app).pending == 1
    listed = client.get("/api/documents").get_json()["documents"]
    assert listed[0]["status"] in ("pending", "processing")

    release.set()
    assert _jobs(app).wait_idle(30)
    assert client.get("/api/documents").get_json()["documents"][0]["status"] == "indexed"


def test_reads_still_work_while_a_document_is_indexing(client, app, blocked_ingest):
    """A held ingest must not make the rest of the application unavailable."""
    started, release = blocked_ingest
    _post(client, "slow.pdf")
    assert started.wait(10)

    assert client.get("/api/documents").status_code == 200
    assert client.get("/api/dashboard/summary").status_code == 200
    assert client.get("/api/documents/search",
                      query_string={"q": "paneer"}).status_code == 200

    release.set()
    assert _jobs(app).wait_idle(30)


# -- concurrent uploads --------------------------------------------------------

def test_two_accounts_can_upload_at_the_same_time(app, user, other_user):
    """Concurrent ingestion must not lose work or leak it across accounts.

    Both documents are submitted before either is allowed to finish, so the two
    jobs genuinely overlap in the pool rather than running one after the other.
    """
    client_a, client_b = app.test_client(), app.test_client()
    sign_in(client_a, user)
    sign_in(client_b, other_user)

    # Two pages each: one page of this text falls below the extractor's
    # 200-character floor and would fail as a scanned PDF.
    assert _post(client_a, "a.pdf", [PAGE_ONE, PAGE_ONE]).status_code == 202
    assert _post(client_b, "b.pdf", [PAGE_TWO, PAGE_TWO]).status_code == 202
    assert _jobs(app).wait_idle(60)

    docs_a = client_a.get("/api/documents").get_json()["documents"]
    docs_b = client_b.get("/api/documents").get_json()["documents"]
    assert [d["filename"] for d in docs_a] == ["a.pdf"]
    assert [d["filename"] for d in docs_b] == ["b.pdf"]
    assert docs_a[0]["status"] == "indexed" and docs_b[0]["status"] == "indexed"

    # Isolation survives concurrency: the vector store has no user_id column, so
    # this is the assertion that would catch two overlapping jobs cross-writing.
    hit_a = client_a.get("/api/documents/search",
                         query_string={"q": PAGE_ONE}).get_json()["result"]
    assert hit_a["grounded"] is True
    assert {c["filename"] for c in hit_a["citations"]} == {"a.pdf"}

    leaked = client_a.get("/api/documents/search",
                          query_string={"q": PAGE_TWO}).get_json()["result"]
    assert all(c["filename"] == "a.pdf" for c in leaked["citations"])


def test_many_queued_uploads_all_reach_a_terminal_state(client, app):
    """Nothing may be dropped or left mid-flight when work is queued behind work."""
    for index in range(6):
        # Distinct content per document, or the SHA-256 dedup rejects them all
        # as duplicates of the first — and two pages, to clear the extractor's
        # 200-character floor.
        assert _post(client, f"doc-{index}.pdf",
                     [f"{PAGE_ONE} variant {index}", PAGE_TWO]).status_code == 202
    assert _jobs(app).wait_idle(90)

    listed = client.get("/api/documents").get_json()["documents"]
    assert len(listed) == 6
    assert {d["status"] for d in listed} == {"indexed"}


# -- refusal is honest ---------------------------------------------------------

def test_a_refused_upload_is_reported_on_the_row_not_lost(client, app, monkeypatch):
    """When the backlog is full the file is already stored and the row exists.

    Raising here would answer 500 and leave an invisible ``pending`` row that
    nothing will ever process — the exact class of stuck document this milestone
    removes. The document is failed with a message that names the way out.
    """
    from nutrimind.services.jobs import JobQueueFull

    def full(*_args, **_kwargs):
        raise JobQueueFull("queue is full")

    monkeypatch.setattr(_jobs(app), "submit", full)

    response = _post(client, "refused.pdf")
    assert response.status_code == 202

    listed = client.get("/api/documents").get_json()["documents"]
    assert listed[0]["status"] == "failed"
    assert "busy" in listed[0]["error"].lower()
    assert "re-index" in listed[0]["error"].lower()


def test_a_document_deleted_mid_ingest_does_not_resurrect(client, app, blocked_ingest):
    """Deleted while its job is *running*."""
    started, release = blocked_ingest
    document_id = _post(client, "doomed.pdf").get_json()["document"]["id"]
    assert started.wait(10)

    assert client.delete(f"/api/documents/{document_id}").status_code == 200
    release.set()
    assert _jobs(app).wait_idle(30)

    assert client.get("/api/documents").get_json()["documents"] == []


def test_a_document_deleted_while_still_queued_is_skipped(client, app, monkeypatch):
    """Deleted while its job is *waiting* — the job then names a row that is gone.

    The job carries an id rather than an instance, precisely so it re-reads the
    row instead of trusting one loaded on another thread. Re-reading is what
    makes this case a no-op instead of an error.

    A single worker is installed so the second upload genuinely waits in the
    queue rather than starting immediately alongside the first.
    """
    from nutrimind.services.jobs import EXTENSION_KEY, JobRunner

    single = JobRunner(app, max_workers=1, queue_limit=8)
    monkeypatch.setitem(app.extensions, EXTENSION_KEY, single)

    release = threading.Event()
    blocker_started = threading.Event()

    import nutrimind.services.rag_service as rag_service
    real = rag_service.process_document

    def gate(document, **kwargs):
        blocker_started.set()
        release.wait(30)
        return real(document, **kwargs)

    monkeypatch.setattr(rag_service, "process_document", gate)
    try:
        _post(client, "blocker.pdf", [PAGE_ONE, PAGE_ONE])
        assert blocker_started.wait(10), "the first job never started"

        queued_id = _post(client, "queued.pdf",
                          [PAGE_TWO, PAGE_TWO]).get_json()["document"]["id"]
        assert client.delete(f"/api/documents/{queued_id}").status_code == 200

        release.set()
        assert single.wait_idle(30)
    finally:
        release.set()
        single.shutdown()

    remaining = [d["filename"] for d in client.get("/api/documents").get_json()["documents"]]
    assert remaining == ["blocker.pdf"], "the deleted document came back"
