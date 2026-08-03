#!/usr/bin/env python3
"""Index PDFs into the NutriMind knowledge base.

Usage:
    python scripts/seed_knowledge_base.py             # every PDF in knowledge_base/
    python scripts/seed_knowledge_base.py --file X.pdf  # one specific PDF

Already-indexed files (same SHA-256) are skipped. Uses whichever embedding
provider the configuration resolves (watsonx / local / hash) — check the
"provider" line in the output.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from nutrimind import apply_migrations, create_app  # noqa: E402
from nutrimind.exceptions import NutriMindError, ValidationError  # noqa: E402
from nutrimind.extensions import db  # noqa: E402
from nutrimind.models import User  # noqa: E402
from nutrimind.services.rag_service import get_rag_service  # noqa: E402


def _resolve_owner(email: str | None) -> User:
    """Account the seeded documents belong to.

    Documents are per-user now, so seeding needs an owner. When exactly one
    account exists the choice is unambiguous and asking would be noise; with
    several, guessing would silently file a knowledge base under the wrong
    person, so --email becomes required.
    """
    if email:
        user = User.by_email(email)
        if user is None:
            raise NutriMindError(f"No account found for {email!r}.")
        return user
    users = list(db.session.execute(db.select(User)).scalars())
    if len(users) == 1:
        return users[0]
    if not users:
        raise NutriMindError(
            "No accounts exist yet. Create one first: "
            "python scripts/manage_users.py create you@example.com")
    raise NutriMindError(
        f"{len(users)} accounts exist — pass --email to choose an owner.")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--file", type=Path, default=None,
                        help="index one PDF instead of the knowledge_base/ folder")
    parser.add_argument("--email", default=None,
                        help="account that will own the documents; optional "
                             "when exactly one account exists")
    args = parser.parse_args()

    app = create_app()
    # The schema is owned by the migration scripts, so this may be the first
    # thing to touch a brand-new database — seeding before `python run.py` is a
    # normal order of operations on a fresh clone.
    apply_migrations(app)
    with app.app_context():
        try:
            owner = _resolve_owner(args.email)
        except NutriMindError as exc:
            print(f"error: {exc.user_message}")
            return 1
        print(f"owner: {owner.email}")
        service = get_rag_service(app.config["NUTRIMIND_SETTINGS"])
        print(f"provider: {service.provider.name} "
              f"(collection {service.store.collection_name})")

        if args.file:
            targets = [args.file.resolve()]
        else:
            kb_dir = app.config["NUTRIMIND_SETTINGS"].knowledge_base_dir
            targets = sorted(kb_dir.glob("*.pdf"))
            if not targets:
                print(f"no PDFs found in {kb_dir} — add nutrition PDFs and rerun")
                return 0

        failures = 0
        for path in targets:
            try:
                document = service.ingest_path(path, owner.id)
                print(f"  indexed  {path.name}: {document.pages} pages -> "
                      f"{document.chunk_count} chunks")
            except ValidationError as exc:
                print(f"  skipped  {path.name}: {exc.message}")
            except NutriMindError as exc:
                failures += 1
                print(f"  FAILED   {path.name}: {exc.user_message}")
        print(f"total chunks in collection: {service.store.count()}")
        return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
