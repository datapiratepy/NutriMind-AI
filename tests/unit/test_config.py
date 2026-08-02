"""Unit tests for configuration defaults, secret-key resolution and validation.

These cover the two safety defaults that a deploy inherits when nobody sets
anything: debug must be off, and the secret key must never be the placeholder.
"""

from __future__ import annotations

import pytest

from nutrimind.config import (
    _DEFAULT_SECRET,
    _SECRET_KEY_FILENAME,
    load_settings,
    resolve_app_mode,
    validate_settings,
)


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch, tmp_path):
    """Every test gets a clean environment and its own instance directory."""
    for var in ("FLASK_DEBUG", "FLASK_SECRET_KEY", "APP_MODE",
                "WATSONX_APIKEY", "WATSONX_PROJECT_ID"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("NUTRIMIND_INSTANCE_DIR", str(tmp_path / "instance"))
    return tmp_path


# -- debug default -------------------------------------------------------------

def test_debug_is_off_unless_explicitly_enabled():
    """The interactive debugger allows code execution, so it cannot be a default."""
    assert load_settings().debug is False


@pytest.mark.parametrize("value", ["1", "true", "yes", "on"])
def test_debug_can_be_opted_into(monkeypatch, value):
    monkeypatch.setenv("FLASK_DEBUG", value)
    assert load_settings().debug is True


# -- secret key ----------------------------------------------------------------

def test_unset_secret_key_is_generated_not_placeholder(_isolated_env):
    settings = load_settings()
    assert settings.secret_key_source == "generated"
    assert settings.secret_key != _DEFAULT_SECRET
    assert len(settings.secret_key) == 64
    assert (settings.instance_dir / _SECRET_KEY_FILENAME).is_file()


def test_generated_secret_key_is_stable_across_restarts(_isolated_env):
    """Regenerating on every boot would silently log every user out on restart."""
    first = load_settings().secret_key
    assert load_settings().secret_key == first


def test_concurrent_generation_converges_on_one_key(_isolated_env):
    """Workers booting simultaneously must agree, or cookies from one are
    rejected by the others. The loser of the create race adopts the winner's key.
    """
    from nutrimind.config import _resolve_secret_key

    instance_dir = _isolated_env / "instance"
    first, _ = _resolve_secret_key("", instance_dir, allow_generation=True)
    second, _ = _resolve_secret_key("", instance_dir, allow_generation=True)
    assert first == second


def test_explicit_secret_key_wins_over_generation(monkeypatch, _isolated_env):
    monkeypatch.setenv("FLASK_SECRET_KEY", "a" * 64)
    settings = load_settings()
    assert settings.secret_key == "a" * 64
    assert settings.secret_key_source == "environment"
    assert not (settings.instance_dir / _SECRET_KEY_FILENAME).exists()


def test_placeholder_value_is_treated_as_unset(monkeypatch, _isolated_env):
    """.env.example ships the placeholder; copying it must not weaken the key."""
    monkeypatch.setenv("FLASK_SECRET_KEY", _DEFAULT_SECRET)
    settings = load_settings()
    assert settings.secret_key != _DEFAULT_SECRET
    assert settings.secret_key_source == "generated"


def test_zero_setup_still_boots_cleanly(_isolated_env):
    """`git clone && python run.py` must stay viable: warnings are fine, errors are not."""
    errors, _warnings = validate_settings(load_settings())
    assert errors == []


def test_generated_key_warns_so_it_is_not_invisible(_isolated_env):
    _errors, warnings = validate_settings(load_settings())
    assert any("FLASK_SECRET_KEY was not set" in w for w in warnings)


def test_unresolvable_secret_key_is_a_fatal_error(_isolated_env):
    """With generation disabled there is no safe key, so startup must abort."""
    settings = load_settings(ensure_dirs=False)
    assert settings.secret_key_source == "placeholder"
    errors, _warnings = validate_settings(settings)
    assert any("FLASK_SECRET_KEY" in e for e in errors)


# -- mode resolution is unaffected by the above --------------------------------

def test_mode_falls_back_to_demo_without_credentials():
    assert resolve_app_mode(load_settings()) == "demo"
