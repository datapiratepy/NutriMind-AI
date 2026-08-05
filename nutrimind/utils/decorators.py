"""Request-scoped middleware and decorators.

* ``init_request_middleware(app)`` — request IDs, access logging, timing.
* ``rate_limit(...)`` — tiny in-memory limiter (single-process dev server);
  applied to token-consuming endpoints such as ``/api/chat``.
"""

from __future__ import annotations

import ipaddress
import logging
import time
import uuid
from collections import defaultdict, deque
from functools import wraps
from typing import Callable, TypeVar

from flask import Flask, g, jsonify, request

logger = logging.getLogger(__name__)

F = TypeVar("F", bound=Callable)

#: endpoint -> client key -> recent call timestamps
_CALL_LOG: dict[str, dict[str, deque]] = defaultdict(lambda: defaultdict(deque))

#: Hard ceiling on tracked clients per endpoint.
#:
#: The map used to grow without any bound. Entries were trimmed only when the
#: *same* client returned, so a caller that arrived once and never came back kept
#: its deque for the life of the process. Measured before this limit existed:
#:
#:     500,000 distinct source addresses -> RSS 58MB -> 436MB  (+379MB)
#:     eviction of idle clients: NONE
#:
#: That is an unauthenticated memory-exhaustion path: /login and /register are
#: public and rate limited, so they populate the map, and a single IPv6 /64
#: supplies 2^64 source addresses. A small VPS is OOM-killed.
#:
#: 20,000 keys is far above any plausible legitimate concurrent client count and
#: costs roughly 15 MB at the measured ~760 bytes per entry.
_MAX_TRACKED_CLIENTS = 20_000

#: How often to sweep expired entries, in seconds of wall clock.
_SWEEP_INTERVAL_S = 60.0
_last_sweep = 0.0


def reset_rate_limits() -> None:
    """Forget every recorded call.

    Process-global state, so without this the test suite shares one bucket
    across every test: roughly 180 sign-ins from ``127.0.0.1`` trip the login
    limiter partway through the run and every later test fails to authenticate.
    Also useful for an operator who has locked themselves out in development.
    """
    global _last_sweep
    _CALL_LOG.clear()
    _last_sweep = 0.0


def rate_limit_client_key(remote_addr: str | None) -> str:
    """Collapse an address to the unit that should share a rate-limit bucket.

    IPv4 is used whole. **IPv6 is grouped by /64**, because that is the smallest
    block routinely assigned to a single subscriber: without grouping, one host
    can present a different source address on every request and never be limited
    at all, while also inflating the tracked-client map.

    Falls back to the raw string for anything unparseable, so a malformed or
    proxied value still gets a bucket rather than being silently exempt.
    """
    if not remote_addr:
        return "unknown"
    try:
        address = ipaddress.ip_address(remote_addr)
    except ValueError:
        return remote_addr
    if address.version == 6:
        return str(ipaddress.ip_network(f"{address}/64", strict=False))
    return str(address)


#: Shared bucket used once an endpoint's table is full. See ``_bucket_for``.
OVERFLOW_KEY = "__overflow__"


def _bucket_for(clients: dict[str, deque], key: str) -> str:
    """Which bucket this client should use, given how full the table already is.

    Known clients always keep their own bucket. A *new* client arriving at a
    full table shares one overflow bucket instead of creating an entry.

    This is O(1) and bounds memory absolutely. The obvious alternative — sweep
    and evict when full — was implemented first and rejected after measuring it:
    when the table is at its ceiling, every request triggers a scan and a sort,
    which turns the limiter itself into the denial of service it was added to
    prevent.

    The trade-off is deliberate and worth stating: while an endpoint's table is
    full, previously unseen clients are limited *collectively*. A legitimate new
    visitor can therefore be refused because of an attacker's traffic. That is
    the correct direction to fail — the process stays up, existing users keep
    their own buckets, and the periodic sweep restores per-client tracking as
    soon as the burst expires.
    """
    if key in clients or len(clients) < _MAX_TRACKED_CLIENTS:
        return key
    return OVERFLOW_KEY


