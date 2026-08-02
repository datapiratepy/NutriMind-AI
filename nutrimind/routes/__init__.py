"""Blueprint registration and shared JSON response helpers.

Response conventions (used by every API blueprint):

* success  -> ``200/201`` with a plain JSON object (documented per endpoint)
* failure  -> ``{"error": {"code", "message", "hint?", "request_id"}}`` with
  the status from the exception's ``http_status`` (see ``nutrimind/__init__``).
"""

from __future__ import annotations

from flask import Flask, jsonify


def ok(data: dict, status: int = 200):
    """Uniform success response."""
    return jsonify(data), status


def register_blueprints(app: Flask, csrf) -> None:
    """Register all blueprints; JSON APIs are exempt from form-CSRF.

    Rationale (and its limit): the API is same-origin JSON consumed by our own
    fetch() code, and with no authentication there is no session for a forged
    request to ride. That stops being true the moment login exists — the blanket
    exemption must be removed together with authentication, not after it.
    Note that /api/documents accepts multipart/form-data, which browsers send
    cross-origin without a preflight.
    """
    from nutrimind.routes.chat_api import chat_api
    from nutrimind.routes.dashboard_api import dashboard_api
    from nutrimind.routes.knowledge_api import knowledge_api
    from nutrimind.routes.meals_api import meals_api
    from nutrimind.routes.pages import pages
    from nutrimind.routes.profile_api import profile_api
    from nutrimind.routes.system_api import system_api

    app.register_blueprint(pages)
    for blueprint in (profile_api, meals_api, dashboard_api, system_api,
                      chat_api, knowledge_api):
        app.register_blueprint(blueprint)
        csrf.exempt(blueprint)
