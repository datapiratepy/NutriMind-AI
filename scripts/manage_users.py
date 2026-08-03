#!/usr/bin/env python3
"""Create and manage accounts from the command line.

Usage::

    python scripts/manage_users.py list
    python scripts/manage_users.py create you@example.com
    python scripts/manage_users.py set-password you@example.com
    python scripts/manage_users.py disable someone@example.com
    python scripts/manage_users.py enable someone@example.com

Passwords are read interactively and never taken as an argument: anything typed
on a command line ends up in shell history and in the process list, where other
users on the machine can read it.

The immediate reason this exists: the tenancy migration adopts pre-existing data
into a placeholder account (``legacy@nutrimind.invalid``) that has no usable
password. ``set-password`` is how you claim it and sign in to data created
before accounts existed.
"""

from __future__ import annotations

import argparse
import getpass
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from nutrimind import create_app  # noqa: E402
from nutrimind.exceptions import NutriMindError  # noqa: E402
from nutrimind.extensions import db  # noqa: E402
from nutrimind.models import User  # noqa: E402
from nutrimind.services import auth_service  # noqa: E402


def _prompt_password() -> str:
    password = getpass.getpass("New password: ")
    if password != getpass.getpass("Confirm password: "):
        raise NutriMindError("Passwords do not match.")
    return auth_service.validate_password(password)


def _require_user(email: str) -> User:
    """Look up an account, failing with something the operator can act on.

    "No account found" is true but unhelpful when the real situation is that the
    database has no accounts at all — which is exactly what a freshly migrated
    empty database looks like, since the tenancy migration only creates the
    placeholder owner when there was data to adopt.
    """
    user = User.by_email(email)
    if user is not None:
        return user

    known = list(db.session.execute(db.select(User.email).order_by(User.id)).scalars())
    if not known:
        raise NutriMindError(
            "This database has no accounts at all.",
            hint="Nothing needed adopting during the migration, so no "
                 "placeholder account was created. Register in the browser, or: "
                 f"python scripts/manage_users.py create {email}")
    raise NutriMindError(
        f"No account found for {email!r}.",
        hint=f"Known accounts: {', '.join(known)}")


def cmd_list(_args) -> int:
    users = list(db.session.execute(db.select(User).order_by(User.id)).scalars())
    if not users:
        print("no accounts yet")
        return 0
    print(f"{'id':>4}  {'email':<40} {'active':<7} {'password':<10} last login")
    for user in users:
        print(f"{user.id:>4}  {user.email:<40} "
              f"{'yes' if user.is_active else 'no':<7} "
              f"{'set' if user.has_usable_password else 'UNSET':<10} "
              f"{user.last_login_at or 'never'}")
    return 0


def cmd_create(args) -> int:
    password = _prompt_password()
    user = auth_service.register(args.email, password, args.name or "")
    print(f"created account {user.email} (id={user.id})")
    return 0


def cmd_set_password(args) -> int:
    user = _require_user(args.email)
    auth_service.reset_password(user, _prompt_password())
    print(f"password updated for {user.email}")
    print("Any existing sessions for this account have been signed out.")
    return 0


def _set_active(email: str, active: bool) -> int:
    user = _require_user(email)
    user.is_active_flag = active
    db.session.commit()
    print(f"{user.email} is now {'enabled' if active else 'disabled'}")
    if not active:
        print("Existing sessions remain valid until they expire; use "
              "set-password to sign the account out immediately.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("list", help="show all accounts")

    create = sub.add_parser("create", help="create an account")
    create.add_argument("email")
    create.add_argument("--name", default="", help="display name")

    for name, help_text in (("set-password", "set or replace a password"),
                            ("disable", "block sign-in"),
                            ("enable", "allow sign-in again")):
        command = sub.add_parser(name, help=help_text)
        command.add_argument("email")

    args = parser.parse_args()
    handlers = {
        "list": cmd_list,
        "create": cmd_create,
        "set-password": cmd_set_password,
        "disable": lambda a: _set_active(a.email, False),
        "enable": lambda a: _set_active(a.email, True),
    }

    app = create_app()
    with app.app_context():
        try:
            return handlers[args.command](args)
        except NutriMindError as exc:
            print(f"error: {exc.user_message}")
            return 1
        except KeyboardInterrupt:
            print("\ncancelled")
            return 1


if __name__ == "__main__":
    raise SystemExit(main())
