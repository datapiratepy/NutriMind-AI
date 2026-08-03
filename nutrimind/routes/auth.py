"""Sign up, sign in, sign out and password reset.

Server-rendered forms rather than a JSON API: these are the only pages an
unauthenticated visitor can reach, and a plain form works without JavaScript,
which is one less thing between a user and their account.
"""

from __future__ import annotations

import logging

from flask import (
    Blueprint,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    url_for,
)
from flask_login import current_user, login_required, login_user, logout_user

from nutrimind.exceptions import ValidationError
from nutrimind.models import User
from nutrimind.services import auth_service
from nutrimind.utils.decorators import rate_limit

logger = logging.getLogger(__name__)

auth = Blueprint("auth", __name__)


def _safe_next(target: str | None) -> str:
    """Return ``target`` only if it is a path on this site.

    Without this check, ``/login?next=https://evil.example`` would send a user
    who just typed their password straight to an attacker's page — an open
    redirect, and a convincing one precisely because it follows a real login.
    Only root-relative paths are accepted; ``//host`` is rejected because
    browsers read it as protocol-relative and would leave the site.
    """
    if not target or not target.startswith("/") or target.startswith("//"):
        return url_for("pages.dashboard")
    return target


@auth.get("/login")
def login():
    if current_user.is_authenticated:
        return redirect(url_for("pages.dashboard"))
    return render_template("auth/login.html", next=request.args.get("next", ""))


@auth.post("/login")
# Throttled per IP: password guessing is cheap for an attacker and expensive
# for us, since every attempt runs a deliberately slow hash.
@rate_limit(max_calls=10, per_seconds=300)
def login_post():
    email = request.form.get("email", "")
    password = request.form.get("password", "")
    remember = request.form.get("remember") == "on"

    try:
        user = auth_service.authenticate(email, password)
    except ValidationError as exc:
        flash(exc.message, "error")
        return render_template("auth/login.html", email=email,
                               next=request.form.get("next", "")), 401

    login_user(user, remember=remember)
    return redirect(_safe_next(request.form.get("next")))


@auth.get("/register")
def register():
    if current_user.is_authenticated:
        return redirect(url_for("pages.dashboard"))
    return render_template("auth/register.html")


@auth.post("/register")
@rate_limit(max_calls=5, per_seconds=3600)
def register_post():
    email = request.form.get("email", "")
    password = request.form.get("password", "")
    display_name = request.form.get("display_name", "")

    try:
        user = auth_service.register(email, password, display_name)
    except ValidationError as exc:
        flash(exc.user_message, "error")
        return render_template("auth/register.html", email=email,
                               display_name=display_name), 400

    login_user(user)
    flash("Welcome to NutriMind. Start by filling in your profile.", "success")
    return redirect(url_for("pages.profile"))


@auth.post("/logout")
@login_required
def logout():
    logout_user()
    flash("You have been signed out.", "success")
    return redirect(url_for("auth.login"))


# ---------------------------------------------------------------------------
# Password reset
# ---------------------------------------------------------------------------

@auth.get("/forgot-password")
def forgot_password():
    return render_template("auth/forgot_password.html")


@auth.post("/forgot-password")
@rate_limit(max_calls=5, per_seconds=3600)
def forgot_password_post():
    email = request.form.get("email", "")
    user = User.by_email(email)

    if user is not None and user.is_active:
        token = auth_service.generate_reset_token(
            user, current_app.config["SECRET_KEY"])
        link = url_for("auth.reset_password", token=token, _external=True)
        _deliver_reset_link(user, link)

    # Always the same response, whether or not the address exists. Saying "no
    # such account" here would turn this form into a membership oracle: anyone
    # could test an address and learn whether its owner uses the service.
    flash("If that address has an account, a reset link is on its way.", "success")
    return redirect(url_for("auth.login"))


def _deliver_reset_link(user: User, link: str) -> None:
    """Get the reset link to the user.

    No mail transport is configured yet, so the link is written to the
    application log where an operator can retrieve it. That is honest for a
    single-operator deployment and useless for a public one — wiring SMTP is a
    prerequisite for the public launch milestone, tracked in DEPLOYMENT.md.
    """
    logger.warning(
        "PASSWORD RESET for user_id=%s — no mail transport configured, so the "
        "link is logged here instead: %s", user.id, link)


@auth.get("/reset-password/<token>")
def reset_password(token: str):
    try:
        auth_service.consume_reset_token(token, current_app.config["SECRET_KEY"])
    except ValidationError as exc:
        flash(exc.user_message, "error")
        return redirect(url_for("auth.forgot_password"))
    return render_template("auth/reset_password.html", token=token)


@auth.post("/reset-password/<token>")
@rate_limit(max_calls=10, per_seconds=3600)
def reset_password_post(token: str):
    try:
        user = auth_service.consume_reset_token(
            token, current_app.config["SECRET_KEY"])
        auth_service.reset_password(user, request.form.get("password", ""))
    except ValidationError as exc:
        flash(exc.user_message, "error")
        return render_template("auth/reset_password.html", token=token), 400

    # Not logged in automatically: the new password should be proved to work
    # once, while the user still remembers setting it.
    flash("Password updated. Please sign in.", "success")
    return redirect(url_for("auth.login"))
