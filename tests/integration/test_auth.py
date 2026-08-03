"""Registration, sign-in, sign-out, password reset, and the guards around them.

Covers the security properties that are easy to write and easy to lose: that
protected routes reject strangers, that CSRF is actually enforced, that failed
sign-ins cannot be used to discover who has an account, and that changing a
password ends existing sessions.
"""

from __future__ import annotations

import io
import re

import pytest

from nutrimind.extensions import db
from nutrimind.models import User
from nutrimind.services import auth_service
from tests.conftest import make_user

GOOD_PASSWORD = "a-long-enough-passphrase"


def _register(test_client, email="new@example.test", password=GOOD_PASSWORD, **extra):
    return test_client.post("/register", data={
        "email": email, "password": password, **extra})


# ---------------------------------------------------------------------------
# Access control
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("path", [
    "/api/profile", "/api/targets", "/api/meals", "/api/meal-plans",
    "/api/documents", "/api/dashboard/summary", "/api/bmi/history",
    "/api/water/today", "/api/chat/sessions", "/api/system/info",
])
def test_api_endpoints_reject_anonymous_requests(anon_client, path):
    """Default-deny is applied per blueprint, so this list is the proof it holds.

    401 rather than a redirect: the browser client parses these as JSON, and an
    HTML sign-in page would surface as a parse error instead of "signed out".
    """
    response = anon_client.get(path)
    assert response.status_code == 401
    assert response.get_json()["error"]["code"] == "authentication_required"


@pytest.mark.parametrize("path", [
    "/dashboard", "/chat", "/planner", "/analyzer", "/profile", "/knowledge",
    "/history",
])
def test_private_pages_redirect_anonymous_visitors_to_sign_in(anon_client, path):
    response = anon_client.get(path)
    assert response.status_code == 302
    assert "/login" in response.headers["Location"]


@pytest.mark.parametrize("path", ["/", "/about", "/login", "/register",
                                  "/forgot-password", "/api/health"])
def test_public_routes_stay_reachable(anon_client, path):
    """A visitor has to be able to see what this is before signing up, and an
    uptime monitor has no session to present."""
    assert anon_client.get(path).status_code == 200


def test_health_stays_public_but_system_info_does_not(anon_client):
    """Split deliberately: probes cannot authenticate, but the diagnostics
    endpoint reports versions, platform and the configured watsonx region."""
    assert anon_client.get("/api/health").status_code == 200
    assert anon_client.get("/api/system/info").status_code == 401


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

def test_registration_creates_an_account_and_signs_in(anon_client):
    response = _register(anon_client, display_name="New Person")
    assert response.status_code == 302
    assert anon_client.get("/api/profile").status_code == 200


def test_registration_rejects_a_short_password(anon_client):
    assert _register(anon_client, password="short").status_code == 400
    assert anon_client.get("/api/profile").status_code == 401


def test_registration_rejects_a_duplicate_address(app, anon_client):
    with app.app_context():
        make_user(email="taken@example.test")
    assert _register(anon_client, email="taken@example.test").status_code == 400


def test_email_is_stored_and_matched_case_insensitively(app, anon_client):
    """Nobody thinks of their address as case-sensitive, and treating it that way
    would let one person register twice and be unable to sign in reliably."""
    _register(anon_client, email="MiXeD@Example.Test")
    with app.app_context():
        assert User.by_email("mixed@example.test") is not None
        assert User.by_email("MIXED@EXAMPLE.TEST") is not None


@pytest.mark.parametrize("bad", ["", "  ", "no-at-sign", "no@domain", "a b@c.com"])
def test_registration_rejects_malformed_addresses(anon_client, bad):
    assert _register(anon_client, email=bad).status_code == 400


# ---------------------------------------------------------------------------
# Sign-in
# ---------------------------------------------------------------------------

def test_sign_in_and_sign_out(app, anon_client, user):  # noqa: ARG001
    assert anon_client.post("/login", data={
        "email": user["email"], "password": user["password"]}).status_code == 302
    assert anon_client.get("/api/profile").status_code == 200

    assert anon_client.post("/logout").status_code == 302
    assert anon_client.get("/api/profile").status_code == 401


def test_wrong_password_is_rejected(anon_client, user):
    assert anon_client.post("/login", data={
        "email": user["email"], "password": "not-the-password"}).status_code == 401


def test_failed_sign_in_does_not_reveal_whether_the_account_exists(anon_client, user):
    """Different wording for "no such user" and "wrong password" turns the login
    form into a membership oracle: anyone could test an address and learn
    whether its owner uses the service."""
    real = anon_client.post("/login", data={
        "email": user["email"], "password": "wrong-password-entirely"})
    unknown = anon_client.post("/login", data={
        "email": "nobody@example.test", "password": "wrong-password-entirely"})

    assert real.status_code == unknown.status_code
    assert b"incorrect" in real.data.lower() and b"incorrect" in unknown.data.lower()


