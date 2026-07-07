"""Shared pytest fixtures — everything runs in demo mode, no IBM credentials."""

from __future__ import annotations

import pytest

from nutrimind import create_app
from nutrimind.config import load_settings
from nutrimind.extensions import db as _db


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
    import nutrimind.services.rag_service as rag_module
    rag_module._service = None

    application = create_app(load_settings(ensure_dirs=True))
    yield application
    with application.app_context():
        _db.session.remove()
        _db.drop_all()


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
