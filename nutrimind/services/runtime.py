"""Runtime ownership of the vector store, and recovery from an unclean stop.

The supported process model
---------------------------
**One process, many threads.** Not because of taste — because of measurement.

ChromaDB 1.5.9 keeps vector data in two places with different sharing
properties. Metadata lives in SQLite and is coherent across processes. The HNSW
vector index is read through a **per-process cached reader** that does not
refresh when another process writes. Two processes sharing one Chroma directory
therefore diverge, in one of two ways depending on when each client opened:

* a client opened while the collection was empty raises
  ``InternalError: Error creating hnsw segment reader: Nothing found on disk``
  on every query once a peer has written;
* a client that already held a segment simply **cannot see** the peer's
  vectors — ``count()`` reports them, ``get()`` returns them, and ``query()``
  does not find them.

The second is the dangerous one. Nothing errors. The document list shows
"indexed" with the right chunk count, because that count comes from the
coherent metadata path, and retrieval quietly returns nothing — which is
indistinguishable from the retrieval bug fixed in Milestone 3, and would arrive
intermittently, on whichever share of requests landed on the other worker.

Writes are *not* the problem: two processes writing 120 interleaved records
produced 120 correct, searchable rows with no corruption. It is readers that
diverge, so running ``gunicorn -w 2`` does not damage the store — it makes
half the workers unable to search it.

Threads, by contrast, are safe: one client under 8 concurrent writer threads
and 3 concurrent reader threads completed 200 adds with zero errors, nothing
lost and nothing unsearchable. Hence ``--threads N`` rather than ``-w N``.

This module makes that constraint enforceable rather than merely documented: a
second process is detected at startup and told exactly why it is unsupported.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from flask import Flask

from nutrimind.config import Settings

logger = logging.getLogger(__name__)

#: Lock file inside the Chroma directory — the resource actually being guarded.
LOCK_FILENAME = ".nutrimind-runtime.lock"

#: Held open for the life of the process. The OS releases the lock when the
#: process dies, which is why this is a lock and not a PID file: a PID file left
#: behind by a crash has to be distinguished from a live owner, and PIDs get
#: reused. An advisory lock has neither problem.
_handle = None

_MULTIPROCESS_WARNING = (
    "ANOTHER PROCESS IS ALREADY USING THIS CHROMA DIRECTORY (%s).\n"
    "        NutriMind supports ONE process with MANY THREADS, not multiple "
    "worker processes.\n"
    "        ChromaDB caches its HNSW index reader per process and never "
    "refreshes it, so a second\n"
    "        worker will report the correct chunk count and still find nothing "
    "when it searches --\n"
    "        retrieval silently returns no results on whichever requests land "
    "here, with no error.\n"
    "        Replace 'gunicorn -w N' with 'gunicorn -w 1 --threads N', or use "
    "'waitress-serve --threads N'.\n"
    "        See docs/DEPLOYMENT.md 'Production process model'."
)


def _try_lock(path: Path) -> bool:
    """Take a non-blocking exclusive lock on ``path``. False if already held.

    Both platform branches are best effort: if locking is unavailable the guard
    disables itself rather than blocking startup. Refusing to boot because an
    advisory lock could not be evaluated would turn a diagnostic into an outage.
    """
    global _handle
    release_runtime()  # a re-created app (tests) must not leak the old handle
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+b")
    try:
        if os.name == "nt":  # pragma: no cover — exercised on Windows only
            import msvcrt

            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        return False
    except (ImportError, AttributeError):  # pragma: no cover — exotic platform
        logger.debug("file locking unavailable; multi-process guard disabled")
        _handle = handle
        return True

    handle.seek(0)
    handle.truncate()
    handle.write(f"{os.getpid()}\n".encode())
    handle.flush()
    _handle = handle
    return True


def claim_runtime(settings: Settings) -> bool:
    """Become the sole owner of this Chroma directory, or report that we are not.

    :returns: True when this process holds the store exclusively. False means
        another live process already does, and this one will serve requests with
        a vector index that cannot see the other's writes.

    Never raises. A guard that can prevent startup is worse than the problem it
    reports.
    """
    lock_path = Path(settings.chroma_dir) / LOCK_FILENAME
    try:
        if _try_lock(lock_path):
            logger.debug("runtime lock acquired: %s (pid %d)", lock_path, os.getpid())
            return True
    except Exception:  # noqa: BLE001 — diagnostics must not break startup
        logger.debug("runtime lock check failed; guard disabled", exc_info=True)
        return True

    logger.error(_MULTIPROCESS_WARNING, settings.chroma_dir)
    return False


def release_runtime() -> None:
    """Drop the lock. Only needed by tests; the OS does this on process exit."""
    global _handle
    if _handle is not None:
        try:
            _handle.close()
        except OSError:  # pragma: no cover — closing twice is not interesting
            pass
        _handle = None


def recover_interrupted_ingestions(app: Flask) -> int:
    """Fail any document left mid-ingestion by a process that is no longer running.

    ``pending`` means queued and ``processing`` means a worker had started, and
    both live only in the memory of the process that owned the job. Once that
    process is gone — a deploy, a restart, an OOM kill — no thread exists to
    finish the work or to record that it stopped. Before this, such a row stayed
    ``processing`` permanently: the knowledge page polled it every 2.5 seconds
    forever, and ``document_ids_for()`` filters on ``indexed`` so it also
    contributed nothing to retrieval. A row that can never change is worse than
    a failure, because a failure has a button next to it.

    Only safe to call when this process owns the store (see
    :func:`claim_runtime`) — otherwise it would fail another live process's
    in-flight work.

    :returns: how many documents were transitioned to ``failed``.
    """
    from sqlalchemy import inspect

    from nutrimind.extensions import db
    from nutrimind.models import Document

    with app.app_context():
        # A fresh install has no tables until the migrations run. Asking anyway
        # would raise on every boot before the first upgrade.
        if not inspect(db.engine).has_table(Document.__tablename__):
            return 0

        stranded = list(db.session.execute(
            db.select(Document).where(Document.status.in_(("pending", "processing")))
        ).scalars())
        for document in stranded:
            document.mark_failed(
                "Indexing was interrupted when the application stopped. "
                "Use Re-index to run it again.")
        if stranded:
            db.session.commit()
            logger.warning(
                "recovered %d document(s) left mid-indexing by a previous run: %s",
                len(stranded), ", ".join(d.filename for d in stranded))
        return len(stranded)