def test_a_disabled_account_cannot_sign_in(app, anon_client, user):
    with app.app_context():
        account = User.by_email(user["email"])
        account.is_active_flag = False
        db.session.commit()

    assert anon_client.post("/login", data={
        "email": user["email"], "password": user["password"]}).status_code == 401


def test_sign_in_redirect_target_cannot_leave_the_site(anon_client, user):
    """Without this check, /login?next=https://evil.example sends a user who has
    just typed their password to an attacker's page — convincing precisely
    because it follows a real sign-in."""
    response = anon_client.post("/login", data={
        "email": user["email"], "password": user["password"],
        "next": "https://evil.example/steal"})

    assert "evil.example" not in response.headers["Location"]


def test_sign_in_honours_a_safe_redirect_target(anon_client, user):
    response = anon_client.post("/login", data={
        "email": user["email"], "password": user["password"],
        "next": "/knowledge"})
    assert response.headers["Location"].endswith("/knowledge")


def test_repeated_failures_are_rate_limited(anon_client, user):
    """Password guessing is cheap for an attacker and expensive for us: every
    attempt runs a deliberately slow hash."""
    statuses = [
        anon_client.post("/login", data={"email": user["email"],
                                         "password": f"wrong-{attempt}"}).status_code
        for attempt in range(12)
    ]
    assert 429 in statuses, "login is not throttled"


# ---------------------------------------------------------------------------
# CSRF
# ---------------------------------------------------------------------------

def test_state_changing_api_requests_require_a_csrf_token(client):
    """The JSON API used to be exempt wholesale. That was defensible only while
    there was no session for a cross-site request to ride."""
    response = client.post("/api/water", json={"glasses": 1}, no_csrf=True)
    assert response.status_code == 400


def test_state_changing_api_requests_succeed_with_a_token(client):
    assert client.post("/api/water", json={"glasses": 1}).status_code == 200


def test_a_csrf_failure_still_returns_the_json_error_envelope(client):
    """Regression: a rejected request under /api/ must not answer with HTML.

    Flask-WTF raises CSRFError, a plain HTTPException, so it escaped as
    Werkzeug's stock HTML 400 page. The browser client parses every API response
    as JSON and reported "unexpected character at line 1 column 1" — column 1
    being the '<' of '<!doctype html>' — which hid the real cause completely.
    """
    response = client.post("/api/water", json={"glasses": 1}, no_csrf=True)

    assert response.status_code == 400
    assert response.mimetype == "application/json"
    error = response.get_json()["error"]
    assert error["code"] == "csrf_error"
    assert error["message"] and error["hint"]
    assert error["request_id"]


def test_a_csrf_failure_on_a_file_upload_also_returns_json(client):
    """The endpoint that actually broke. Multipart takes a different path through
    Flask-WTF than a JSON body, so it is asserted separately rather than assumed
    to behave the same."""
    pdf = io.BytesIO(b"%PDF-1.4\ntrailer<</Root 1 0 R>>\n%%EOF\n")
    response = client.post(
        "/api/documents",
        data={"file": (pdf, "doc.pdf", "application/pdf")},
        content_type="multipart/form-data", no_csrf=True)

    assert response.status_code == 400
    assert response.mimetype == "application/json"
    assert response.get_json()["error"]["code"] == "csrf_error"


def test_a_csrf_failure_on_a_page_form_returns_html_not_json(anon_client, user):
    """Pages are not the API: a browser form should get a readable page.

    ``no_csrf=True`` because the test client otherwise supplies the token, which
    is what made this class of bug invisible to the suite in the first place.
    """
    response = anon_client.post("/login", data={
        "email": user["email"], "password": user["password"]}, no_csrf=True)

    assert response.status_code == 400
    assert response.mimetype == "text/html"
    assert b"reload" in response.data.lower()


def test_reads_do_not_require_a_token(client):
    assert client.get("/api/profile").status_code == 200


def test_session_cookie_is_hardened(app, anon_client, user):
    """HttpOnly keeps an XSS bug from stealing the session; SameSite is the
    backstop if a CSRF check is ever missed."""
    response = anon_client.post("/login", data={
        "email": user["email"], "password": user["password"]})
    cookie = response.headers.get("Set-Cookie", "")

    assert "HttpOnly" in cookie
    assert "SameSite=Lax" in cookie
    assert app.config["SESSION_COOKIE_SECURE"] is not True  # debug=1 under test


# ---------------------------------------------------------------------------
# Password reset
# ---------------------------------------------------------------------------

def test_reset_token_round_trip(app, user):
    with app.app_context():
        account = User.by_email(user["email"])
        token = auth_service.generate_reset_token(account, "test-secret")
        assert auth_service.consume_reset_token(token, "test-secret").id == account.id


