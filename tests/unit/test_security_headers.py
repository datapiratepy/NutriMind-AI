"""Response security headers, and the properties that make them worth having.

Measured before this existed — the complete header set on a page response with
``FLASK_DEBUG=0``:

    Content-Length, Content-Type, Set-Cookie, Vary, X-Request-ID

No CSP, no framing policy, no referrer policy. The only defence against XSS in
model-rendered markdown was DOMPurify, loaded from a CDN, with nothing behind it.
"""

from __future__ import annotations

import re

import pytest

REQUIRED = [
    "Content-Security-Policy",
    "X-Content-Type-Options",
    "X-Frame-Options",
    "Referrer-Policy",
    "Permissions-Policy",
    "Cross-Origin-Opener-Policy",
    "Cross-Origin-Resource-Policy",
]


@pytest.mark.parametrize("header", REQUIRED)
@pytest.mark.parametrize("path", ["/", "/about", "/login"])
def test_pages_carry_the_security_headers(anon_client, path, header):
    assert header in anon_client.get(path).headers, f"{path} is missing {header}"


@pytest.mark.parametrize("header", REQUIRED)
def test_api_responses_carry_them_too(client, header):
    """An API response can still be navigated to directly in a browser."""
    assert header in client.get("/api/documents").headers


# -- the CSP has to actually restrict something -------------------------------

def test_script_src_does_not_allow_unsafe_inline(anon_client):
    """``'unsafe-inline'`` in script-src makes the whole header decorative.

    It would allow the template's own inline script and every injected
    ``<script>`` the policy exists to stop. The inline theme script carries a
    nonce instead.
    """
    policy = anon_client.get("/").headers["Content-Security-Policy"]
    script_src = next(p for p in policy.split(";") if p.strip().startswith("script-src"))
    assert "'unsafe-inline'" not in script_src
    assert "'nonce-" in script_src


def test_csp_pins_the_dangerous_directives(anon_client):
    policy = anon_client.get("/").headers["Content-Security-Policy"]
    for directive in ("object-src 'none'", "frame-ancestors 'none'",
                      "base-uri 'self'", "form-action 'self'",
                      "connect-src 'self'"):
        assert directive in policy, f"missing: {directive}"


def test_every_inline_script_in_the_templates_carries_a_nonce():
    """A new inline script without one is silently dead under this CSP.

    The failure is invisible server-side — the page renders, the script simply
    never executes — so it is worth failing the build instead.

    ``encoding="utf-8"`` is not optional. ``Path.read_text()`` defaults to the
    *platform* encoding, which is UTF-8 on Linux and cp1252 on Windows. Every
    one of these templates contains non-ASCII (em dashes, non-breaking spaces),
    so without it this test raises ``UnicodeDecodeError`` on Windows before it
    checks anything — passing in CI and failing on a developer's machine.
    """
    from pathlib import Path

    templates = Path(__file__).resolve().parents[2] / "nutrimind" / "templates"
    offenders = [
        str(path.relative_to(templates))
        for path in templates.rglob("*.html")
        if re.search(r"<script(?![^>]*\bnonce=)(?![^>]*\bsrc=)[^>]*>",
                     path.read_text(encoding="utf-8"))
    ]
    assert not offenders, (
        f"inline <script> without nonce=\"{{{{ csp_nonce() }}}}\": {offenders}")


def test_the_nonce_changes_between_requests(anon_client):
    """A reused nonce is a nonce an attacker can learn from one page and use."""
    def nonce_of():
        policy = anon_client.get("/").headers["Content-Security-Policy"]
        return re.search(r"'nonce-([^']+)'", policy).group(1)

    assert nonce_of() != nonce_of()


def test_the_rendered_page_uses_the_same_nonce_it_advertises(anon_client):
    """If the header and the markup disagree, the page's own script is blocked."""
    response = anon_client.get("/")
    header_nonce = re.search(
        r"'nonce-([^']+)'", response.headers["Content-Security-Policy"]).group(1)
    assert f'nonce="{header_nonce}"' in response.get_data(as_text=True)


# -- HSTS is conditional on purpose -------------------------------------------

def test_hsts_is_not_sent_over_plain_http(anon_client):
    """Sent over http it is ignored by browsers — and in development it would
    pin http://localhost to HTTPS in the developer's browser for a year."""
    assert "Strict-Transport-Security" not in anon_client.get("/").headers


def test_hsts_is_sent_over_https(anon_client):
    response = anon_client.get("/", base_url="https://nutrimind.example")
    assert "max-age=31536000" in response.headers["Strict-Transport-Security"]
    assert "includeSubDomains" in response.headers["Strict-Transport-Security"]


def test_referrer_policy_does_not_leak_paths(anon_client):
    """Paths here carry document ids and password-reset tokens."""
    assert anon_client.get("/").headers["Referrer-Policy"] == \
        "strict-origin-when-cross-origin"
