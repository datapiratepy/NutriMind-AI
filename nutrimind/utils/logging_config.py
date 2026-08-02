"""Logging setup: console + rotating file, per-request IDs, no secrets.

Format example::

    2026-07-06 15:04:11 INFO nutrimind.routes [a1b2c3d4] GET /api/health -> 200 (3ms)

The request ID comes from ``flask.g`` when inside a request context and is
also returned to clients in the ``X-Request-ID`` header and JSON error
bodies, so a user-reported error can be matched to a log line instantly.
"""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover — typing only
    from nutrimind.config import Settings

_FORMAT = "%(asctime)s %(levelname)s %(name)s [%(request_id)s] %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"
_CONFIGURED_FLAG = "_nutrimind_configured"


class RequestIdFilter(logging.Filter):
    """Inject the current request ID (or '-') into every log record."""

    def filter(self, record: logging.LogRecord) -> bool:
        request_id = "-"
        try:
            from flask import g, has_request_context

            if has_request_context():
                request_id = getattr(g, "request_id", "-")
        except Exception:  # noqa: BLE001 — pragma: no cover; logging must never raise
            pass
        record.request_id = request_id
        return True


def configure_logging(settings: "Settings") -> None:
    """Configure the root logger once per process (idempotent for tests)."""
    root = logging.getLogger()
    if getattr(root, _CONFIGURED_FLAG, False):
        return

    level = getattr(logging, settings.log_level, logging.INFO)
    formatter = logging.Formatter(_FORMAT, datefmt=_DATE_FORMAT)
    request_filter = RequestIdFilter()

    console = logging.StreamHandler()
    console.setFormatter(formatter)
    console.addFilter(request_filter)

    handlers: list[logging.Handler] = [console]
    try:
        log_dir = settings.instance_dir / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            log_dir / "nutrimind.log", maxBytes=1_000_000, backupCount=3, encoding="utf-8"
        )
        file_handler.setFormatter(formatter)
        file_handler.addFilter(request_filter)
        handlers.append(file_handler)
    except OSError:  # pragma: no cover — read-only environments still get console logs
        pass

    root.setLevel(level)
    for handler in handlers:
        root.addHandler(handler)

    # Werkzeug's per-request lines duplicate our own access log.
    logging.getLogger("werkzeug").setLevel(logging.WARNING)

    setattr(root, _CONFIGURED_FLAG, True)
