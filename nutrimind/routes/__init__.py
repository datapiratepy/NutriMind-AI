"""Blueprint registration, the current-user helper, and JSON response helpers.

Response conventions (used by every API blueprint):

* success  -> ``200/201`` with a plain JSON object (documented per endpoint)
* failure  -> ``{"error": {"code", "message", "hint?", "request_id"}}`` with
  the status from the exception's ``http_status`` (see ``nutrimind/__init__``).
"""

from __future__ import annotations

from flask import Flask, jsonify
from flask_login import current_user


def ok(data: dict, status: int = 200):
    """Uniform success response."""
    return jsonify(data), status


def current_user_id() -> int:
    """Primary key of the signed-in user.

    Every query that touches user data goes through this rather than reading
    ``current_user.id`` directly, so there is exactly one place to audit and one
    place to change. Tenancy bugs do not raise — they quietly return someone
    else's rows — so the value of a single chokepoint is that reviewing it is
    possible at all.

    Only ever called behind ``@login_required``; the assertion turns a missing
    decorator into an immediate, obvious failure rather than a query filtered on
    ``None`` that returns nothing and looks like empty data.
    """
    assert current_user.is_authenticated, (  # noqa: S101 — invariant, not validation
        "current_user_id() reached without authentication — the route is "
        "missing @login_required")
    return int(current_user.id)


def register_blueprints(app: Flask, csrf) -> None:
    """Register all blueprints.

    CSRF applies to everything. The blanket exemption the JSON API used to carry
    was defensible only while there was no session to forge a request against;
    adding authentication is exactly what removed that defence. The browser
    client sends the token as an ``X-CSRFToken`` header (see static/js/app.js).

    ``/api/health`` is exempt because uptime monitors cannot fetch a token, and
    it is a read-only liveness probe with nothing to forge.
    """
    from nutrimind.routes.auth import auth
    from nutrimind.routes.chat_api import chat_api
    from nutrimind.routes.dashboard_api import dashboard_api
    from nutrimind.routes.knowledge_api import knowledge_api
    from nutrimind.routes.meals_api import meals_api
    from nutrimind.routes.pages import pages
    from nutrimind.routes.profile_api import profile_api
    from nutrimind.routes.system_api import health, system_api

    app.register_blueprint(pages)
    app.register_blueprint(auth)
    app.register_blueprint(health)
    for blueprint in (profile_api, meals_api, dashboard_api, system_api,
                      chat_api, knowledge_api):
        app.register_blueprint(blueprint)

    csrf.exempt(health)
