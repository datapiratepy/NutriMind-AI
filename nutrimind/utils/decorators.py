"""Request-scoped middleware and decorators.

* ``init_request_middleware(app)`` — request IDs, access logging, timing.
* ``rate_limit(...)`` — tiny in-memory limiter (single-process dev server);
  applied to token-consuming endpoints such as ``/api/chat``.
"""

from __future__ import annotations

import logging
import time
import uuid
from collections import defaultdict, deque
from functools import wraps
from typing import Callable, TypeVar

from flask import Flask, g, jsonify, request

logger = logging.getLogger(__name__)

F = TypeVar("F", bound=Callable)

#: endpoint -> ip -> recent call timestamps
_CALL_LOG: dict[str, dict[str, deque]] = defaultdict(lambda: defaultdict(deque))


def reset_rate_limits() -> None:
    """Forget every recorded call.

    Process-global state, so without this the test suite shares one bucket
    across every test: roughly 180 sign-ins from ``127.0.0.1`` trip the login
    limiter partway through the run and every later test fails to authenticate.
    Also useful for an operator who has locked themselves out in development.
    """
    _CALL_LOG.clear()


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
            client = request.remote_addr or "unknown"
            calls = _CALL_LOG[func.__qualname__][client]
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
