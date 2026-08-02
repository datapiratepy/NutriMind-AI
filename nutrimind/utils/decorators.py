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