def _sweep(now: float, per_seconds: int) -> None:
    """Drop clients with no calls inside the window.

    Throttled to once every ``_SWEEP_INTERVAL_S``: doing it per request would
    make the limiter O(tracked clients) per call. The ceiling is enforced by
    ``_bucket_for`` on insertion instead, so the table cannot grow past its
    bound between sweeps.
    """
    global _last_sweep
    if now - _last_sweep < _SWEEP_INTERVAL_S:
        return
    _last_sweep = now

    for endpoint, clients in list(_CALL_LOG.items()):
        for key, calls in list(clients.items()):
            while calls and now - calls[0] > per_seconds:
                calls.popleft()
            if not calls:
                del clients[key]
        if not clients:
            del _CALL_LOG[endpoint]


def rate_limit_tracked_clients() -> int:
    """Total tracked client keys across all endpoints (diagnostics and tests)."""
    return sum(len(clients) for clients in _CALL_LOG.values())


def configure_proxy_awareness(app: Flask, hops: int) -> None:
    """Trust ``X-Forwarded-For`` from ``hops`` reverse proxies, or none.

    Behind a reverse proxy ``request.remote_addr`` is the *proxy's* address, so
    every visitor shares one identity. For rate limiting that is not a small
    inaccuracy: the login limiter would count all users together and lock the
    whole site out after ten attempts by anybody.

    Off by default (``hops=0``) because trusting the header when nothing strips
    it is worse than not trusting it at all — a client can then send any
    ``X-Forwarded-For`` it likes and be rate-limited as a different address, or
    appear in logs as one. Set ``TRUSTED_PROXY_HOPS`` to the number of proxies
    that actually sit in front of the app, and only those.
    """
    if hops <= 0:
        return
    from werkzeug.middleware.proxy_fix import ProxyFix

    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=hops, x_proto=hops,
                            x_host=hops, x_prefix=hops)
    logger.info("trusting X-Forwarded-* from %d proxy hop(s)", hops)


def init_request_middleware(app: Flask) -> None:
    """Attach request-ID generation, timing and access logging to the app."""

    @app.before_request
    def _begin_request() -> None:
        g.request_id = uuid.uuid4().hex[:8]
        g.request_started = time.perf_counter()

    @app.after_request
    def _log_request(response):
        if not request.path.startswith("/static"):
            duration_ms = (time.perf_counter() - getattr(g, "request_started", time.perf_counter())) * 1000
            logger.info("%s %s -> %s (%.0fms)", request.method, request.path,
                        response.status_code, duration_ms)
        response.headers["X-Request-ID"] = getattr(g, "request_id", "-")
        return response


def rate_limit(max_calls: int = 20, per_seconds: int = 60) -> Callable[[F], F]:
    """Reject a client IP exceeding ``max_calls`` per ``per_seconds`` window.

    In-memory and per-process — right-sized for a local single-user app;
    swap for Flask-Limiter/Redis if this ever runs multi-process.
    """

    def decorator(func: F) -> F:
        @wraps(func)
        def wrapper(*args, **kwargs):
            now = time.monotonic()
            _sweep(now, per_seconds)
            clients = _CALL_LOG[func.__qualname__]
            client = _bucket_for(clients, rate_limit_client_key(request.remote_addr))
            calls = clients[client]
            while calls and now - calls[0] > per_seconds:
                calls.popleft()
            if len(calls) >= max_calls:
                logger.warning("rate limit hit: %s from %s", func.__qualname__, client)
                response = jsonify(error={
                    "code": "rate_limited",
                    "message": "Too many requests — please slow down.",
                    "hint": f"Limit is {max_calls} requests per {per_seconds}s.",
                })
                response.status_code = 429
                return response
            calls.append(now)
            return func(*args, **kwargs)

        return wrapper  # type: ignore[return-value]

    return decorator
