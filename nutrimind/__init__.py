"""NutriMind AI — application package and Flask app factory.

``create_app()`` wires the layers defined in ARCHITECTURE.md §2:
configuration -> logging -> database -> blueprints -> error handling.
The app boots fully in demo mode with zero IBM credentials.
"""

from __future__ import annotations

import datetime as dt
import logging
from pathlib import Path

from flask import Flask, g, jsonify, redirect, render_template, request, url_for
from flask_wtf.csrf import CSRFError
from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError
from werkzeug.exceptions import HTTPException

from nutrimind.config import Settings, load_settings, resolve_app_mode, validate_settings
from nutrimind.exceptions import ConfigurationError, NutriMindError
from nutrimind.extensions import csrf, db, login_manager, migrate
from nutrimind.security import init_security_headers
from nutrimind.utils.decorators import configure_proxy_awareness, init_request_middleware
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
        # -- session cookie hardening ------------------------------------
        # Not readable from JavaScript, so an XSS bug cannot exfiltrate the
        # session itself.
        SESSION_COOKIE_HTTPONLY=True,
        # Not sent on cross-site POSTs. A second line of defence behind CSRF
        # tokens, and the one that still works if a token check is ever missed.
        SESSION_COOKIE_SAMESITE="Lax",
        # HTTPS-only in production. Tied to debug rather than hard-coded so
        # local development over http still works; production runs debug off.
        SESSION_COOKIE_SECURE=not settings.debug,
        PERMANENT_SESSION_LIFETIME=dt.timedelta(days=settings.session_days),
        WTF_CSRF_TIME_LIMIT=None,  # tokens live as long as the session
        # -- "remember me" cookie -----------------------------------------
        # Flask-Login keeps this on its own REMEMBER_COOKIE_* keys, which do
        # NOT inherit from SESSION_COOKIE_*. Left unset, its defaults are
        # Secure=False, SameSite=None and a lifetime of 365 days — so the
        # longest-lived credential the app issues was the least protected one,
        # and it silently overrode SESSION_DAYS by a factor of 26. Measured
        # before this was added:
        #     remember_token -> Expires=+365d, HttpOnly, Path=/   (no Secure,
        #                                                          no SameSite)
        #     session        -> Secure, HttpOnly, Path=/, SameSite=Lax
        REMEMBER_COOKIE_SECURE=not settings.debug,
        REMEMBER_COOKIE_HTTPONLY=True,
        REMEMBER_COOKIE_SAMESITE="Lax",
        # One lifetime, one setting. "Remember me" should mean "do not make me
        # sign in on every visit", not "keep me signed in twenty-six times
        # longer than the documented session policy".
        REMEMBER_COOKIE_DURATION=dt.timedelta(days=settings.session_days),
    )

    db.init_app(app)
    csrf.init_app(app)
    _configure_login(app)

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

    configure_proxy_awareness(app, settings.trusted_proxy_hops)
    init_request_middleware(app)
    init_security_headers(app)
    _register_blueprints(app)
    _register_error_handlers(app)
    _init_runtime(app, settings)

    logger.info("NutriMind AI v%s started — requested mode '%s' (effective '%s')",
                __version__, settings.app_mode, resolve_app_mode(settings))
    return app


def _init_runtime(app: Flask, settings: Settings) -> None:
    """Start the background job pool and settle ownership of the vector store.

    The two are related. ``claim_runtime`` takes an advisory lock on the Chroma
    directory; holding it means this process is the only one using that store,
    which is both the supported deployment shape (see
    ``nutrimind/services/runtime.py`` for the measurements) and the precondition
    for recovering interrupted work: rows left ``pending`` or ``processing``
    can only be safely failed if no other live process might still be working on
    them.
    """
    from nutrimind.services.jobs import init_jobs
    from nutrimind.services.runtime import claim_runtime, recover_interrupted_ingestions

    init_jobs(app, max_workers=settings.ingest_workers,
              queue_limit=settings.ingest_queue_limit)
    if claim_runtime(settings):
        recover_interrupted_ingestions(app)


