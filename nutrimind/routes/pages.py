"""Server-rendered page routes; each renders a template that extends base.html."""

from __future__ import annotations

from flask import Blueprint, render_template

pages = Blueprint("pages", __name__)

#: route -> template; single source for the nav structure.
_PAGES = {
    "/": "index.html",
    "/dashboard": "dashboard.html",
    "/chat": "chat.html",
    "/planner": "planner.html",
    "/analyzer": "analyzer.html",
    "/profile": "profile.html",
    "/knowledge": "knowledge.html",
    "/history": "history.html",
    "/about": "about.html",
}


def _make_view(template: str):
    def view() -> str:
        return render_template(template)

    return view


for rule, template_name in _PAGES.items():
    endpoint = template_name.rsplit(".", 1)[0]
    pages.add_url_rule(rule, endpoint, _make_view(template_name))
