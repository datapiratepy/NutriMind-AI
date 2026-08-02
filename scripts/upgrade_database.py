#!/usr/bin/env python3
"""Bring the database up to the latest schema revision.

Usage::

    python scripts/upgrade_database.py

**This is the deployment entry point — use it instead of `flask db upgrade`.**

Bare ``flask db upgrade`` works only on a database Alembic already tracks. A
database created before this project adopted migrations has the tables but no
``alembic_version`` row, so Alembic reads no version marker, concludes it is
empty, replays the baseline revision and fails on the first ``CREATE TABLE``.

This script calls :func:`nutrimind.apply_migrations`, which detects that state
and stamps the baseline first — recording that the existing schema is already
applied, without modifying it — before upgrading through anything newer. On an
empty database, or one already under Alembic's control, it is an ordinary
upgrade.

Run it once per release, before starting the new workers, and never from the
workers themselves: several processes migrating the same database concurrently
is how a schema gets corrupted.

Pair it with ``scripts/check_schema.py`` to confirm the result matches the
models before serving traffic.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from sqlalchemy import inspect  # noqa: E402

from nutrimind import apply_migrations, create_app  # noqa: E402
from nutrimind.exceptions import NutriMindError  # noqa: E402
from nutrimind.extensions import db  # noqa: E402


def _current_revision() -> str:
    """Revision recorded in the database, or ``"(none)"`` before adoption."""
    if "alembic_version" not in set(inspect(db.engine).get_table_names()):
        return "(none)"
    row = db.session.execute(
        db.text("SELECT version_num FROM alembic_version")).scalars().first()
    return row or "(none)"


def main() -> int:
    app = create_app()

    with app.app_context():
        url = db.engine.url.render_as_string(hide_password=True)
        print(f"database: {db.engine.dialect.name} ({url})")
        print(f"before:   {_current_revision()}")

    try:
        apply_migrations(app)
    except NutriMindError as exc:
        print(f"\nFAILED: {exc.user_message}")
        return 1
    except Exception as exc:  # noqa: BLE001 — a CLI reports, it does not traceback
        print(f"\nFAILED: {type(exc).__name__}: {exc}")
        print("\nThe database was not upgraded. Restore from backup before "
              "retrying if any revision partially applied.")
        return 1

    with app.app_context():
        print(f"after:    {_current_revision()}")
        db.session.remove()

    print("\nSchema is up to date. Verify with: python scripts/check_schema.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
