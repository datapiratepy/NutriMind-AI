#!/usr/bin/env python3
"""Verify that a live database matches the SQLAlchemy models.

Usage::

    python scripts/check_schema.py                 # uses DATABASE_URI
    DATABASE_URI=postgresql+psycopg://... python scripts/check_schema.py

Exits 0 when the schema matches, 1 when it has drifted, printing what differs.

Two jobs:

* **CI** runs it against a real PostgreSQL server after applying the migrations,
  which is what catches dialect problems that SQLite cannot show — column types,
  VARCHAR limits (SQLite ignores them entirely), constraint rendering.
* **Deployment** can run it after ``flask db upgrade`` as a release gate: it
  answers "is the database this code is about to serve actually the right
  shape?" before any traffic arrives.

``tests/unit/test_migrations.py`` performs the same comparison on SQLite during
the test run. This script exists because that test cannot reach a PostgreSQL
server, and because a release gate has to be runnable outside pytest.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from alembic.autogenerate import compare_metadata  # noqa: E402
from alembic.migration import MigrationContext  # noqa: E402

from nutrimind import create_app  # noqa: E402
from nutrimind.extensions import db  # noqa: E402


def main() -> int:
    app = create_app()
    with app.app_context():
        engine = db.engine
        dialect = engine.dialect.name
        print(f"database: {dialect} ({engine.url.render_as_string(hide_password=True)})")

        with engine.connect() as connection:
            context = MigrationContext.configure(
                connection,
                opts={
                    "compare_type": True,
                    # Batch mode is a SQLite workaround for its missing ALTER
                    # support. Enabling it elsewhere makes the comparison report
                    # differences that do not exist.
                    "render_as_batch": dialect == "sqlite",
                },
            )
            differences = compare_metadata(context, db.metadata)

    if not differences:
        print("schema matches the models — no drift")
        return 0

    print(f"\nSCHEMA DRIFT: {len(differences)} difference(s)\n")
    for difference in differences:
        print(f"  - {difference}")
    print(
        "\nThe database does not match the models. Either a migration is "
        "missing:\n"
        '    flask --app nutrimind db migrate -m "<what changed>"\n'
        "or migrations were not applied:\n"
        "    flask --app nutrimind db upgrade"
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
