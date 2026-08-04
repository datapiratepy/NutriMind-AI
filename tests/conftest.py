"""Shared pytest fixtures — everything runs in demo mode, no IBM credentials."""

from __future__ import annotations

import re

import pytest
from flask.testing import FlaskClient

from nutrimind import create_app
from nutrimind.config import load_settings
from nutrimind.extensions import db as _db

#: IBM credential variables that must never leak into a test run.
_CREDENTIAL_VARS = ("WATSONX_APIKEY", "WATSONX_PROJECT_ID", "WATSONX_SPACE_ID")


@pytest.fixture(autouse=True, scope="session")
def _isolate_from_local_dotenv():
    """Stop a developer's real ``.env`` from leaking into the test session.

    ``load_settings()`` calls ``load_dotenv(..., override=False)``. ``override=False``
    only protects variables that are **present** in ``os.environ`` — a variable removed
    with ``monkeypatch.delenv`` is *absent*, so python-dotenv repopulates it from the
    on-disk ``.env``.

    Consequence before this fixture existed: any test that deleted WATSONX_APIKEY /
    WATSONX_PROJECT_ID to exercise the credential-free path silently got the real
    credentials back, and ``resolve_embedding_provider`` returned the live watsonx
    provider instead of the hash/local fallback the test was written for. The suite
    passed in CI and in a fresh clone (no ``.env``) but failed on a configured
    developer machine — a test-isolation defect, not an application bug.

    Neutralising the ``.env`` read and clearing the credential variables once per
    session makes the suite behave identically everywhere. Production behaviour is
    untouched: only the test session is isolated.
    """
    mp = pytest.MonkeyPatch()
    mp.setattr("nutrimind.config.load_dotenv", lambda *args, **kwargs: False)
    for var in _CREDENTIAL_VARS:
        mp.delenv(var, raising=False)
    yield
    mp.undo()


def _release_chroma_clients() -> None:
    """Close Chroma's cached per-path system clients and their SQLite handles.

    Every test gets its own ``tmp_path``, so Chroma builds a new system per test and
    keeps it in a process-wide cache that nothing ever closes — the source of the
    ResourceWarnings about unclosed database connections. Guarded because this touches
    Chroma internals whose module path has moved between versions; on any mismatch it
    is a harmless no-op.
    """
    for module_path in ("chromadb.api.shared_system_client", "chromadb.api.client"):
        try:
            module = __import__(module_path, fromlist=["SharedSystemClient"])
            module.SharedSystemClient.clear_system_cache()
            return
        except Exception:  # noqa: BLE001 — pragma: no cover; version-dependent, best effort
            continue


@pytest.fixture(autouse=True)
def _reset_rate_limits():
    """Clear the process-global rate-limit state between tests.

    Without this the ~180 sign-ins the suite performs all count against one
    bucket for 127.0.0.1, trip the login limiter partway through, and every
    later test fails in setup with a confusing authentication error.
    """
    from nutrimind.utils.decorators import reset_rate_limits

    reset_rate_limits()
    yield
    reset_rate_limits()


@pytest.fixture()
def app(monkeypatch, tmp_path):
    """A fresh app per test: demo mode, isolated on-disk SQLite in tmp_path."""
    monkeypatch.setenv("APP_MODE", "demo")
    monkeypatch.setenv("DATABASE_URI", f"sqlite:///{(tmp_path / 'test.db').as_posix()}")
    monkeypatch.setenv("FLASK_DEBUG", "1")
    monkeypatch.setenv("NUTRIMIND_INSTANCE_DIR", str(tmp_path / "instance"))
    monkeypatch.delenv("WATSONX_APIKEY", raising=False)
    monkeypatch.delenv("WATSONX_PROJECT_ID", raising=False)

    # Reset process-wide singletons so each test gets isolated stores.
    # The LLM client is cached process-wide too (services/llm/__init__.py), so it must
    # be reset alongside the RAG service — otherwise the first test's client is reused
    # for the entire session regardless of each test's APP_MODE.
    import nutrimind.services.llm as llm_module
    import nutrimind.services.rag_service as rag_module
    rag_module._service = None
    llm_module._client = None

    application = create_app(load_settings(ensure_dirs=True))
    application.test_client_class = CSRFClient

    # create_app no longer builds the schema — the migration scripts own it.
    # Tests use create_all() rather than running the migration chain per test,
    # because that would re-run every revision for each of ~180 tests to produce
    # a schema create_all() derives from the same metadata instantly.
    # The risk that skips — migrations drifting away from the models — is covered
    # directly and once by tests/unit/test_migrations.py.
    with application.app_context():
        _db.create_all()

    yield application

    # Stop the background pool before tearing the database down. Each app builds
    # its own runner, so without this every test leaks its worker threads — 5
    # apps measured 5 surviving threads, and the suite creates hundreds. Shutting
    # down first also means no job can be mid-query when drop_all() runs.
    from nutrimind.services.jobs import EXTENSION_KEY
    from nutrimind.services.runtime import release_runtime

    runner = application.extensions.get(EXTENSION_KEY)
    if runner is not None:
        runner.shutdown(wait=True)

    with application.app_context():
        _db.session.remove()
        _db.drop_all()
        _db.engine.dispose()  # return pooled SQLite connections instead of leaking them
    _release_chroma_clients()
    release_runtime()  # drop the Chroma directory lock and its open file handle


