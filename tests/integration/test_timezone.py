"""Daily aggregates must bucket by the user's day, not the UTC day.

Before this, ``today`` was ``utctoday()`` everywhere. For a user in India that
means the dashboard resets at 05:30 local, and anything logged after 17:30 local
counts towards tomorrow. For an application whose core loop is daily tracking,
the day boundary being wrong is a correctness bug rather than a preference.

Storage is unchanged: timestamps stay naive UTC (see nutrimind/utils/time.py).
Only the question "which calendar day is that?" became local.
"""

from __future__ import annotations

import datetime as dt

import pytest

from nutrimind.utils.time import local_today, resolve_zone

PROFILE = {
    "name": "Pat", "age": 30, "gender": "female", "height_cm": 165,
    "weight_kg": 62, "activity_level": "moderate", "food_preference": "vegetarian",
    "weight_goal": "maintain", "medical_conditions": [], "allergies": [],
    "country": "India",
}


# -- the helper ----------------------------------------------------------------

def test_local_today_differs_from_utc_across_the_boundary(monkeypatch):
    """The concrete bug: 22:00 UTC is already tomorrow in Kolkata."""
    import nutrimind.utils.time as time_module

    monkeypatch.setattr(time_module, "utcnow",
                        lambda: dt.datetime(2026, 3, 14, 22, 0, 0))
    assert local_today(None) == dt.date(2026, 3, 14)              # UTC
    assert local_today("Asia/Kolkata") == dt.date(2026, 3, 15)    # +05:30
    assert local_today("America/Los_Angeles") == dt.date(2026, 3, 14)


def test_an_unset_timezone_behaves_exactly_as_before(monkeypatch):
    """NULL must mean UTC, or the migration would silently move every existing
    user's day boundary."""
    import nutrimind.utils.time as time_module

    monkeypatch.setattr(time_module, "utcnow",
                        lambda: dt.datetime(2026, 3, 14, 22, 0, 0))
    assert local_today(None) == local_today("UTC") == dt.date(2026, 3, 14)


@pytest.mark.parametrize("bad", ["Mars/Olympus", "not a zone", "", None, "UTC+5"])
def test_an_unknown_zone_falls_back_instead_of_raising(bad):
    """A dashboard that 500s because a profile holds a bad string is worse than
    one that quietly shows UTC."""
    assert resolve_zone(bad) is not None
    assert local_today(bad) is not None


# -- the API accepts and enforces it ------------------------------------------

def test_a_valid_timezone_is_stored_and_returned(client):
    response = client.put("/api/profile", json={**PROFILE,
                                                "timezone": "Asia/Kolkata"})
    assert response.status_code in (200, 201)
    assert client.get("/api/profile").get_json()["profile"]["timezone"] == \
        "Asia/Kolkata"


def test_an_invalid_timezone_is_rejected(client):
    response = client.put("/api/profile", json={**PROFILE, "timezone": "Mars/Olympus"})
    assert response.status_code == 400
    error = response.get_json()["error"]
    assert "not a known time zone" in error["message"]
    assert "Asia/Kolkata" in error["hint"], "the error should show a valid example"


def test_the_timezone_can_be_cleared(client):
    client.put("/api/profile", json={**PROFILE, "timezone": "Asia/Kolkata"})
    client.put("/api/profile", json={**PROFILE, "timezone": None})
    assert client.get("/api/profile").get_json()["profile"]["timezone"] is None


# -- and the aggregates actually move -----------------------------------------

def test_the_dashboard_day_follows_the_profile_timezone(client, monkeypatch):
    """End to end: the same instant is a different 'today' in two zones."""
    import nutrimind.utils.time as time_module

    monkeypatch.setattr(time_module, "utcnow",
                        lambda: dt.datetime(2026, 3, 14, 22, 0, 0))

    client.put("/api/profile", json={**PROFILE, "timezone": "UTC"})
    utc_day = client.get("/api/water/today").get_json()["water"]["date"]

    client.put("/api/profile", json={**PROFILE, "timezone": "Asia/Kolkata"})
    kolkata_day = client.get("/api/water/today").get_json()["water"]["date"]

    assert utc_day == "2026-03-14"
    assert kolkata_day == "2026-03-15", (
        "the water log is still bucketed by the UTC day")


def test_water_logged_in_the_evening_stays_on_the_local_day(client, monkeypatch):
    """The user-visible symptom: an evening entry jumping to tomorrow."""
    import nutrimind.utils.time as time_module

    monkeypatch.setattr(time_module, "utcnow",
                        lambda: dt.datetime(2026, 3, 14, 19, 0, 0))  # 00:30 IST
    client.put("/api/profile", json={**PROFILE, "timezone": "Asia/Kolkata"})
    client.post("/api/water", json={"glasses": 6})

    water = client.get("/api/water/today").get_json()["water"]
    assert water["glasses"] == 6, "the entry landed on a different day than the read"
