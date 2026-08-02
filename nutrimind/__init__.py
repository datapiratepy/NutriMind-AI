"""NutriMind AI — application package and Flask app factory.

``create_app()`` wires the layers defined in ARCHITECTURE.md §2:
configuration -> logging -> database -> blueprints -> error handling.
The app boots fully in demo mode with zero IBM credentials.
"""

from __future__ import annotations

import logging
from pathlib import Path

from flask import Flask, g, jsonify, render_template, request
from sqlalchemy.exc import SQLAlchemyError
from werkzeug.exceptions import HTTPException

from nutrimind.config import Settings, load_settings, resolve_app_mode, validate_settings
from nutrimind.exceptions import ConfigurationError, NutriMindError
from nutrimind.extensions import csrf, db, migrate
from nutrimind.utils.decorators import init_request_middleware
from nutrimind.utils.logging_config import configure_logging

__version__ = "1.0.0"

logger = logging.getLogger(__name__)


def create_app(settings: Settings | None = None) -> Flask:
    """Build and return a fully configured Flask application.

    :param settings: injectable settings (tests); defaults to ``.env`` loading.
    :raises ConfigurationError: when settings validation reports fatal errors.
    """
    settings = settings or load_settings()
    configure_logging(settings)

    errors, warnings = validate_settings(settings)
    for message in warnings:
        logger.warning("config: %s", message)
    if errors:
        raise ConfigurationError(
            "Startup aborted: " + " | ".join(errors),
            hint="Fix the values above in .env (see docs/IBM_SETUP.md).",
        )

    app = Flask(__name__, instance_path=str(settings.instance_dir))
    app.config.update(
        SECRET_KEY=settings.secret_key,
        DEBUG=settings.debug,
        SQLALCHEMY_DATABASE_URI=settings.database_uri,
        MAX_CONTENT_LENGTH=settings.max_upload_mb * 1024 * 1024,
        JSON_SORT_KEYS=False,
        NUTRIMIND_SETTINGS=settings,
    )

    db.init_app(app)
    csrf.init_app(app)  # JSON API blueprints are exempted during registration

    # Importing the models package registers every table on ``db.metadata``,
    # which is what Alembic autogenerate compares against.
    import nutrimind.models  # noqa: F401

    # The schema is created and evolved by the migration scripts, never here.
    # ``db.create_all()`` only ever creates *missing tables* — it cannot add a
    # column, change a constraint or backfill data, so it silently does nothing
    # useful the first time the schema changes under real data. Applying
    # migrations is an explicit step: automatic in ``run.py`` for development,
    # and a deliberate ``flask db upgrade`` in the deployment runbook, where
    # several workers must not race each other to migrate.
    migrate.init_app(app, db)

    init_request_middleware(app)
    _register_blueprints(app)
    _register_error_handlers(app)

    logger.info("NutriMind AI v%s started — requested mode '%s' (effective '%s')",
                __version__, settings.app_mode, resolve_app_mode(settings))
    return app


#: Absolute location of the Alembic scripts, resolved from the package rather
#: than the caller's working directory.
MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"