def test_a_reset_token_cannot_be_used_twice(app, user):
    """Single-use without a token table: the current password hash is signed into
    the payload, so the token stops verifying as soon as the password changes —
    including the change it authorised."""
    with app.app_context():
        account = User.by_email(user["email"])
        token = auth_service.generate_reset_token(account, "test-secret")
        auth_service.reset_password(account, "brand-new-passphrase")

        with pytest.raises(Exception, match="expired or has already been used"):
            auth_service.consume_reset_token(token, "test-secret")


def test_a_reset_token_signed_with_another_key_is_rejected(app, user):
    with app.app_context():
        account = User.by_email(user["email"])
        token = auth_service.generate_reset_token(account, "test-secret")
        with pytest.raises(Exception, match="expired or has already been used"):
            auth_service.consume_reset_token(token, "a-different-secret")


@pytest.mark.parametrize("token", ["", "garbage", "a.b.c"])
def test_malformed_reset_tokens_are_rejected(app, token):
    with app.app_context(), pytest.raises(Exception, match="expired or has already"):
        auth_service.consume_reset_token(token, "test-secret")


def test_changing_a_password_signs_out_existing_sessions(app, client, user):
    """Falls out of folding the password hash into the session identity, and is
    what makes a reset useful after someone else has had access."""
    assert client.get("/api/profile").status_code == 200

    with app.app_context():
        auth_service.reset_password(User.by_email(user["email"]), "another-passphrase")

    assert client.get("/api/profile").status_code == 401


def test_forgot_password_does_not_reveal_whether_an_address_exists(anon_client, user):
    known = anon_client.post("/forgot-password", data={"email": user["email"]})
    unknown = anon_client.post("/forgot-password",
                               data={"email": "nobody@example.test"})
    assert known.status_code == unknown.status_code == 302


def _request_reset_token(anon_client, email: str, caplog) -> str:
    """Drive /forgot-password and recover the link from the delivery seam.

    Reads ``caplog.records`` rather than ``caplog.text``: the formatted text
    depends on which handlers and formatters happen to be attached to the root
    logger, which other tests in the session can change. The records themselves
    are stable.
    """
    with caplog.at_level("WARNING", logger="nutrimind.routes.auth"):
        anon_client.post("/forgot-password", data={"email": email})

    for record in caplog.records:
        match = re.search(r"/reset-password/(\S+)", record.getMessage())
        if match:
            return match.group(1)
    raise AssertionError("no reset link was produced")


def test_reset_link_completes_the_flow(app, anon_client, user, caplog):
    """End to end through the routes, including the logged delivery seam that
    stands in for email until SMTP is configured."""
    token = _request_reset_token(anon_client, user["email"], caplog)

    assert anon_client.get(f"/reset-password/{token}").status_code == 200
    assert anon_client.post(f"/reset-password/{token}",
                            data={"password": "yet-another-passphrase"}).status_code == 302

    assert anon_client.post("/login", data={
        "email": user["email"], "password": "yet-another-passphrase"}).status_code == 302


def test_reset_rejects_a_password_that_is_too_short(app, anon_client, user, caplog):
    token = _request_reset_token(anon_client, user["email"], caplog)

    assert anon_client.post(f"/reset-password/{token}",
                            data={"password": "short"}).status_code == 400


# ---------------------------------------------------------------------------
# Accounts with no usable password
# ---------------------------------------------------------------------------

def test_missing_account_error_distinguishes_an_empty_database(app):
    """"No account found" is true but useless when there are no accounts at all.

    That is exactly what a freshly migrated empty database looks like — the
    tenancy migration only creates the placeholder owner when there was data to
    adopt — so the message has to say which situation it is and what to do next.
    """
    from nutrimind.exceptions import NutriMindError
    from scripts.manage_users import _require_user

    with app.app_context():
        with pytest.raises(NutriMindError) as empty:
            _require_user("legacy@nutrimind.invalid")
        assert "no accounts at all" in empty.value.user_message

        make_user(email="real@example.test")
        with pytest.raises(NutriMindError) as wrong:
            _require_user("someone-else@example.test")
        assert "real@example.test" in wrong.value.user_message


def test_an_account_with_no_usable_password_cannot_sign_in(app, anon_client):
    """The tenancy migration adopts pre-existing data into exactly such an
    account. It must own the data without being reachable until claimed."""
    with app.app_context():
        adopted = User(email="legacy@nutrimind.invalid",
                       password_hash="!", display_name="Existing data")
        db.session.add(adopted)
        db.session.commit()

    for attempt in ("", "!", "password", "legacy"):
        assert anon_client.post("/login", data={
            "email": "legacy@nutrimind.invalid", "password": attempt},
        ).status_code == 401
