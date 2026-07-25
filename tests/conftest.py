"""Shared pytest fixtures — everything runs in demo mode, no IBM credentials."""

from __future__ import annotations

import pytest

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
        except Exception:  # pragma: no cover - version-dependent, best effort
            continue


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
    yield application
    with application.app_context():
        _db.session.remove()
        _db.drop_all()
        _db.engine.dispose()  # return pooled SQLite connections instead of leaking them
    _release_chroma_clients()


@pytest.fixture()
def client(app):
    return app.test_client()


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