_CSRF_META = re.compile(r'name="csrf-token" content="([^"]+)"')


class CSRFClient(FlaskClient):
    """Test client that supplies CSRF tokens the way the browser does.

    The usual shortcut is ``WTF_CSRF_ENABLED = False`` in test config, which
    quietly turns every CSRF assertion in the suite into a no-op — the protection
    could be removed entirely and nothing would fail. Sending the real token
    instead keeps those tests meaningful, and means a route that forgets to
    accept the header shows up here rather than in a browser.

    The token comes from ``/about``: public, always rendered from base.html, and
    reachable whether or not the client is signed in.
    """

    _UNSAFE = frozenset({"POST", "PUT", "PATCH", "DELETE"})

    def csrf_token(self) -> str:
        match = _CSRF_META.search(super().get("/about").get_data(as_text=True))
        assert match, "no CSRF token in /about — base.html meta tag missing?"
        return match.group(1)

    def open(self, *args, **kwargs):  # noqa: A003 — Werkzeug's API name
        method = (kwargs.get("method") or "GET").upper()
        if method in self._UNSAFE and not kwargs.pop("no_csrf", False):
            headers = dict(kwargs.get("headers") or {})
            headers.setdefault("X-CSRFToken", self.csrf_token())
            kwargs["headers"] = headers
        return super().open(*args, **kwargs)


def make_user(email: str = "user-a@example.test", password: str = "correct-horse-battery",
              name: str = "Test User"):
    """Create an account directly, bypassing the registration route.

    Tests that exercise data ownership should not have to drive a signup form to
    get there; the flows themselves are covered in tests/integration/test_auth.py.
    """
    from nutrimind.models import User

    user = User(email=User.normalize_email(email), display_name=name)
    user.set_password(password)
    _db.session.add(user)
    _db.session.commit()
    return user


@pytest.fixture()
def user(app):
    """The default account. Its id is stable within a test."""
    with app.app_context():
        created = make_user()
        return {"id": created.id, "email": created.email,
                "password": "correct-horse-battery"}


@pytest.fixture()
def other_user(app):
    """A second account, for proving one user cannot reach another's data."""
    with app.app_context():
        created = make_user(email="user-b@example.test", name="Other User")
        return {"id": created.id, "email": created.email,
                "password": "correct-horse-battery"}


def sign_in(test_client, account: dict) -> None:
    """Log a test client in through the real login route.

    Deliberately not Flask-Login's session-injection helper: going through the
    actual form means these tests would notice if authentication itself broke,
    and it gives the client a real session cookie and CSRF token.
    """
    response = test_client.post("/login", data={
        "email": account["email"], "password": account["password"]})
    assert response.status_code in (302, 200), "sign-in failed in test setup"


@pytest.fixture()
def anon_client(app):
    """A client with no session — for testing that endpoints reject strangers."""
    return app.test_client()


@pytest.fixture()
def client(app, user):
    """Signed-in client used by most tests.

    CSRF stays **enabled** here. Disabling it in tests is the common shortcut and
    it silently voids every CSRF assertion in the suite; instead the test client
    sends the token, exactly as the browser does.
    """
    test_client = app.test_client()
    sign_in(test_client, user)
    return test_client


@pytest.fixture()
def client_b(app, other_user):
    """Signed-in client for the second account."""
    test_client = app.test_client()
    sign_in(test_client, other_user)
    return test_client


@pytest.fixture()
def sample_profile() -> dict:
    """A valid full profile payload used across API tests."""
    return {
        "name": "Harsh",
        "age": 21,
        "gender": "male",
        "height_cm": 175,
        "weight_kg": 70,
        "activity_level": "moderate",
        "food_preference": "vegetarian",
        "weight_goal": "maintain",
        "medical_conditions": [],
        "allergies": ["peanuts"],
        "country": "India",
    }