def _baseline_revision() -> str:
    """The first revision in the history, read from the scripts themselves."""
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    config = Config(str(MIGRATIONS_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    return ScriptDirectory.from_config(config).get_base()


def _needs_baseline_stamp(connection) -> bool:
    """True when the database holds our schema but records no revision.

    The test is "has Alembic recorded a revision", not "does ``alembic_version``
    exist". Those differ in a state that occurs in practice: Alembic creates
    ``alembic_version`` *before* running a migration and writes the row after,
    so an upgrade that fails part-way leaves the table present and **empty**.
    Checking for the table alone would treat such a database as already managed,
    skip adoption, and fail exactly the same way on every retry.

    Both no-revision cases mean the same thing when application tables are
    present: the schema predates migrations. It was built by the
    ``db.create_all()`` used before Alembic was adopted, or by a baseline run
    that died on ``table ... already exists``. Either way the tables are real and
    the baseline must be stamped rather than executed.
    """
    from alembic.migration import MigrationContext
    from sqlalchemy import inspect

    if MigrationContext.configure(connection).get_current_revision() is not None:
        return False
    return bool(set(inspect(connection).get_table_names()) & set(db.metadata.tables))


def apply_migrations(app: Flask) -> None:
    """Bring the database up to the latest revision, from any starting state.

    Handles the three states a database can be in:

    * **empty** — no tables at all; the migrations build the schema.
    * **pre-Alembic** — the schema exists but nothing recorded how it got there
      (any database created before this milestone). It is stamped at the
      baseline revision, which records "this schema is already applied" without
      executing it, and then upgraded through anything newer.
    * **already managed** — a normal upgrade to the newest revision.

    For **single-process entry points only** — the development server and the
    operational scripts. Concurrent web workers must not race each other to
    migrate the same database, so deployments run this once, deliberately, via
    ``scripts/upgrade_database.py``.

    Shared rather than inlined at each call site because both the adoption
    detection and locating ``migrations/`` relative to the caller are easy to
    get subtly wrong, and both fail quietly rather than loudly.
    """
    from flask_migrate import stamp, upgrade

    with app.app_context():
        with db.engine.connect() as connection:
            adopt = _needs_baseline_stamp(connection)

        if adopt:
            baseline = _baseline_revision()
            logger.warning(
                "Database has tables but no recorded revision — adopting it at "
                "baseline %s. The schema is not modified; this only records that "
                "the baseline is already applied.", baseline)
            stamp(directory=str(MIGRATIONS_DIR), revision=baseline)

        upgrade(directory=str(MIGRATIONS_DIR))


def _register_blueprints(app: Flask) -> None:
    """Attach all route blueprints; JSON APIs are CSRF-exempt (see notes)."""
    from nutrimind.routes import register_blueprints

    register_blueprints(app, csrf)


def _register_error_handlers(app: Flask) -> None:
    """Uniform error responses: JSON envelope for /api, friendly pages elsewhere."""

    def _wants_json() -> bool:
        return request.path.startswith("/api/") or request.is_json

    def _json_error(status: int, code: str, message: str, hint: str | None = None):
        payload: dict = {"code": code, "message": message}
        if hint:
            payload["hint"] = hint
        payload["request_id"] = getattr(g, "request_id", "-")
        return jsonify(error=payload), status

    @app.errorhandler(NutriMindError)
    def _handle_domain_error(exc: NutriMindError):
        logger.warning("%s: %s", type(exc).__name__, exc.user_message)
        if _wants_json():
            body = exc.to_payload()
            body["request_id"] = getattr(g, "request_id", "-")
            return jsonify(error=body), exc.http_status
        return render_template("errors/500.html", message=exc.user_message), exc.http_status

    @app.errorhandler(SQLAlchemyError)
    def _handle_database_error(_exc: SQLAlchemyError):
        logger.exception("database error")
        db.session.rollback()
        if _wants_json():
            return _json_error(500, "database_error",
                               "A database error occurred. Please try again.")
        return render_template("errors/500.html",
                               message="A database error occurred."), 500

    @app.errorhandler(404)
    def _handle_not_found(_exc):
        if _wants_json():
            return _json_error(404, "not_found", "The requested resource was not found.")
        return render_template("errors/404.html"), 404

    @app.errorhandler(405)
    def _handle_method_not_allowed(_exc):
        return _json_error(405, "method_not_allowed",
                           "That HTTP method is not allowed on this endpoint.")

    @app.errorhandler(413)
    def _handle_too_large(_exc):
        settings: Settings = app.config["NUTRIMIND_SETTINGS"]
        return _json_error(413, "payload_too_large",
                           f"The upload exceeds the {settings.max_upload_mb} MB limit.")

    @app.errorhandler(Exception)
    def _handle_unexpected(exc: Exception):
        if isinstance(exc, HTTPException):  # let other HTTP codes pass through
            return exc
        logger.exception("unhandled exception")
        if _wants_json():
            return _json_error(500, "internal_error",
                               "An unexpected error occurred. The details were logged.")
        return render_template("errors/500.html",
                               message="An unexpected error occurred."), 500
