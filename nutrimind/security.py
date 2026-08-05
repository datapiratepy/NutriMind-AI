"""Response security headers, and the nonce that makes a strict CSP possible.

Measured on the running application before this existed — every header sent on
a page response, with ``FLASK_DEBUG=0``:

    Content-Length, Content-Type, Set-Cookie, Vary, X-Request-ID

Nothing else. No CSP, no HSTS, no framing policy, no referrer policy. The
application's only defence against XSS in model-rendered markdown was
``DOMPurify``, loaded from a CDN, with nothing behind it if that single layer
were bypassed or the CDN were compromised.

Why a nonce rather than ``'unsafe-inline'``
-------------------------------------------
``base.html`` runs a small inline script before first paint so the saved theme
applies without a flash. ``'unsafe-inline'`` in ``script-src`` would allow that
— and simultaneously allow every injected ``<script>`` the CSP exists to stop,
which makes the header decorative. A per-request nonce allows exactly the
scripts the template emits and nothing else.

Styles are a different case and are handled differently on purpose. There are 65
inline ``style="..."`` attributes across the templates, and **style attributes
cannot carry a nonce** — the mechanism only exists for ``<style>`` elements. The
options were ``'unsafe-inline'`` for styles or rewriting every template. Inline
style is a far weaker vector than inline script (no code execution), so
``style-src`` allows it and ``script-src`` does not. That asymmetry is
deliberate, not an oversight.
"""

from __future__ import annotations

import secrets
from pathlib import Path

from flask import Flask, g, request

#: Where the UI's third-party CSS and JS come from. Narrowed to the exact hosts
#: rather than a wildcard, so a compromise of some other jsdelivr-adjacent
#: domain does not become script execution here.
_CDN = "https://cdn.jsdelivr.net"
_FONTS_CSS = "https://fonts.googleapis.com"
_FONTS_FILES = "https://fonts.gstatic.com"


def _csp(nonce: str, *, vendored: bool) -> str:
    """Build the policy. Tighter when assets are served locally.

    ``scripts/vendor_cdn_assets.py`` copies the third-party files into
    ``static/vendor/``; once that has run, no external origin needs to be
    allowed at all and the policy drops the CDN entirely.
    """
    script = ["'self'", f"'nonce-{nonce}'"]
    style = ["'self'", "'unsafe-inline'"]      # see the module docstring
    font = ["'self'", "data:"]
    if not vendored:
        script.append(_CDN)
        style += [_CDN, _FONTS_CSS]
        font.append(_FONTS_FILES)

    return "; ".join([
        "default-src 'self'",
        f"script-src {' '.join(script)}",
        f"style-src {' '.join(style)}",
        f"font-src {' '.join(font)}",
        # Images may be inlined by the charts; no remote image hosts are used.
        "img-src 'self' data:",
        # The app talks only to itself. This is what stops an injected script
        # from exfiltrating a page's contents to somewhere else.
        "connect-src 'self'",
        # No plugins, no nested browsing contexts, no <base> hijacking.
        "object-src 'none'",
        "base-uri 'self'",
        "frame-ancestors 'none'",
        "form-action 'self'",
        # Belt and braces with the reverse proxy: any stray http:// subresource
        # is upgraded rather than blocked, so mixed content cannot appear.
        "upgrade-insecure-requests",
    ])


def _assets_are_vendored(app: Flask) -> bool:
    """True once ``scripts/vendor_cdn_assets.py`` has copied the assets locally.

    Presence of the directory is the switch, so vendoring is a single command
    with no configuration to keep in step, and deleting the directory reverts
    to the CDN.
    """
    return (Path(app.root_path) / "static" / "vendor" / "purify.min.js").is_file()


def init_security_headers(app: Flask) -> None:
    """Attach a per-request CSP nonce and set the response headers."""

    @app.before_request
    def _make_nonce() -> None:
        # 16 bytes: comfortably beyond guessing within a single response, and
        # regenerated per request so a nonce captured from one page cannot be
        # replayed into another.
        g.csp_nonce = secrets.token_urlsafe(16)

    @app.context_processor
    def _expose_nonce() -> dict:
        return {"csp_nonce": lambda: getattr(g, "csp_nonce", ""),
                "vendored_assets": _assets_are_vendored(app)}

    @app.after_request
    def _apply(response):
        vendored = _assets_are_vendored(app)
        nonce = getattr(g, "csp_nonce", "")

        response.headers.setdefault("Content-Security-Policy",
                                    _csp(nonce, vendored=vendored))
        # Stops a browser from second-guessing Content-Type — the mechanism that
        # turns an uploaded file served as text/plain into executable script.
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        # frame-ancestors above covers modern browsers; this covers the rest.
        response.headers.setdefault("X-Frame-Options", "DENY")
        # Send the origin cross-site, never the full path. Paths here can carry
        # document ids and reset tokens.
        response.headers.setdefault("Referrer-Policy",
                                    "strict-origin-when-cross-origin")
        # The app asks for none of these; denying them means a compromised
        # dependency cannot either.
        response.headers.setdefault(
            "Permissions-Policy",
            "geolocation=(), microphone=(), camera=(), payment=(), usb=(), "
            "magnetometer=(), gyroscope=(), interest-cohort=()")
        # Process isolation: a cross-origin opener cannot reach this window,
        # and this window's resources cannot be embedded elsewhere.
        response.headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
        response.headers.setdefault("Cross-Origin-Resource-Policy", "same-origin")

        # Deliberately NOT setting Cross-Origin-Embedder-Policy. `require-corp`
        # would block the CDN assets outright, and the app needs none of the
        # APIs (SharedArrayBuffer, precise timers) that COEP exists to unlock.
        # It would be cost with no benefit until the assets are vendored.

        # HSTS only over HTTPS. Sent on a plain-HTTP response it is ignored by
        # browsers, and in local development it would pin http://localhost to
        # HTTPS in the developer's browser for a year — a genuinely painful
        # thing to undo.
        if request.is_secure:
            response.headers.setdefault(
                "Strict-Transport-Security",
                "max-age=31536000; includeSubDomains")
        return response
