"""What the browser is actually told to store.

These assert the real ``Set-Cookie`` headers rather than the config dictionary.
The bug that prompted them was invisible in config: ``SESSION_COOKIE_*`` was set
correctly and looked fine, while Flask-Login's separate ``REMEMBER_COOKIE_*``
keys were left unset and silently took their own defaults — ``Secure=False``,
``SameSite=None``, and a 365-day lifetime. Measured before the fix:

    remember_token -> Expires=+365d, HttpOnly, Path=/     (no Secure, no SameSite)
    session        -> Secure, HttpOnly, Path=/, SameSite=Lax

So the longest-lived credential the application issues was the least protected
one, and it overrode the documented ``SESSION_DAYS`` policy by a factor of 26.
Reading config would not have caught it; reading the header does.
"""

from __future__ import annotations

import datetime as dt
import email.utils as eut
import re

import pytest

from nutrimind import create_app
from nutrimind.config import load_settings
from nutrimind.extensions import db

PASSWORD = "correct-horse-battery"


@pytest.fixture()
def production_app(monkeypatch, tmp_path):
    """An app built the way production builds it: debug off, real secret key."""
    monkeypatch.setenv("APP_MODE", "demo")
    monkeypatch.setenv("FLASK_DEBUG", "0")
    monkeypatch.setenv("SESSION_DAYS", "14")
    monkeypatch.setenv("FLASK_SECRET_KEY", "a-real-secret-key-for-tests-only-32b")
    monkeypatch.setenv("NUTRIMIND_INSTANCE_DIR", str(tmp_path / "instance"))
    monkeypatch.setenv("DATABASE_URI", f"sqlite:///{(tmp_path / 'c.db').as_posix()}")
    monkeypatch.delenv("WATSONX_APIKEY", raising=False)
    monkeypatch.delenv("WATSONX_PROJECT_ID", raising=False)

    import nutrimind.services.llm as llm_module
    import nutrimind.services.rag_service as rag_module
    rag_module._service = None
    llm_module._client = None

    application = create_app(load_settings(ensure_dirs=True))
    with application.app_context():
        db.create_all()
        from nutrimind.models import User

        user = User(email="cookie@example.test", display_name="Cookie")
        user.set_password(PASSWORD)
        db.session.add(user)
        db.session.commit()
    yield application

    from nutrimind.services.jobs import EXTENSION_KEY
    from nutrimind.services.runtime import release_runtime

    runner = application.extensions.get(EXTENSION_KEY)
    if runner is not None:
        runner.shutdown(wait=True)
    with application.app_context():
        db.session.remove()
        db.drop_all()
        db.engine.dispose()
    release_runtime()


def _sign_in(app, *, remember: bool):
    client = app.test_client()
    page = client.get("/about").get_data(as_text=True)
    token = re.search(r'name="csrf-token" content="([^"]+)"', page).group(1)
    data = {"email": "cookie@example.test", "password": PASSWORD}
    if remember:
        data["remember"] = "on"
    response = client.post("/login", data=data, headers={"X-CSRFToken": token})
    return {name: header for header in response.headers.getlist("Set-Cookie")
            for name in [header.split("=", 1)[0]]}


def _attrs(header: str) -> set[str]:
    return {part.strip().split("=")[0].lower() for part in header.split(";")[1:]}


# -- both cookies, same protection --------------------------------------------

@pytest.mark.parametrize("cookie", ["session", "remember_token"])
def test_credential_cookies_are_secure_httponly_and_samesite(production_app, cookie):
    """A credential the browser stores must not travel over plain HTTP.

    Parametrised deliberately: the session cookie always passed this. Only
    running the same assertions against remember_token revealed that the two
    were configured independently and had drifted apart.
    """
    cookies = _sign_in(production_app, remember=True)
    assert cookie in cookies, f"{cookie} was not issued"
    attributes = _attrs(cookies[cookie])
    assert "secure" in attributes, f"{cookie} is sent over plain HTTP"
    assert "httponly" in attributes, f"{cookie} is readable from JavaScript"
    assert "samesite" in attributes, f"{cookie} is sent on cross-site requests"


def test_remember_cookie_lifetime_matches_the_session_policy(production_app):
    """SESSION_DAYS is the documented policy; remember-me must not override it.

    Flask-Login's default is 365 days. With SESSION_DAYS=14 that made the
    documentation wrong by a factor of 26 — and the wrong direction, since the
    longer-lived cookie was the weaker one.
    """
    cookies = _sign_in(production_app, remember=True)
    expires = re.search(r"Expires=([^;]+)", cookies["remember_token"]).group(1)
    lifetime = eut.parsedate_to_datetime(expires) - dt.datetime.now(dt.timezone.utc)
    assert lifetime < dt.timedelta(days=15), (
        f"remember_token lives {lifetime.days} days but SESSION_DAYS is 14")
    assert lifetime > dt.timedelta(days=13), "remember-me is now shorter than the session"


def test_no_remember_cookie_when_the_box_is_unticked(production_app):
    """Opting out must actually opt out."""
    cookies = _sign_in(production_app, remember=False)
    assert "session" in cookies
    remember = cookies.get("remember_token", "")
    # Flask-Login emits a deletion cookie on every login; a *live* token would
    # carry a future expiry, so an empty value is the correct outcome here.
    assert remember == "" or remember.startswith("remember_token=;"), (
        "a remember-me cookie was issued despite the box being unticked")


# -- development must stay usable over http -----------------------------------

def test_debug_mode_does_not_force_secure_cookies(monkeypatch, tmp_path):
    """Secure cookies over http would make local development impossible.

    Both cookies are tied to debug rather than hard-coded, so this pins the
    other side of that decision.
    """
    monkeypatch.setenv("APP_MODE", "demo")
    monkeypatch.setenv("FLASK_DEBUG", "1")
    monkeypatch.setenv("NUTRIMIND_INSTANCE_DIR", str(tmp_path / "instance"))
    monkeypatch.setenv("DATABASE_URI", f"sqlite:///{(tmp_path / 'd.db').as_posix()}")
    application = create_app(load_settings(ensure_dirs=True))
    assert application.config["SESSION_COOKIE_SECURE"] is False
    assert application.config["REMEMBER_COOKIE_SECURE"] is False

    from nutrimind.services.jobs import EXTENSION_KEY
    from nutrimind.services.runtime import release_runtime

    runner = application.extensions.get(EXTENSION_KEY)
    if runner is not None:
        runner.shutdown(wait=True)
    release_runtime()
