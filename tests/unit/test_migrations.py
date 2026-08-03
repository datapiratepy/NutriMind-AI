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
    "users",
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


def _head_revision() -> str:
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    config = Config(str(MIGRATIONS_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    return ScriptDirectory.from_config(config).get_current_head()


@pytest.fixture()
def legacy_app(monkeypatch, tmp_path):
    """An app over a genuinely pre-Alembic database.

    Built by upgrading to the *baseline* revision and then deleting Alembic's
    bookkeeping — which reproduces a real Milestone-1 database, where the schema
    was created by ``db.create_all()`` and nothing recorded a revision.

    Deliberately not the ``app`` fixture: that runs ``create_all()`` against
    today's models, so its schema is at HEAD, not at the baseline. Adoption
    stamps the baseline, so testing against a HEAD-shaped database would prove
    the opposite of what these tests are for.
    """
    monkeypatch.setenv("APP_MODE", "demo")
    monkeypatch.setenv("DATABASE_URI", f"sqlite:///{(tmp_path / 'legacy.db').as_posix()}")
    monkeypatch.setenv("NUTRIMIND_INSTANCE_DIR", str(tmp_path / "instance"))

    application = create_app(load_settings(ensure_dirs=True))
    with application.app_context():
        upgrade(directory=str(MIGRATIONS_DIR), revision=_baseline_revision())
        db.session.execute(db.text("DROP TABLE alembic_version"))
        db.session.commit()
    yield application
    with application.app_context():
        db.session.remove()
        db.engine.dispose()


def _seed_legacy_profile(application) -> None:
    """Insert a pre-tenancy profile row: no user_id column exists yet."""
    with application.app_context():
        db.session.execute(db.text(
            "INSERT INTO user_profile (name, age, gender, height_cm, weight_kg, "
            "activity_level, medical_conditions, allergies, food_preference, "
            "weight_goal, created_at, updated_at) VALUES ('Harsh', 21, 'male', "
            "175, 70, 'moderate', '[]', '[]', 'vegetarian', 'maintain', "
            "'2026-01-01 00:00:00', '2026-01-01 00:00:00')"))
        db.session.commit()


def test_pre_alembic_database_is_adopted_with_data_intact(legacy_app):
    """Regression: a database created before migrations existed must upgrade.

    Without adoption Alembic reads no version marker, concludes the database is
    empty, replays the baseline and dies on ``table bmi_records already
    exists`` — leaving the application unable to start against its own data.
    """
    _seed_legacy_profile(legacy_app)

    apply_migrations(legacy_app)

    with legacy_app.app_context():
        profiles = list(db.session.execute(db.select(UserProfile)).scalars())
        assert len(profiles) == 1, "adoption destroyed existing data"
        assert profiles[0].name == "Harsh"
        # Adopted rows must end up owned, or later queries would never find them.
        assert profiles[0].user_id is not None
        assert _stamped_revision() == _head_revision()


def test_adoption_makes_existing_data_reachable_by_its_new_owner(legacy_app):
    """The adopted account must actually own the data, not merely exist.

    A backfill that created the account but left ``user_id`` unset would satisfy
    "nothing was deleted" while making every row permanently invisible, since
    every query now filters by owner.
    """
    _seed_legacy_profile(legacy_app)

    apply_migrations(legacy_app)

    with legacy_app.app_context():
        owner_id = db.session.execute(
            db.text("SELECT id FROM users WHERE email = 'legacy@nutrimind.invalid'")
        ).scalar_one()
        assert UserProfile.for_user(owner_id) is not None


def test_half_adopted_database_recovers(legacy_app):
    """A database left behind by a *failed* baseline run must still recover.

    Alembic creates ``alembic_version`` before running a migration and writes the
    revision row after it succeeds. A baseline that dies on ``table ... already
    exists`` therefore leaves the table present and empty.

    That state is easy to mistake for "already managed" — the table is right
    there — which would skip adoption and reproduce the original failure on
    every retry, leaving the operator with a database that cannot be repaired by
    running the upgrade again.
    """
    with legacy_app.app_context():
        db.session.execute(db.text(
            "CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL, "
            "CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num))"))
        db.session.commit()

    apply_migrations(legacy_app)

    with legacy_app.app_context():
        assert _stamped_revision() == _head_revision()


def test_empty_database_creates_no_placeholder_account(migrated_db):
    """A fresh install must not be given a ghost account.

    The tenancy migration creates a placeholder owner only when there is data to
    adopt; doing it unconditionally would leave every new deployment with a
    permanent account nobody created and nobody can use.
    """
    engine = create_engine(f"sqlite:///{migrated_db.as_posix()}")
    try:
        with engine.connect() as connection:
            count = connection.execute(
                db.text("SELECT COUNT(*) FROM users")).scalar_one()
    finally:
        engine.dispose()
    assert count == 0


def test_apply_migrations_is_idempotent(legacy_app):
    """Re-running must not stamp twice or re-execute anything.

    Both callers can run more than once in a session: `python run.py` after the
    seed script, or a deployment retried after a partial failure.
    """
    apply_migrations(legacy_app)
    apply_migrations(legacy_app)

    with legacy_app.app_context():
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
            assert _stamped_revision() == _head_revision()
        finally:
            db.session.remove()
            db.engine.dispose()


def test_applying_migrations_does_not_disable_application_logging(legacy_app):
    """Regression: migrations must not switch the application's logging off.

    Alembic's ``env.py`` calls ``logging.config.fileConfig``, which defaults to
    ``disable_existing_loggers=True`` — that disables every logger already
    created and not named in ``alembic.ini``, which is all of ``nutrimind.*``.

    ``run.py`` applies migrations during startup, so the effect in production was
    an application that then ran with no access log, no request ids and no error
    records at all, for the life of the process. Nothing failed; the logs simply
    stopped, which is the worst way for observability to break.
    """
    import logging

    logger = logging.getLogger("nutrimind.routes.auth")
    root = logging.getLogger()
    assert not logger.disabled, "logger was already disabled before the test"
    handlers_before = list(root.handlers)
    level_before = root.level

    apply_migrations(legacy_app)

    assert not logger.disabled, (
        "applying migrations disabled the nutrimind loggers — "
        "migrations/env.py must not call fileConfig()")
    # Both halves matter. fileConfig also rewrites the root logger from
    # alembic.ini, which sets it to WARN and swaps in Alembic's console handler:
    # that alone drops the INFO access log and detaches the rotating file handler,
    # even with disable_existing_loggers=False.
    assert root.level == level_before, (
        f"migrations changed the root log level {level_before} -> {root.level}")
    assert list(root.handlers) == handlers_before, (
        "migrations replaced the root logging handlers — the application's "
        "log file would no longer be written")


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
