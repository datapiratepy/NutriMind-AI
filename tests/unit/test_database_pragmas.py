"""Per-connection SQLite pragmas, and the dialects they must not touch.

The application serves requests from many threads in one process, so the
journal mode is not a detail: under the default ``delete`` journal a writer
blocks readers and a reader blocks the writer.

Measured on this codebase before enabling WAL, and reported in both directions
because the result was not uniformly positive:

    write-only, 8 threads x 15 writes
        delete : p50  34ms  p95 246ms  max 975ms  0 errors
        wal    : p50  77ms  p95 250ms  max 591ms  0 errors    <- p50 worse

    mixed, 3 writers + 5 readers (what the app actually does)
        delete : read p50 122ms  p95 271ms   wall 1.70s
        wal    : read p50  92ms  p95 197ms   wall 1.36s
                      -25%       -27%          -20%

Zero errors in both modes, so this is a latency choice rather than a
correctness one.
"""

from __future__ import annotations

from sqlalchemy import create_engine, text

from nutrimind.extensions import db


def test_sqlite_uses_write_ahead_logging(app):
    """WAL lets readers and the writer proceed together, which is the whole
    point of a threaded single-process runtime."""
    with app.app_context():
        assert db.session.execute(text("PRAGMA journal_mode")).scalar() == "wal"


def test_sqlite_synchronous_is_normal(app):
    """WAL's conventional companion: no fsync per commit, still crash-safe.

    1 == NORMAL. FULL (2) fsyncs on every commit and gives most of the latency
    back; OFF (0) would risk corruption on power loss rather than merely losing
    the most recent transactions.
    """
    with app.app_context():
        assert db.session.execute(text("PRAGMA synchronous")).scalar() == 1


def test_foreign_keys_are_still_enforced(app):
    """The pragma that was already here must survive being joined by others.

    SQLite parses REFERENCES but ignores it without this, so ownership
    constraints PostgreSQL enforces would be decorative in development.
    """
    with app.app_context():
        assert db.session.execute(text("PRAGMA foreign_keys")).scalar() == 1


def test_pragmas_are_not_sent_to_other_dialects():
    """The listener is on the generic Engine and also sees PostgreSQL.

    ``PRAGMA`` is not valid SQL there, so an unguarded listener would break
    every non-SQLite connection — including the PostgreSQL job in CI. Asserted
    by driving the listener with a non-sqlite3 connection object and checking it
    issues nothing.
    """
    from nutrimind import _configure_sqlite_connection

    class _NotSQLite:
        """Stands in for a DBAPI connection from another driver."""

        def cursor(self):  # pragma: no cover — must never be reached
            raise AssertionError("pragmas were sent to a non-SQLite connection")

    _configure_sqlite_connection(_NotSQLite(), None)  # must be a no-op


def test_the_listener_applies_to_every_sqlite_engine_in_the_process(tmp_path):
    """Process-wide by design, and this pins that it stays that way.

    The listener is registered on SQLAlchemy's generic ``Engine`` rather than on
    the application's own engine, which was the existing choice for
    ``foreign_keys`` and now carries the journal settings too. The consequence
    is deliberate: engines the application does not create — Alembic builds its
    own during ``upgrade_database.py`` — get identical pragmas, so a migration
    cannot run with foreign keys silently off or leave the file in a different
    journal mode than the app expects.

    I wrote this test the other way round first, asserting that an unrelated
    engine would be untouched. It failed, and the code was right: that is the
    behaviour we want. Recorded here rather than quietly corrected.

    Worth knowing alongside it: ``journal_mode=WAL`` is a persistent property of
    the database file, not of a connection. Once any connection sets it the file
    stays in WAL, so this pragma is idempotent rather than per-session work.
    """
    engine = create_engine(f"sqlite:///{(tmp_path / 'plain.db').as_posix()}")
    with engine.connect() as connection:
        assert connection.execute(text("PRAGMA journal_mode")).scalar() == "wal"
        assert connection.execute(text("PRAGMA foreign_keys")).scalar() == 1
    engine.dispose()
