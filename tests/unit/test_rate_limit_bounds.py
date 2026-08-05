"""The rate limiter's memory must be bounded, and its cost must stay O(1).

The limiter tracks callers in a process-global map. It used to grow without any
bound: entries were trimmed only when the *same* client returned, so a caller
that arrived once and never came back kept its deque for the life of the
process. Measured before this was fixed:

    500,000 distinct source addresses -> RSS 58MB -> 436MB  (+379MB)
    eviction of idle clients: NONE

That is an unauthenticated memory-exhaustion path — ``/login`` and ``/register``
are public and rate limited, so they populate the map, and a single IPv6 /64
supplies 2^64 source addresses.

The existing suite could not see it: the autouse ``_reset_rate_limits`` fixture
clears the map between tests, so accumulation is structurally unobservable.
These tests drive the map directly for that reason.
"""

from __future__ import annotations

import time

import pytest

from nutrimind.utils import decorators as d


@pytest.fixture(autouse=True)
def _clean():
    d.reset_rate_limits()
    yield
    d.reset_rate_limits()


def _record(endpoint: str, address: str, when: float) -> None:
    """Insert exactly the way the decorator does."""
    clients = d._CALL_LOG[endpoint]
    clients[d._bucket_for(clients, d.rate_limit_client_key(address))].append(when)


# -- IPv6 cannot be used to multiply identities -------------------------------

def test_ipv6_addresses_in_one_subnet_share_a_bucket():
    """A /64 is the smallest block routinely given to one subscriber.

    Without grouping, a single host rotates its source address per request and
    is never limited at all — while inflating the tracked-client map on the way
    through.
    """
    first = d.rate_limit_client_key("2001:db8::1")
    second = d.rate_limit_client_key("2001:db8::dead:beef")
    assert first == second == "2001:db8::/64"


def test_ipv4_addresses_are_tracked_individually():
    """Grouping IPv4 would let one household's NAT lock out a neighbour."""
    assert d.rate_limit_client_key("203.0.113.7") == "203.0.113.7"
    assert d.rate_limit_client_key("203.0.113.8") != d.rate_limit_client_key("203.0.113.7")


@pytest.mark.parametrize("value", ["", None, "not-an-address", "::gg"])
def test_unparseable_addresses_still_get_a_bucket(value):
    """Failing open here would exempt a malformed value from limiting entirely."""
    assert d.rate_limit_client_key(value)


# -- the table is bounded -----------------------------------------------------

def test_tracked_clients_never_exceed_the_ceiling():
    """The measurement that mattered: 500k distinct sources -> 20,001 keys.

    20,000 is the cap; the extra one is the shared overflow bucket.
    """
    now = time.monotonic()
    for index in range(d._MAX_TRACKED_CLIENTS + 5_000):
        _record("probe", f"2001:db8:0:{index:x}::1", now)
    assert d.rate_limit_tracked_clients() <= d._MAX_TRACKED_CLIENTS + 1


def test_new_clients_share_the_overflow_bucket_once_full():
    now = time.monotonic()
    for index in range(d._MAX_TRACKED_CLIENTS):
        _record("probe", f"198.51.100.{index // 250}.{index}", now)
    before = len(d._CALL_LOG["probe"])
    _record("probe", "203.0.113.99", now)
    _record("probe", "203.0.113.100", now)
    assert len(d._CALL_LOG["probe"]) <= before + 1
    assert d.OVERFLOW_KEY in d._CALL_LOG["probe"]


def test_a_known_client_keeps_its_own_bucket_when_the_table_is_full():
    """Existing users must not be pushed into the shared bucket by an attack."""
    now = time.monotonic()
    _record("probe", "203.0.113.5", now)
    for index in range(d._MAX_TRACKED_CLIENTS + 100):
        _record("probe", f"2001:db8:1:{index:x}::1", now)
    _record("probe", "203.0.113.5", now)
    assert "203.0.113.5" in d._CALL_LOG["probe"]


# -- idle clients are evicted -------------------------------------------------

def test_idle_clients_are_swept_once_their_window_expires():
    """Before this, an entry survived until its owner returned — i.e. forever."""
    start = time.monotonic()
    for index in range(500):
        _record("probe", f"198.51.100.{index % 256}", start)
    assert d.rate_limit_tracked_clients() > 0

    # Advance past both the window and the sweep interval.
    d._sweep(start + 400, per_seconds=300)
    assert d.rate_limit_tracked_clients() == 0, "idle clients were never evicted"


def test_active_clients_survive_a_sweep():
    """Eviction must not reset someone who is still calling."""
    start = time.monotonic()
    _record("probe", "203.0.113.5", start)
    _record("probe", "203.0.113.5", start + 350)
    d._sweep(start + 400, per_seconds=300)
    assert "203.0.113.5" in d._CALL_LOG.get("probe", {})


def test_sweeping_is_throttled():
    """Sweeping per request makes the limiter O(tracked clients) per call —
    which is the denial of service it exists to prevent."""
    start = time.monotonic()
    _record("probe", "198.51.100.1", start)
    d._sweep(start + 400, per_seconds=300)          # first sweep runs
    _record("probe", "198.51.100.2", start + 401)
    d._sweep(start + 402, per_seconds=300)          # too soon: must be a no-op
    assert "198.51.100.2" in d._CALL_LOG["probe"]


# -- the limiter still limits -------------------------------------------------

def test_the_limiter_still_refuses_over_the_threshold(client):
    """Bounding memory must not have disabled the actual limiting."""
    codes = [client.post("/api/documents", data={}, content_type="multipart/form-data")
             .status_code for _ in range(14)]
    assert 429 in codes, f"the limit no longer fires: {codes}"
