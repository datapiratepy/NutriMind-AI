"""The background job runner: bounded, contextual, and never silently lossy.

These test the runner itself rather than ingestion. The properties that matter
are the ones whose absence is invisible: a job that raises must not disappear, a
backlog must have a ceiling, and a pool thread must get its own app context and
give its database session back.
"""

from __future__ import annotations

import threading

import pytest

from nutrimind.services.jobs import JobQueueFull, JobRunner, get_jobs, init_jobs


@pytest.fixture()
def runner(app):
    made = JobRunner(app, max_workers=2, queue_limit=4)
    yield made
    made.shutdown()


# -- it actually runs off the calling thread ----------------------------------

def test_work_runs_on_a_different_thread(runner):
    """The whole point. If it ran inline, nothing here would be gained."""
    seen: list[int] = []
    runner.submit(lambda: seen.append(threading.get_ident()))
    assert runner.wait_idle(10)
    assert seen and seen[0] != threading.get_ident()


def test_wait_idle_reports_completion(runner):
    done = threading.Event()
    runner.submit(done.set)
    assert runner.wait_idle(10)
    assert done.is_set()
    assert runner.pending == 0


def test_wait_idle_times_out_while_work_is_in_flight(runner):
    """A false 'idle' would make every test built on it pass vacuously."""
    release = threading.Event()
    runner.submit(release.wait)
    try:
        assert runner.wait_idle(0.2) is False
    finally:
        release.set()
        runner.wait_idle(10)


# -- a job has a working application context ----------------------------------

def test_job_runs_inside_an_app_context(runner, app):
    """Pool threads have no context; a job that assumed one would raise."""
    from flask import current_app

    names: list[str] = []
    runner.submit(lambda: names.append(current_app.name))
    assert runner.wait_idle(10)
    assert names == [app.name]


def test_job_can_use_the_database_and_its_session_is_released(runner, app):
    """A job that leaks its session exhausts the connection pool over time."""
    from nutrimind.extensions import db

    counts: list[int] = []

    def work():
        from nutrimind.models import Document

        counts.append(len(list(db.session.execute(db.select(Document)).scalars())))

    runner.submit(work)
    assert runner.wait_idle(10)
    assert counts == [0]


# -- failures are recorded, never swallowed into an unread Future -------------

def test_a_raising_job_is_logged_and_does_not_kill_the_pool(runner, caplog):
    """ThreadPoolExecutor parks exceptions on a Future nobody holds.

    Without the handler in ``JobRunner._run`` the failure vanishes completely —
    no log line, no traceback, and the next job still runs, so nothing looks
    wrong. That is the specific way background work becomes untrustworthy.
    """
    def boom():
        raise RuntimeError("job exploded")

    with caplog.at_level("ERROR"):
        runner.submit(boom)
        assert runner.wait_idle(10)
    assert "job exploded" in caplog.text

    after: list[str] = []
    runner.submit(lambda: after.append("still alive"))
    assert runner.wait_idle(10)
    assert after == ["still alive"]


def test_pending_returns_to_zero_after_a_failure(runner):
    """A leaked count would slowly fill the backlog until uploads were refused."""
    def boom():
        raise RuntimeError("nope")

    runner.submit(boom)
    assert runner.wait_idle(10)
    assert runner.pending == 0


# -- the bound is real --------------------------------------------------------

def test_backlog_is_refused_once_the_queue_limit_is_reached(app):
    """ThreadPoolExecutor's queue is unbounded, so this ceiling is ours.

    Without it, a burst of uploads is accepted in full and becomes unbounded
    memory plus a queue that takes hours to drain — accepted work that will not
    start for a very long time, which is worse than a clear refusal.
    """
    small = JobRunner(app, max_workers=1, queue_limit=2)
    release = threading.Event()
    try:
        small.submit(release.wait)     # occupies the single worker
        small.submit(release.wait)     # fills the backlog to the limit
        with pytest.raises(JobQueueFull):
            small.submit(release.wait)
    finally:
        release.set()
        small.wait_idle(10)
        small.shutdown()


def test_submitting_after_shutdown_is_refused(app):
    stopped = JobRunner(app, max_workers=1, queue_limit=4)
    stopped.shutdown()
    with pytest.raises(JobQueueFull):
        stopped.submit(lambda: None)


# -- wiring -------------------------------------------------------------------

def test_get_jobs_returns_the_runner_the_app_was_given(app):
    made = init_jobs(app, max_workers=1, queue_limit=3)
    assert get_jobs(app) is made


def test_get_jobs_creates_one_on_demand(app):
    app.extensions.pop("nutrimind_jobs", None)
    created = get_jobs(app)
    assert created is get_jobs(app)
    created.shutdown()
