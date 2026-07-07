"""NutriMind AI — application package and Flask app factory.

``create_app()`` wires the layers defined in ARCHITECTURE.md §2:
configuration -> logging -> database -> blueprints -> error handling.
The app boots fully in demo mode with zero IBM credentials.
"""

from __future__ import annotations

import logging

from flask import Flask, g, jsonify, render_template, request
from sqlalchemy.exc import SQLAlchemyError
from werkzeug.exceptions import HTTPException

from nutrimind.config import Settings, load_settings, resolve_app_mode, validate_settings
from nutrimind.exceptions import ConfigurationError, NutriMindError
from nutrimind.extensions import csrf, db
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

    with app.app_context():
        import nutrimind.models  # noqa: F401 — register all models
        db.create_all()

    init_request_middleware(app)
    _register_blueprints(app)
    _register_error_handlers(app)

    logger.info("NutriMind AI v%s started — requested mode '%s' (effective '%s')",
                __version__, settings.app_mode, resolve_app_mode(settings))
    return app


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
    def _handle_database_error(exc: SQLAlchemyError):
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
