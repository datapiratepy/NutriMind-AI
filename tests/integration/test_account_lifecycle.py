"""Getting your data out, and getting it deleted.

Deletion is the interesting half. This account's data lives in four places and
only one of them cascades:

    relational rows   ON DELETE CASCADE handles these
    uploaded files    nothing cascades to a filesystem
    Chroma vectors    the collection has no user_id column at all
    the session       must be ended, or the cookie names a user that is gone

The orphaned-vector problem has been documented as theoretical since Milestone 3
precisely because there was no way to delete an account. These tests are what
stop it becoming real now that there is.
"""

from __future__ import annotations

import io

from tests.conftest import sign_in
from tests.integration.test_api_documents import PAGE_ONE, PAGE_TWO, _build_pdf

PASSWORD = "correct-horse-battery"


def _seed(client, app):
    """Give an account a profile, a document with vectors, and a chat message."""
    from nutrimind.services.jobs import get_jobs

    client.put("/api/profile", json={
        "name": "Pat", "age": 41, "gender": "female", "height_cm": 165,
        "weight_kg": 92, "activity_level": "sedentary", "food_preference": "vegetarian",
        "weight_goal": "lose", "medical_conditions": ["type 2 diabetes"],
        "allergies": ["peanuts"], "country": "India"})
    client.post("/api/bmi", json={"height_cm": 165, "weight_kg": 92})
    client.post("/api/water", json={"glasses": 4})
    client.post("/api/documents", data={
        "file": (io.BytesIO(_build_pdf([PAGE_ONE, PAGE_TWO])), "mine.pdf"),
    }, content_type="multipart/form-data")
    assert get_jobs(app).wait_idle(60)
    client.post("/api/chat", json={"message": "how much protein is in paneer?",
                                   "stream": False})


def _vector_count(app, document_id: int) -> int:
    from nutrimind.services.rag_service import get_rag_service

    with app.app_context():
        return get_rag_service(
            app.config["NUTRIMIND_SETTINGS"]).store.count_for_document(document_id)


# -- export -------------------------------------------------------------------

def test_export_contains_every_category_the_account_owns(client, app):
    _seed(client, app)
    response = client.get("/api/account/export")
    assert response.status_code == 200
    assert "attachment" in response.headers["Content-Disposition"]

    payload = response.get_json()
    assert payload["account"]["email"]
    assert payload["profile"]["name"] == "Pat"
    assert payload["bmi_history"] and payload["water_log"]
    assert payload["documents"][0]["filename"] == "mine.pdf"
    assert payload["chat_messages"], "chat history is missing from the export"


def test_export_never_leaks_another_account(client, client_b, app):
    """The export is the most concentrated disclosure in the application."""
    _seed(client, app)
    payload = client_b.get("/api/account/export").get_json()
    assert payload["profile"] is None
    assert payload["documents"] == []
    assert payload["chat_messages"] == []


def test_export_requires_a_session(anon_client):
    assert anon_client.get("/api/account/export").status_code == 401


# -- deletion -----------------------------------------------------------------

def test_deletion_requires_the_current_password(client, app):
    _seed(client, app)
    response = client.delete("/api/account", json={"password": "not-my-password"})
    assert response.status_code == 400
    assert client.get("/api/documents").status_code == 200, "the account was deleted"


def test_deletion_requires_a_session(anon_client):
    assert anon_client.delete("/api/account", json={"password": PASSWORD}).status_code == 401


def test_deletion_removes_rows_files_and_vectors(client, app, user):
    """The whole point: all four places, not just the ones that cascade."""
    _seed(client, app)
    document = client.get("/api/documents").get_json()["documents"][0]
    document_id = document["id"]
    assert _vector_count(app, document_id) > 0, "nothing was indexed to delete"

    uploads = app.config["NUTRIMIND_SETTINGS"].instance_dir / "uploads"
    assert list(uploads.glob("*")), "no file to delete"

    response = client.delete("/api/account", json={"password": PASSWORD})
    assert response.status_code == 200
    assert response.get_json()["documents_removed"] == 1

    assert _vector_count(app, document_id) == 0, "vectors were orphaned in Chroma"
    assert list(uploads.glob("*")) == [], "the uploaded file was left on disk"

    with app.app_context():
        from nutrimind.extensions import db
        from nutrimind.models import BMIRecord, ChatMessage, Document, User

        assert db.session.get(User, user["id"]) is None
        for model in (Document, BMIRecord, ChatMessage):
            remaining = db.session.execute(
                db.select(model).where(model.user_id == user["id"])).scalars().all()
            assert remaining == [], f"{model.__name__} rows survived the deletion"


def test_deletion_ends_the_session(client, app):
    _seed(client, app)
    client.delete("/api/account", json={"password": PASSWORD})
    assert client.get("/api/documents").status_code == 401, (
        "the browser still holds a session for a deleted account")


def test_deleting_one_account_leaves_the_other_intact(client, client_b, app,
                                                      other_user):
    """Cascade scoping, asserted rather than assumed."""
    _seed(client, app)
    _seed(client_b, app)
    b_document = client_b.get("/api/documents").get_json()["documents"][0]

    assert client.delete("/api/account", json={"password": PASSWORD}).status_code == 200

    assert client_b.get("/api/documents").get_json()["documents"][0]["id"] == \
        b_document["id"]
    assert _vector_count(app, b_document["id"]) > 0, (
        "deleting one account destroyed another's vectors")
    result = client_b.get("/api/documents/search",
                          query_string={"q": PAGE_TWO}).get_json()["result"]
    assert result["grounded"] is True, "the surviving account can no longer search"


def test_the_email_can_be_reused_after_deletion(client, app, user):
    """A deleted account must not permanently reserve its address."""
    _seed(client, app)
    client.delete("/api/account", json={"password": PASSWORD})

    fresh = app.test_client()
    token = fresh.csrf_token()
    response = fresh.post("/register", data={
        "email": user["email"], "display_name": "Again",
        "password": PASSWORD, "confirm": PASSWORD},
        headers={"X-CSRFToken": token})
    assert response.status_code in (200, 302)
    sign_in(fresh, {"email": user["email"], "password": PASSWORD})
    assert fresh.get("/api/documents").get_json()["documents"] == []
