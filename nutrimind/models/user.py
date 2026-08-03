"""User accounts and password handling.

Every row in every other table belongs to exactly one user. This model is the
root of that ownership graph, so the constraints here are load-bearing:
``email`` is unique and always stored lowercased, and ``id`` is what the seven
``user_id`` foreign keys point at.
"""

from __future__ import annotations

import datetime as dt

from flask_login import UserMixin
from sqlalchemy.orm import Mapped, mapped_column
from werkzeug.security import check_password_hash, generate_password_hash

from nutrimind.extensions import db
from nutrimind.utils.time import utcnow

#: Marks an account that cannot be logged into. Werkzeug hashes always contain
#: "$", so this can never collide with a real hash and check_password_hash is
#: never even reached for it. Used by the accounts created during the tenancy
#: migration, which own data but have no password until someone claims them.
UNUSABLE_PASSWORD = "!"

#: Cost factor for password hashing. scrypt is Werkzeug's default and is memory-
#: hard, which is what makes offline cracking of a leaked database expensive.
PASSWORD_HASH_METHOD = "scrypt"


class User(db.Model, UserMixin):
    """One account."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    # Lowercased on write (see normalize_email) so uniqueness is case-insensitive
    # without needing citext or a functional index, which differ across dialects.
    email: Mapped[str] = mapped_column(db.String(255), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(db.String(255))
    display_name: Mapped[str] = mapped_column(db.String(80), default="")
    is_active_flag: Mapped[bool] = mapped_column("is_active", default=True)
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow)
    last_login_at: Mapped[dt.datetime | None] = mapped_column(default=None)

    # -- password handling ---------------------------------------------------

    @staticmethod
    def normalize_email(email: str) -> str:
        """Canonical form used for both storage and lookup."""
        return (email or "").strip().lower()

    def set_password(self, password: str) -> None:
        self.password_hash = generate_password_hash(
            password, method=PASSWORD_HASH_METHOD)

    def check_password(self, password: str) -> bool:
        """Verify a password. Always False for accounts with no usable password."""
        if not self.password_hash or self.password_hash == UNUSABLE_PASSWORD:
            return False
        return check_password_hash(self.password_hash, password)

    @property
    def has_usable_password(self) -> bool:
        return bool(self.password_hash) and self.password_hash != UNUSABLE_PASSWORD

    # -- Flask-Login contract ------------------------------------------------

    @property
    def is_active(self) -> bool:  # noqa: D401 — Flask-Login property
        """Flask-Login refuses to log in a user whose is_active is False."""
        return bool(self.is_active_flag)

    def get_id(self) -> str:
        """Session identity.

        The password hash is folded in so that changing a password invalidates
        every existing session for that account — the property that makes "log
        out everywhere" fall out of a password reset for free. Flask-Login
        compares this against the stored session value on each request.
        """
        return f"{self.id}:{self.password_hash}"

    @classmethod
    def from_session_id(cls, session_id: str) -> "User | None":
        """Reverse of :meth:`get_id`, rejecting stale sessions."""
        raw_id, _, token = (session_id or "").partition(":")
        if not raw_id.isdigit():
            return None
        user = db.session.get(cls, int(raw_id))
        if user is None or user.password_hash != token:
            return None
        return user

    # -- lookups -------------------------------------------------------------

    @classmethod
    def by_email(cls, email: str) -> "User | None":
        return db.session.execute(
            db.select(cls).where(cls.email == cls.normalize_email(email))
        ).scalar_one_or_none()

    def to_dict(self) -> dict:
        """Safe representation — never includes the hash."""
        return {
            "id": self.id,
            "email": self.email,
            "display_name": self.display_name or "",
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
