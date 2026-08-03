"""Server-rendered page routes; each renders a template that extends base.html.

Pages fall into two groups. Anything that displays a user's own data requires a
session; the landing and about pages stay public so a visitor can see what the
project is before deciding to sign up — which is the point of a portfolio
application.

The split is expressed as two lists rather than decorators scattered across the
module, so "which pages are public" is one readable thing to review instead of
an audit of every view function.
"""

from __future__ import annotations

from flask import Blueprint, redirect, render_template, url_for
from flask_login import current_user, login_required

pages = Blueprint("pages", __name__)

#: Reachable without signing in.
_PUBLIC_PAGES = {
    "/about": "about.html",
}

#: Require a session; these render the signed-in user's own data.
_PRIVATE_PAGES = {
    "/dashboard": "dashboard.html",
    "/chat": "chat.html",
    "/planner": "planner.html",
    "/analyzer": "analyzer.html",
    "/profile": "profile.html",
    "/knowledge": "knowledge.html",
    "/history": "history.html",
}


def _make_view(template: str, *, private: bool):
    def view() -> str:
        return render_template(template)

    # Flask registers by endpoint name; without this every generated view would
    # be called "view" and the second registration would collide.
    view.__name__ = template.rsplit(".", 1)[0]
    return login_required(view) if private else view


@pages.get("/")
def index():
    """Landing page, or straight to the dashboard for a signed-in user.

    Showing an authenticated visitor a marketing page they have already acted on
    is friction; their data is what they came back for.
    """
    if current_user.is_authenticated:
        return redirect(url_for("pages.dashboard"))
    return render_template("index.html")


for _rule, _template in _PUBLIC_PAGES.items():
    pages.add_url_rule(_rule, _template.rsplit(".", 1)[0],
                       _make_view(_template, private=False))

for _rule, _template in _PRIVATE_PAGES.items():
    pages.add_url_rule(_rule, _template.rsplit(".", 1)[0],
                       _make_view(_template, private=True))