#: Absolute location of the Alembic scripts, resolved from the package rather
#: than the caller's working directory.
MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"


@event.listens_for(Engine, "connect")
def _configure_sqlite_connection(dbapi_connection, _record) -> None:
    """Per-connection SQLite pragmas: foreign keys, and WAL journalling.

    **foreign_keys** — SQLite parses ``REFERENCES`` but ignores it unless this
    pragma is set per connection, so ownership constraints that PostgreSQL
    enforces would be decorative in development, and a bug that orphans rows
    would pass locally and fail in production.

    **journal_mode=WAL** — the application serves requests from many threads in
    one process (see ``services/runtime.py``). Under the default ``delete``
    journal, a writer blocks readers and a reader blocks the writer, so page
    loads queue behind whoever is logging a glass of water.

    Measured on this codebase, 8 threads, before and after. The honest version
    of the result, including the case where WAL is *not* an improvement:

        write-only (8 threads x 15 writes)
            delete : p50  34ms  p95 246ms  max 975ms  0 errors
            wal    : p50  77ms  p95 250ms  max 591ms  0 errors   <- p50 worse

        mixed 3 writers + 5 readers (the realistic shape)
            delete : read p50 122ms p95 271ms | write p50 130ms | wall 1.70s
            wal    : read p50  92ms p95 197ms | write p50  95ms | wall 1.36s
                          -25%      -27%             -27%          -20%

    So WAL is not a free win everywhere: for pure serialised writes it costs a
    little. It is worth enabling because the real workload is read-dominated,
    and because zero errors in both modes means this is a latency choice rather
    than a correctness one.

    ``synchronous=NORMAL`` is the conventional companion to WAL: it stops
    fsyncing on every commit while still being crash-safe, since WAL's own
    checkpointing preserves committed transactions. A power loss can cost the
    most recent transactions, which for a nutrition tracker is an acceptable
    trade against the latency.

    Guarded by class name rather than dialect because the listener is on the
    generic Engine and also sees PostgreSQL connections, where these pragmas do
    not exist.
    """
    if not type(dbapi_connection).__module__.startswith("sqlite3"):
        return
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    # In-memory databases have no WAL to write to; asking is harmless but
    # pointless, and some SQLite builds refuse it outright.
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.close()


def _configure_login(app: Flask) -> None:
    """Wire session authentication and decide what unauthenticated users get."""
    login_manager.init_app(app)
    login_manager.login_view = "auth.login"
    login_manager.login_message = "Please sign in to continue."
    login_manager.session_protection = "strong"

    @login_manager.user_loader
    def _load_user(session_id: str):
        from nutrimind.models import User

        return User.from_session_id(session_id)

    @login_manager.unauthorized_handler
    def _unauthorized():
        """JSON for the API, a redirect for pages.

        The API must not answer with an HTML login page: the frontend would try
        to parse it as JSON and report a confusing error instead of "you are
        signed out".
        """
        if request.path.startswith("/api/"):
            return jsonify(error={
                "code": "authentication_required",
                "message": "Please sign in to continue.",
                "request_id": getattr(g, "request_id", "-"),
            }), 401
        return redirect(url_for("auth.login", next=request.full_path))


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

    @app.errorhandler(CSRFError)
    def _handle_csrf_error(exc: CSRFError):
        """Keep the JSON contract when a CSRF check fails.

        Flask-WTF raises CSRFError, a plain HTTPException, so without this it
        escapes as Werkzeug's stock HTML 400 page. Under ``/api/`` that breaks
        the documented envelope: the browser client parses every API response as
        JSON and reports "unexpected character at line 1 column 1" — column 1
        being the ``<`` of ``<!doctype html>``. The real cause is then invisible.
        """
        logger.warning("CSRF rejected: %s %s — %s",
                       request.method, request.path, exc.description)
        if _wants_json():
            return _json_error(
                400, "csrf_error",
                "Your session token was missing or expired.",
                hint="Reload the page and try again.")
        return render_template(
            "errors/500.html",
            message="Your session token was missing or expired. "
                    "Please reload the page and try again."), 400

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
