"""In-process background jobs: a bounded pool for work too slow for a request.

Why this exists
---------------
Document ingestion (extract -> chunk -> embed -> index) used to run inside the
HTTP request that uploaded the file. Measured on the zero-credential lexical
provider, a 400-page PDF takes 12.0s end to end (5.7s extract+chunk+embed,
6.3s Chroma add). That is survivable. The credentialed path is not: embeddings
are sent to watsonx in batches of ``_EMBED_BATCH_SIZE`` (16), so the same
document is **125 sequential HTTP round-trips**, and the app's own
``MAX_UPLOAD_MB`` default of 15 permits documents roughly twenty times larger
than that. Holding a worker thread and a browser connection open for that long
is what a background job is for.

Why not Celery / RQ / Redis
---------------------------
They solve a problem this application does not have. A task broker earns its
keep when jobs must survive process death, fan out across machines, or be
retried independently. Here there is exactly one process (see
``nutrimind.services.runtime`` for why), the work is idempotent because the
source PDF is on disk and re-index already exists as a user-facing button, and
the deployment target is a single small VPS. Adding a broker would add two
processes to deploy, supervise and monitor in exchange for nothing this app
needs. Interrupted work is recovered at startup instead — see
``recover_interrupted_ingestions``.

Bounded on purpose
------------------
``max_workers`` caps concurrency and ``queue_limit`` caps the backlog. Python's
``ThreadPoolExecutor`` has an unbounded queue, so without a limit a burst of
uploads becomes unbounded memory and an ingest queue nobody can drain. When the
backlog is full, submission is refused loudly rather than accepted and silently
delayed for minutes.

Threads and Flask
-----------------
A job runs on a pool thread, which has no application context and no request.
Each job therefore pushes its own ``app.app_context()`` and removes its session
afterwards: Flask-SQLAlchemy scopes the session to the app context, so a job
that does not clean up leaks a connection per run and eventually exhausts the
pool.
"""

from __future__ import annotations

import logging
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Callable

from flask import Flask

logger = logging.getLogger(__name__)

#: Key under which the runner is stored on ``app.extensions``.
EXTENSION_KEY = "nutrimind_jobs"


class JobQueueFull(RuntimeError):
    """Raised when the backlog is at ``queue_limit`` and work must be refused."""


