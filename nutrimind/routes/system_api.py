"""System diagnostics API.

Endpoints
    GET /api/health       -> liveness: app + database + LLM mode
    GET /api/system/info  -> full diagnostics for demos and troubleshooting
"""

from __future__ import annotations

import platform
import sys
from importlib.metadata import version as _pkg_version

from flask import Blueprint, current_app

from nutrimind.extensions import db
from nutrimind.routes import ok
from nutrimind.services.llm import describe_llm

system_api = Blueprint("system_api", __name__, url_prefix="/api")


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


@system_api.get("/health")
def health():
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
