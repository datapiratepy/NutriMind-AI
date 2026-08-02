"""The migration chain must produce exactly the schema the models describe.

This is the one place the real Alembic scripts are executed. Everywhere else the
suite builds the schema with ``create_all()`` for speed, which means a migration
could silently drift from the models and no other test would notice — the
failure would surface on a fresh production deploy instead.

Drift is easy to cause and invisible in review: adding a column to a model and
forgetting to generate a revision leaves a codebase whose tests all pass and
whose deployment is broken.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from flask_migrate import upgrade
from sqlalchemy import create_engine, inspect

from nutrimind import _baseline_revision, apply_migrations, create_app
from nutrimind.config import load_settings
from nutrimind.extensions import db
from nutrimind.models import UserProfile

MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "migrations"

#: Tables the migrations are expected to create, besides Alembic's bookkeeping.
EXPECTED_TABLES = {
    "bmi_records",
    "chat_messages",
    "documents",
    "meal_logs",
    "meal_plans",
    "user_profile",
    "water_logs",
}


@pytest.fixture()
def migrated_db(monkeypatch, tmp_path):
    """An empty database taken through the full migration chain."""
    database_path = tmp_path / "migrated.db"
    monkeypatch.setenv("APP_MODE", "demo")
    monkeypatch.setenv("DATABASE_URI", f"sqlite:///{database_path.as_posix()}")
    monkeypatch.setenv("NUTRIMIND_INSTANCE_DIR", str(tmp_path / "instance"))

    application = create_app(load_settings(ensure_dirs=True))
    with application.app_context():
        upgrade(directory=str(MIGRATIONS_DIR))
        yield database_path
        db.session.remove()
        db.engine.dispose()


def test_migrations_apply_to_an_empty_database(migrated_db):
    """The baseline must build the whole schema from nothing."""
    engine = create_engine(f"sqlite:///{migrated_db.as_posix()}")
    try:
        tables = set(inspect(engine).get_table_names())
    finally:
        engine.dispose()

    assert EXPECTED_TABLES <= tables, f"missing: {EXPECTED_TABLES - tables}"
    assert "alembic_version" in tables, "migration state was not recorded"


def test_migrated_schema_matches_the_models(migrated_db):
    """Autogenerate must find nothing left to do.

    A non-empty diff means the models and the migration scripts disagree: either
    a model changed without a revision, or a revision was hand-edited and no
    longer matches. Both ship a database that does not fit the running code.
    """
    engine = create_engine(f"sqlite:///{migrated_db.as_posix()}")
    try:
        with engine.connect() as connection:
            context = MigrationContext.configure(
                connection,
                # Matches migrations/env.py: SQLite cannot ALTER most things, so
                # Alembic rewrites tables in batches. Comparison has to use the
                # same setting or it reports differences that are not real.
                opts={"compare_type": True, "render_as_batch": True},
            )
            differences = compare_metadata(context, db.metadata)
    finally:
        engine.dispose()

    assert differences == [], (
        "Schema drift between the models and migrations/.\n"
        "Generate the missing revision with:\n"
        "    flask --app nutrimind db migrate -m \"<what changed>\"\n"
        f"Alembic reported: {differences}"
    )


def _stamped_revision() -> str:
    return db.session.execute(
        db.text("SELECT version_num FROM alembic_version")).scalar_one()


def test_pre_alembic_database_is_adopted_with_data_intact(app):
    """Regression: a database created before migrations existed must upgrade.

    The ``app`` fixture builds the schema with ``create_all()`` and no
    ``alembic_version`` table, which is exactly the shape of every database
    created before this milestone.

    Without adoption Alembic reads no version marker, concludes the database is
    empty, replays the baseline and dies on ``table bmi_records already
    exists`` — leaving the application unable to start against its own data.
    """
    with app.app_context():
        db.session.add(UserProfile(
            name="Harsh", age=21, gender="male", height_cm=175, weight_kg=70,
            activity_level="moderate", food_preference="vegetarian",
            weight_goal="maintain", medical_conditions=[], allergies=[]))
        db.session.commit()

    apply_migrations(app)

    with app.app_context():
        profile = UserProfile.get_singleton()
        assert profile is not None, "adoption destroyed existing data"
        assert profile.name == "Harsh"
        assert _stamped_revision() == _baseline_revision()


def test_half_adopted_database_recovers(app):
    """A database left behind by a *failed* baseline run must still recover.

    Alembic creates ``alembic_version`` before running a migration and writes the
    revision row after it succeeds. A baseline that dies on ``table ... already
    exists`` therefore leaves the table present and empty.

    That state is easy to mistake for "already managed" — the table is right
    there — which would skip adoption and reproduce the original failure on
    every retry, leaving the operator with a database that cannot be repaired by
    running the upgrade again.
    """
    with app.app_context():
        db.session.execute(db.text(
            "CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL, "
            "CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num))"))
        db.session.commit()
        assert db.session.execute(
            db.text("SELECT version_num FROM alembic_version")).scalars().all() == []

    apply_migrations(app)

    with app.app_context():
        assert _stamped_revision() == _baseline_revision()


def test_apply_migrations_is_idempotent(app):
    """Re-running must not stamp twice or re-execute anything.

    Both callers can run more than once in a session: `python run.py` after the
    seed script, or a deployment retried after a partial failure.
    """
    apply_migrations(app)
    apply_migrations(app)

    with app.app_context():
        versions = db.session.execute(
            db.text("SELECT version_num FROM alembic_version")).scalars().all()
        assert len(versions) == 1, f"migration state duplicated: {versions}"


def test_empty_database_is_not_mistaken_for_a_legacy_one(tmp_path, monkeypatch):
    """A genuinely empty database must be built by the migrations, not stamped.

    Stamping an empty database would record the schema as applied without
    creating a single table, and every later query would fail.
    """
    monkeypatch.setenv("APP_MODE", "demo")
    monkeypatch.setenv("DATABASE_URI", f"sqlite:///{(tmp_path / 'empty.db').as_posix()}")
    monkeypatch.setenv("NUTRIMIND_INSTANCE_DIR", str(tmp_path / "instance"))

    application = create_app(load_settings(ensure_dirs=True))
    apply_migrations(application)

    with application.app_context():
        try:
            tables = set(inspect(db.engine).get_table_names())
            assert EXPECTED_TABLES <= tables, f"missing: {EXPECTED_TABLES - tables}"
            assert _stamped_revision() == _baseline_revision()
        finally:
            db.session.remove()
            db.engine.dispose()


def test_migration_chain_is_linear(migrated_db):
    """Exactly one head, or `db upgrade` cannot know which revision to apply.

    Branching happens when two people generate a revision from the same parent;
    the merge is easy, but only if it is caught before deployment.
    """
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    config = Config(str(MIGRATIONS_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    heads = ScriptDirectory.from_config(config).get_heads()

    assert len(heads) == 1, f"migration history has branched: {heads}"
