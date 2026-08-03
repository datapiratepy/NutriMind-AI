"""System diagnostics API.

Endpoints
    GET /api/health       -> liveness/readiness: app + database + LLM mode (public)
    GET /api/system/info  -> full diagnostics (requires sign-in)

Split across two blueprints on purpose. ``/api/health`` has to be reachable by
uptime monitors and load balancers, which have no session and cannot fetch a
CSRF token, so it is public and exempt. ``/api/system/info`` reports Python and
Flask versions, the platform, model ids and the configured watsonx region —
individually harmless, collectively a free reconnaissance report — so it is
behind authentication.
"""

from __future__ import annotations

import platform
import sys
from importlib.metadata import version as _pkg_version

from flask import Blueprint, current_app
from flask_login import login_required

from nutrimind.extensions import db
from nutrimind.routes import ok
from nutrimind.services.llm import describe_llm

#: Public liveness probe. Registered separately so it can be CSRF-exempt and
#: unauthenticated without weakening anything else.
health = Blueprint("health", __name__, url_prefix="/api")

system_api = Blueprint("system_api", __name__, url_prefix="/api")


@system_api.before_request
@login_required
def _require_login():
    """Default-deny for this blueprint.

    Applied here rather than per route so that adding an endpoint cannot
    accidentally expose it — the failure mode of a forgotten decorator is a
    silent information leak, which nothing in the test suite would notice
    unless someone thought to write that specific test.
    """


def _database_status() -> str:
    try:
        db.session.execute(db.text("SELECT 1"))
        return "ok"
    except Exception:  # noqa: BLE001 — health endpoints must not raise
        return "error"


def _chroma_status(settings) -> dict | str:
    try:
        from nutrimind.services.rag_service import get_rag_service

        return get_rag_service(settings).status()
    except Exception as exc:  # noqa: BLE001 — diagnostics must not raise
        return f"unavailable: {exc}"


@health.get("/health")
def health_check():
    """Readiness probe: 200 when serving, 503 when a dependency is unusable.

    The status code carries the signal, not just the body — load balancers and
    uptime monitors read the code. Returning 200 with ``"status": "degraded"``
    means an app on a dead database still looks healthy to everything watching it.
    """
    settings = current_app.config["NUTRIMIND_SETTINGS"]
    llm = describe_llm(settings)
    database = _database_status()
    healthy = database == "ok"
    return ok({"status": "ok" if healthy else "degraded", "mode": llm["mode"],
               "mode_detail": llm["detail"], "database": database},
              200 if healthy else 503)


@system_api.get("/system/info")
def system_info():
    from nutrimind import __version__

    settings = current_app.config["NUTRIMIND_SETTINGS"]
    llm = describe_llm(settings)
    demo = llm["mode"] == "demo"
    return ok({
        "app": {"name": "NutriMind AI", "version": __version__,
                "environment": "development" if settings.debug else "production"},
        "mode": {"requested": settings.app_mode, "effective": llm["mode"],
                 "demo_active": demo, "detail": llm["detail"]},
        "ibm": {"provider": "demo (deterministic)" if demo else "ibm-watsonx-ai",
                "chat_model": settings.watsonx.model_id,
                "embedding_model": settings.watsonx.embedding_model_id,
                "embeddings_provider_setting": settings.embeddings_provider,
                "region_url": settings.watsonx.url if settings.watsonx.has_credentials
                else "not configured"},
        "storage": {"database": _database_status(), "chroma": _chroma_status(settings)},
        "runtime": {"python": platform.python_version(),
                    "flask": _pkg_version("flask"),
                    "platform": sys.platform},
    })
