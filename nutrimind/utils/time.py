"""The single source of "now" for NutriMind AI.

**Convention: every timestamp stored in the database is UTC and naive.**
"Naive" means the ``datetime`` carries no ``tzinfo``; the timezone is UTC by
convention, enforced by everything going through :func:`utcnow`.

Why naive rather than timezone-aware, which is normally the better default:

``DateTime(timezone=True)`` behaves differently on the two dialects this project
supports. Postgres round-trips an aware datetime faithfully. SQLite has no
native timezone support, so SQLAlchemy returns the value **naive** — the
``tzinfo`` is silently dropped. Measured, not assumed:

    SQLite    write aware -> read naive  -> comparing with an aware value
                                            raises TypeError
    Postgres  write aware -> read aware  -> comparison works

Making the columns aware would therefore mean the same expression works in
production and raises in development, which is a worse failure than the
deprecation it set out to fix. SQLite is the zero-setup development default and
Postgres is the production target, so identical semantics on both wins.

The cost is that callers must not mix these values with aware datetimes. That
cost is contained precisely because there is one helper: to move to aware
timestamps later (once SQLite is no longer supported) you change this module
and add one migration, rather than auditing every call site again.

Replaces ``datetime.utcnow()``, which is deprecated from Python 3.12 and emits
a DeprecationWarning on every call under the supported interpreters.
"""

from __future__ import annotations

import datetime as dt


def utcnow() -> dt.datetime:
    """Current UTC time as a naive ``datetime`` (no ``tzinfo``).

    Drop-in replacement for the deprecated ``datetime.datetime.utcnow()``.
    ``datetime.timezone.utc`` is spelled out rather than using the ``datetime.UTC``
    alias so this reads identically on every interpreter in the supported range.
    """
    return dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)


def utctoday() -> dt.date:
    """Today's date in UTC.

    Named explicitly because "today" is ambiguous: this is the UTC calendar day,
    which is what the stored timestamps bucket into. It is deliberately *not*
    the user's local day — presenting local days is tracked separately as audit
    finding M3 and needs a timezone on the user profile to do correctly.
    """
    return utcnow().date()


#: Fallback when a profile has no timezone set, or names one this interpreter
#: does not know. UTC keeps the previous behaviour rather than guessing.
DEFAULT_TIMEZONE = "UTC"


def resolve_zone(name: str | None):
    """An IANA zone object for ``name``, or UTC if it is missing or unknown.

    Never raises. A bad zone string is a data problem, and a dashboard that
    500s because someone's profile says ``"Mars/Olympus"`` is worse than one
    that quietly shows UTC.
    """
    import datetime as _dt
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

    if not name:
        return _dt.timezone.utc
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, KeyError):
        return _dt.timezone.utc


def local_today(zone_name: str | None) -> "dt.date":
    """The calendar date it currently is for someone in ``zone_name``.

    This is the whole timezone fix. Timestamps stay naive UTC in the database;
    only the question "which day is that?" becomes local. ``utcnow()`` is
    naive by convention, so it is attached to UTC before being converted —
    without that step the conversion silently treats it as local time and the
    bug moves rather than disappearing.
    """
    return utcnow().replace(tzinfo=dt.timezone.utc).astimezone(
        resolve_zone(zone_name)).date()
