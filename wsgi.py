"""Production WSGI entry point.

    waitress-serve --host 0.0.0.0 --port 8000 --threads 8 wsgi:app

Separate from ``run.py`` (the development server) and from ``create_app()``
itself, because this module does two things a plain app factory must not.

**It installs signal handlers.** ``create_app()`` is called by the test suite
hundreds of times and could be called from a worker thread, where
``signal.signal`` raises. Process-wide side effects belong to the process's
entry point, not to building an object.

**It does not apply migrations.** Schema changes are an explicit deployment
step (``scripts/upgrade_database.py``), run once, before the new process
starts. A container that migrates on boot will re-run migrations on every
restart, and a crash-looping container would retry a failing migration forever
against live data. See docs/RUNBOOK.md.
"""

from __future__ import annotations

from nutrimind import create_app
from nutrimind.services.jobs import get_jobs, install_signal_handlers

app = create_app()

# SIGTERM is what a container runtime sends first. Draining here means a deploy
# finishes the document it was indexing instead of abandoning it — and if the
# drain outlasts the grace period, the startup recovery added in Milestone 4
# turns the abandoned job into a 'failed' row with a Re-index button rather than
# one stuck on 'processing' forever.
install_signal_handlers(get_jobs(app))
