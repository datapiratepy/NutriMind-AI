"""Runtime ownership of the vector store, and recovery from an unclean stop.

Two guarantees are covered here, both of which failed silently before Milestone 4:

* a second process sharing one Chroma directory is **detected and explained**,
  rather than quietly serving searches against a vector index it cannot see;
* a document left mid-indexing by a process that died reaches a terminal state,
  rather than sitting on ``processing`` forever with the UI polling it.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from nutrimind.extensions import db
from nutrimind.models import Document
from nutrimind.services.runtime import (
    LOCK_FILENAME,
    claim_runtime,
    recover_interrupted_ingestions,
    release_runtime,
)
from tests.conftest import make_user

REPO_ROOT = Path(__file__).resolve().parents[2]


def _document(user_id: int, status: str, filename: str = "guide.pdf") -> Document:
    row = Document(filename=filename, stored_name=f"instance/uploads/{filename}",
                   sha256=f"sha-{filename}-{status}", status=status,
                   user_id=user_id, condition_tags=[])
    db.session.add(row)
    db.session.commit()
    return row


# -- interrupted ingestion recovers -------------------------------------------

@pytest.mark.parametrize("stranded_status", ["pending", "processing"])
def test_a_document_left_mid_indexing_is_failed_at_startup(app, stranded_status):
    """Both non-terminal states live only in the memory of the process that owned
    the job. Once it is gone nothing will ever move them, so a restart must."""
    with app.app_context():
        user = make_user()
        row = _document(user.id, stranded_status)
        row_id = row.id

    assert recover_interrupted_ingestions(app) == 1

    with app.app_context():
        recovered = db.session.get(Document, row_id)
        assert recovered.status == "failed"
        assert "interrupted" in recovered.error.lower()
        assert "re-index" in recovered.error.lower(), "must name the way out"


def test_recovery_leaves_terminal_documents_untouched(app):
    """Failing an already-indexed document would destroy working search results."""
    with app.app_context():
        user = make_user()
        indexed = _document(user.id, "indexed", "indexed.pdf")
        indexed.chunk_count = 12
        failed = _document(user.id, "failed", "failed.pdf")
        failed.error = "original reason"
        db.session.commit()
        indexed_id, failed_id = indexed.id, failed.id

    assert recover_interrupted_ingestions(app) == 0

    with app.app_context():
        assert db.session.get(Document, indexed_id).status == "indexed"
        assert db.session.get(Document, indexed_id).chunk_count == 12
        assert db.session.get(Document, failed_id).error == "original reason"


def test_recovery_counts_every_stranded_document(app):
    with app.app_context():
        user = make_user()
        _document(user.id, "pending", "a.pdf")
        _document(user.id, "processing", "b.pdf")
        _document(user.id, "indexed", "c.pdf")

    assert recover_interrupted_ingestions(app) == 2


def test_recovery_is_a_no_op_before_the_tables_exist(app):
    """A fresh install boots before its first migration runs.

    Querying anyway would raise on every startup until someone upgraded, turning
    a diagnostic convenience into a boot failure.
    """
    with app.app_context():
        db.drop_all()
        assert recover_interrupted_ingestions(app) == 0


# -- the multi-process guard --------------------------------------------------

def test_claim_runtime_succeeds_when_nothing_else_holds_the_store(app):
    settings = app.config["NUTRIMIND_SETTINGS"]
    release_runtime()
    assert claim_runtime(settings) is True
    assert (Path(settings.chroma_dir) / LOCK_FILENAME).exists()


def test_claim_runtime_detects_a_second_live_process(app, caplog):
    """The guard's whole purpose, tested against a real competing process.

    An in-process check would not prove anything: the failure being guarded
    against is two OS processes sharing one Chroma directory, where ChromaDB's
    per-process HNSW reader cache makes one of them unable to find the other's
    vectors while still reporting the correct chunk count.
    """
    settings = app.config["NUTRIMIND_SETTINGS"]
    release_runtime()

    holder = subprocess.Popen(
        [sys.executable, "-c", textwrap.dedent("""
            import sys
            sys.path.insert(0, sys.argv[1])
            from nutrimind.services.runtime import _try_lock
            from pathlib import Path
            got = _try_lock(Path(sys.argv[2]))
            print("LOCKED" if got else "FAILED", flush=True)
            sys.stdin.readline()
        """), str(REPO_ROOT), str(Path(settings.chroma_dir) / LOCK_FILENAME)],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    try:
        assert holder.stdout.readline().strip() == "LOCKED", "helper never locked"
        with caplog.at_level("ERROR"):
            assert claim_runtime(settings) is False
    finally:
        holder.stdin.write("\n")
        holder.stdin.flush()
        holder.wait(timeout=10)

    message = caplog.text
    assert "ONE process" in message and "MANY THREADS" in message
    # The explanation is the deliverable. A bare "already running" would leave
    # the operator to guess, and the obvious guess (add more workers) is wrong.
    assert "--threads" in message
    assert "chunk count" in message, "must say why it is silent, not just that it is"


def test_guard_never_prevents_startup(app, monkeypatch):
    """A diagnostic that can stop the app is worse than the bug it reports."""
    import nutrimind.services.runtime as runtime_module

    def explode(_path):
        raise OSError("filesystem does not support locking")

    monkeypatch.setattr(runtime_module, "_try_lock", explode)
    assert claim_runtime(app.config["NUTRIMIND_SETTINGS"]) is True