class JobRunner:
    """A small bounded thread pool that runs work inside an app context.

    Not a general task framework — deliberately. It does one thing: run a
    callable off the request thread, with an application context, and account
    for how many are in flight so tests (and shutdown) can wait for quiet.
    """

    def __init__(self, app: Flask, *, max_workers: int = 2,
                 queue_limit: int = 32) -> None:
        self._app = app
        self._max_workers = max_workers
        self._queue_limit = queue_limit
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers, thread_name_prefix="nm-job")
        # ``_pending`` counts submitted-but-not-finished work, which is what
        # bounds the backlog and what ``wait_idle`` waits on. A plain counter
        # under a Condition is enough; there is no need to track futures.
        self._idle = threading.Condition()
        self._pending = 0
        self._shutdown = False

    # -- submission -----------------------------------------------------------

    @property
    def pending(self) -> int:
        """Jobs submitted but not yet finished (queued or running)."""
        with self._idle:
            return self._pending

    def submit(self, func: Callable[..., None], *args, **kwargs) -> None:
        """Queue ``func`` to run on a pool thread inside an app context.

        :raises JobQueueFull: the backlog is at its limit. Callers surface this
            to the user as "try again shortly" rather than queueing work that
            will not start for minutes.
        """
        with self._idle:
            if self._shutdown:
                raise JobQueueFull("The job runner is shutting down.")
            if self._pending >= self._queue_limit:
                raise JobQueueFull(
                    f"{self._pending} jobs are already queued "
                    f"(limit {self._queue_limit}).")
            self._pending += 1
        try:
            self._executor.submit(self._run, func, *args, **kwargs)
        except RuntimeError:  # pool already shut down — keep the count honest
            self._finished()
            raise

    def _run(self, func: Callable[..., None], *args, **kwargs) -> None:
        """Pool-thread entry point: app context in, session cleaned up, always.

        Every exception is logged and swallowed here. A job that raises into a
        ``ThreadPoolExecutor`` sets the exception on a Future nobody holds, so
        the failure would vanish silently — the specific way background work
        tends to become invisible. Jobs are expected to record their own failure
        in the database; this is the net beneath that.
        """
        try:
            with self._app.app_context():
                try:
                    func(*args, **kwargs)
                finally:
                    from nutrimind.extensions import db

                    db.session.remove()
        except Exception:  # noqa: BLE001 — a pool thread must never die silently
            logger.exception("background job failed: %s",
                             getattr(func, "__name__", func))
        finally:
            self._finished()

    def _finished(self) -> None:
        with self._idle:
            self._pending -= 1
            if self._pending <= 0:
                self._idle.notify_all()

    # -- lifecycle ------------------------------------------------------------

    def wait_idle(self, timeout: float = 30.0) -> bool:
        """Block until nothing is in flight. Returns False on timeout.

        Exists for tests and for shutdown. Tests use it instead of sleeping:
        polling a status column with a sleep is how a suite becomes flaky on a
        slower CI machine.
        """
        with self._idle:
            return self._idle.wait_for(lambda: self._pending <= 0, timeout=timeout)

    def shutdown(self, *, wait: bool = True) -> None:
        with self._idle:
            self._shutdown = True
        self._executor.shutdown(wait=wait)

    def drain(self, timeout: float = 25.0) -> bool:
        """Stop accepting work, then let what is running finish.

        The two halves are the point. Refusing new submissions immediately means
        a shutdown cannot be outrun by fresh uploads; waiting for the in-flight
        ones means a document being indexed when the deploy started is finished
        rather than abandoned.

        Returns False if the wait expired with work still running. That is not
        an error — Milestone 4 made an abandoned job recoverable, so the caller
        logs it and exits rather than blocking a deployment indefinitely.
        """
        with self._idle:
            self._shutdown = True
            outstanding = self._pending
        if outstanding:
            logger.info("draining %d background job(s) before shutdown", outstanding)
        drained = self.wait_idle(timeout)
        if not drained:
            logger.warning(
                "shutdown timed out with %d job(s) still running; they will be "
                "recovered as 'failed' on the next start", self.pending)
        self._executor.shutdown(wait=False)
        return drained


# -- wiring -------------------------------------------------------------------

def init_jobs(app: Flask, *, max_workers: int = 2, queue_limit: int = 32) -> JobRunner:
    """Create the runner and attach it to the app."""
    runner = JobRunner(app, max_workers=max_workers, queue_limit=queue_limit)
    app.extensions[EXTENSION_KEY] = runner
    return runner


def install_signal_handlers(runner: JobRunner) -> None:
    """Drain the pool on SIGTERM/SIGINT, then re-raise the default behaviour.

    Off by default and opted into by the server entry points only. Installing
    process-wide signal handlers as a side effect of building an app object
    would be wrong in tests (which build hundreds of apps) and impossible in a
    worker thread, where ``signal.signal`` raises ValueError.

    Chaining rather than replacing: whatever was installed before — the WSGI
    server's own graceful-stop handler, or Python's default — still runs after
    the drain. Swallowing SIGTERM would leave a container to be SIGKILLed by
    its runtime instead of exiting cleanly.
    """
    import signal

    def _handler(signum, frame):
        logger.info("received %s — draining background jobs",
                    signal.Signals(signum).name)
        runner.drain()
        if callable(previous.get(signum)):
            previous[signum](signum, frame)
        elif previous.get(signum) == signal.SIG_DFL:
            signal.signal(signum, signal.SIG_DFL)
            os.kill(os.getpid(), signum)

    previous: dict[int, object] = {}
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            previous[sig] = signal.signal(sig, _handler)
        except (ValueError, OSError):  # pragma: no cover — not the main thread
            logger.debug("could not install handler for %s", sig)


def get_jobs(app: Flask) -> JobRunner:
    """The runner for this app.

    Created on demand so an app built directly in a test still has one.
    """
    runner = app.extensions.get(EXTENSION_KEY)
    if runner is None:
        runner = init_jobs(app)
    return runner
