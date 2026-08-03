"""Registration, authentication and password-reset logic.

Routes handle HTTP; this module owns the rules. Kept separate so the security
decisions — what counts as a valid password, how reset tokens expire, what an
attacker can learn from a failed login — are reviewable in one place.
"""

from __future__ import annotations

import logging

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from nutrimind.exceptions import ValidationError
from nutrimind.extensions import db
from nutrimind.models import User
from nutrimind.utils.time import utcnow
from nutrimind.utils.validators import sanitize_text

logger = logging.getLogger(__name__)

#: Long enough to resist guessing, short enough that people do not write it down.
#: No composition rules (upper/digit/symbol): they push users toward predictable
#: patterns like "Password1!" and NIST SP 800-63B advises against them. Length
#: plus a slow hash is what actually helps.
PASSWORD_MIN_LENGTH = 10
PASSWORD_MAX_LENGTH = 200  # bounds the work a single request can force

EMAIL_MAX_LENGTH = 255

#: Reset links expire in an hour. Long enough to survive a slow inbox, short
#: enough to limit the window if the message is read by someone else.
RESET_TOKEN_MAX_AGE_SECONDS = 3600
_RESET_SALT = "nutrimind-password-reset"


class AuthError(ValidationError):
    """Authentication failed. Deliberately vague — see :func:`authenticate`."""

    default_message = "Email or password is incorrect."
    error_code = "auth_failed"


def validate_email(raw: str) -> str:
    """Normalise and sanity-check an address.

    Deliberately permissive. Full RFC 5322 validation rejects addresses that
    work, and the only proof an address is real is sending to it — which is the
    job of email verification, not a regex.
    """
    email = User.normalize_email(
        sanitize_text(raw, max_chars=EMAIL_MAX_LENGTH, field="email"))
    local, sep, domain = email.partition("@")
    if not sep or not local or "." not in domain or domain.startswith("."):
        raise ValidationError(f"'{email}' does not look like an email address.")
    if " " in email:
        raise ValidationError("Email addresses cannot contain spaces.")
    return email


def validate_password(raw: str) -> str:
    if not isinstance(raw, str) or not raw:
        raise ValidationError("'password' is required.")
    if len(raw) < PASSWORD_MIN_LENGTH:
        raise ValidationError(
            f"Password must be at least {PASSWORD_MIN_LENGTH} characters.")
    if len(raw) > PASSWORD_MAX_LENGTH:
        raise ValidationError(
            f"Password must be at most {PASSWORD_MAX_LENGTH} characters.")
    return raw


def register(email: str, password: str, display_name: str = "") -> User:
    """Create an account.

    :raises ValidationError: on invalid input or an address already in use.
    """
    email = validate_email(email)
    password = validate_password(password)
    name = sanitize_text(display_name, max_chars=80, field="display_name") \
        if display_name else ""

    if User.by_email(email) is not None:
        # Registration necessarily reveals whether an address is taken — the
        # alternative is silently doing nothing, which strands real users who
        # genuinely forgot they had signed up. The reset flow, where no such
        # trade-off exists, does not leak (see request_password_reset).
        raise ValidationError(
            "That email address is already registered.",
            hint="Sign in instead, or reset your password.")

    user = User(email=email, display_name=name)
    user.set_password(password)
    db.session.add(user)
    db.session.commit()
    logger.info("account created: user_id=%s", user.id)
    return user


def authenticate(email: str, password: str) -> User:
    """Verify credentials and record the login.

    :raises AuthError: for a wrong password, an unknown address, a disabled
        account, or an account with no usable password — all with the same
        message, so the response cannot be used to enumerate who has an account.
    """
    user = User.by_email(email or "")
    if user is None or not user.check_password(password or ""):
        logger.info("failed login for %r", User.normalize_email(email or "")[:80])
        raise AuthError()
    if not user.is_active:
        logger.warning("login attempt on disabled account user_id=%s", user.id)
        raise AuthError()

    user.last_login_at = utcnow()
    db.session.commit()
    logger.info("login ok: user_id=%s", user.id)
    return user


# ---------------------------------------------------------------------------
# Password reset
# ---------------------------------------------------------------------------

def _serializer(secret_key: str) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(secret_key, salt=_RESET_SALT)


def generate_reset_token(user: User, secret_key: str) -> str:
    """Signed, expiring, single-use token for ``user``.

    Single-use without a database table: the current password hash is signed
    into the payload, so the token stops verifying the moment the password
    changes — including the change the token itself authorised. A stolen link
    is therefore useless after first use, and no cleanup job is needed for
    expired rows.
    """
    return _serializer(secret_key).dumps(
        {"uid": user.id, "pw": user.password_hash})


def consume_reset_token(token: str, secret_key: str) -> User:
    """Validate a reset token and return its user.

    :raises ValidationError: expired, tampered with, or already used.
    """
    expired = ValidationError(
        "This password reset link has expired or has already been used.",
        hint="Request a new one.")
    try:
        payload = _serializer(secret_key).loads(
            token or "", max_age=RESET_TOKEN_MAX_AGE_SECONDS)
    except SignatureExpired as exc:
        raise expired from exc
    except (BadSignature, Exception) as exc:  # noqa: BLE001 — any decode failure
        raise expired from exc

    user = db.session.get(User, payload.get("uid"))
    if user is None or user.password_hash != payload.get("pw"):
        raise expired
    return user


def reset_password(user: User, new_password: str) -> None:
    """Set a new password, invalidating existing sessions and reset links.

    Both follow from the hash changing: ``User.get_id`` embeds it, so sessions
    stop matching, and reset tokens sign it, so outstanding links stop verifying.
    """
    validate_password(new_password)
    user.set_password(new_password)
    db.session.commit()
    logger.info("password reset completed: user_id=%s", user.id)
