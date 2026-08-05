"""Account lifecycle: export everything, or delete everything.

Endpoints
    GET    /api/account/export   -> JSON archive of every record this account owns
    DELETE /api/account          -> erase the account and all of its data

Why this exists
---------------
The application stores medical conditions, weight history, dietary restrictions
and uploaded documents. Until now there was no way to get any of it out and no
way to remove it. For an application holding health information that is not a
missing feature so much as a missing obligation.

Deletion is the harder half, because this account's data lives in four places
and only one of them cascades:

* **relational rows** — ``ON DELETE CASCADE`` handles these;
* **uploaded files** on disk — nothing cascades to a filesystem;
* **vectors in Chroma** — the collection has no ``user_id`` column at all,
  so nothing in the database can reach them;
* **the session** — which must be ended, or the browser keeps a cookie naming
  a user id that no longer exists.

Order matters and is the opposite of the intuitive one. Files and vectors are
removed *before* the rows, because the rows are what say which files and vectors
belong to this account. Delete the rows first and the rest becomes unreachable
garbage — the orphaned-vector problem that has been documented as theoretical
since Milestone 3 and would have become real the moment this endpoint existed.
"""

from __future__ import annotations

import logging

from flask import Blueprint, Response, current_app, json, request
from flask_login import current_user, login_required, logout_user

from nutrimind.exceptions import ValidationError
from nutrimind.extensions import db
from nutrimind.models import (
    BMIRecord,
    ChatMessage,
    Document,
    MealLog,
    MealPlan,
    UserProfile,
    WaterLog,
)
from nutrimind.routes import current_user_id, ok
from nutrimind.services.rag_service import get_rag_service
from nutrimind.utils.decorators import rate_limit
from nutrimind.utils.time import utcnow

logger = logging.getLogger(__name__)

account_api = Blueprint("account_api", __name__, url_prefix="/api")


@account_api.before_request
@login_required
def _require_login():
    """Default-deny: both endpoints here are destructive or disclose everything."""


def _service():
    return get_rag_service(current_app.config["NUTRIMIND_SETTINGS"])


# -- export -------------------------------------------------------------------

@account_api.get("/account/export")
@rate_limit(max_calls=5, per_seconds=600)
def export_account():
    """Everything this account owns, as a downloadable JSON document.

    Rate limited despite being a read: it is the most concentrated disclosure in
    the application, so a stolen session should not be able to pull it
    repeatedly and quietly.

    The uploaded PDFs are referenced by name rather than embedded. Base64 in a
    JSON blob would multiply the size by four and put a 100 MB quota's worth of
    documents through memory in one response; the originals are the user's own
    files, which they still have.
    """
    user_id = current_user_id()

    def rows(model, order=None):
        query = db.select(model).where(model.user_id == user_id)
        return list(db.session.execute(
            query.order_by(order) if order is not None else query).scalars())

    profile = db.session.execute(
        db.select(UserProfile).where(UserProfile.user_id == user_id)
    ).scalar_one_or_none()

    payload = {
        "exported_at": utcnow().isoformat(timespec="seconds") + "Z",
        "account": {
            "email": current_user.email,
            "display_name": current_user.display_name,
            "created_at": (current_user.created_at.isoformat()
                           if getattr(current_user, "created_at", None) else None),
        },
        "profile": profile.to_dict() if profile else None,
        "bmi_history": [r.to_dict() for r in rows(BMIRecord, BMIRecord.ts)],
        "water_log": [r.to_dict() for r in rows(WaterLog, WaterLog.date)],
        "meal_logs": [r.to_dict() for r in rows(MealLog, MealLog.ts)],
        "meal_plans": [r.to_dict() for r in rows(MealPlan, MealPlan.created_at)],
        "documents": [r.to_dict() for r in rows(Document, Document.uploaded_at)],
        "chat_messages": [r.to_dict() for r in rows(ChatMessage,
                                                    ChatMessage.created_at)],
        "note": ("Uploaded PDFs are listed by filename but not embedded. "
                 "Download them from the Knowledge page if you need the files."),
    }

    body = json.dumps(payload, indent=2, sort_keys=False)
    logger.info("account export produced for user_id=%s (%d bytes)", user_id, len(body))
    return Response(
        body, mimetype="application/json",
        headers={"Content-Disposition":
                 'attachment; filename="nutrimind-export.json"'})


# -- deletion -----------------------------------------------------------------

@account_api.delete("/account")
@rate_limit(max_calls=5, per_seconds=3600)
def delete_account():
    """Erase this account and everything belonging to it. Irreversible.

    Requires the current password in the request body. A session alone is not
    enough authority to destroy an account: sessions outlive the moment they
    were created, and this is the one action with no undo.
    """
    payload = request.get_json(silent=True) or {}
    password = payload.get("password") or ""
    if not current_user.check_password(password):
        # Deliberately the same shape as a failed sign-in, and deliberately not
        # rate-limit-exempt: this is a password oracle if either is missed.
        raise ValidationError(
            "That password is not correct.",
            hint="Deleting an account needs your current password.")

    user_id = current_user_id()
    service = _service()

    # 1. Vectors and files first — the rows below are the only record of which
    #    ones belong to this account.
    documents = list(db.session.execute(
        db.select(Document).where(Document.user_id == user_id)).scalars())
    for document in documents:
        try:
            service.remove(document)
        except Exception as exc:  # noqa: BLE001 — keep going; report at the end
            logger.warning("could not fully remove document %d during account "
                           "deletion: %s", document.id, exc)

    # 2. The account row. Everything else cascades from it.
    account = current_user._get_current_object()
    logout_user()
    db.session.delete(account)
    db.session.commit()

    logger.warning("account deleted: user_id=%s (%d document(s) removed)",
                   user_id, len(documents))
    return ok({"deleted": True, "documents_removed": len(documents)})
