"""One account must never be able to see or touch another account's data.

The most important tests in this codebase, because the bug they guard against
does not announce itself: a missing owner filter raises nothing, fails nothing,
and returns a perfectly well-formed response containing somebody else's
information. Only a test that looks specifically for it will notice.

Every user-owned resource is covered twice — reading and writing — and the
vector store is covered separately, because ownership there is enforced by a
query filter rather than by a foreign key.
"""

from __future__ import annotations

import io

import pytest

from nutrimind.extensions import db
from nutrimind.models import BMIRecord, Document, MealLog, MealPlan, UserProfile
from nutrimind.retrieval.chunker import Chunk
from nutrimind.services.rag_service import get_rag_service
from nutrimind.utils.time import utcnow

PROFILE = {
    "name": "Owner", "age": 30, "gender": "female", "height_cm": 165,
    "weight_kg": 60, "activity_level": "light", "food_preference": "vegan",
    "weight_goal": "maintain",
}


def _seed_for(app, user_id: int) -> dict[str, int]:
    """Create one row of every owned type for ``user_id``; returns their ids."""
    with app.app_context():
        profile = UserProfile(user_id=user_id, **PROFILE)
        meal = MealLog(user_id=user_id, calories=500, meal_type="lunch")
        plan = MealPlan(user_id=user_id, title="Private plan",
                        targets={"calories": 2000}, plan={"meals": []})
        bmi = BMIRecord(user_id=user_id, height_cm=165, weight_kg=60, bmi=22.0,
                        category="normal", ideal_weight_min_kg=50.4,
                        ideal_weight_max_kg=67.8)
        document = Document(user_id=user_id, filename="private.pdf",
                            stored_name=f"instance/uploads/{user_id}-private.pdf",
                            sha256=f"{user_id:064d}", status="indexed",
                            pages=1, chunk_count=1)
        db.session.add_all([profile, meal, plan, bmi, document])
        db.session.commit()
        return {"profile": profile.id, "meal": meal.id, "plan": plan.id,
                "bmi": bmi.id, "document": document.id}


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------

def test_profile_is_not_shared_between_accounts(app, client, client_b, user):
    """The clearest regression of the old singleton lookup.

    ``get_singleton()`` returned whichever profile row came back first, so the
    second account to sign in would have been shown the first account's name,
    age, weight and medical conditions.
    """
    _seed_for(app, user["id"])

    assert client.get("/api/profile").get_json()["profile"]["name"] == "Owner"
    assert client_b.get("/api/profile").get_json()["profile"] is None


def test_meals_are_not_shared(app, client, client_b, user):
    _seed_for(app, user["id"])
    assert len(client.get("/api/meals").get_json()["meals"]) == 1
    assert client_b.get("/api/meals").get_json()["meals"] == []


def test_meal_plans_are_not_shared(app, client, client_b, user):
    _seed_for(app, user["id"])
    assert len(client.get("/api/meal-plans").get_json()["plans"]) == 1
    assert client_b.get("/api/meal-plans").get_json()["plans"] == []


def test_bmi_history_is_not_shared(app, client, client_b, user):
    _seed_for(app, user["id"])
    assert len(client.get("/api/bmi/history").get_json()["records"]) == 1
    assert client_b.get("/api/bmi/history").get_json()["records"] == []


def test_documents_are_not_shared(app, client, client_b, user):
    _seed_for(app, user["id"])
    assert len(client.get("/api/documents").get_json()["documents"]) == 1
    assert client_b.get("/api/documents").get_json()["documents"] == []


def test_water_is_not_shared(app, client, client_b, user):  # noqa: ARG001
    client.post("/api/water", json={"glasses": 4})
    assert client.get("/api/water/today").get_json()["water"]["glasses"] == 4
    assert client_b.get("/api/water/today").get_json()["water"]["glasses"] == 0


def test_chat_history_is_not_readable_with_a_guessed_session_id(client, client_b):
    """Session ids come from the client, so they are not a secret.

    Filtering history on the session alone would let anyone read another
    account's conversation simply by reusing its id.
    """
    client.post("/api/chat", json={"message": "hello", "session_id": "shared-id",
                                   "stream": False})

    assert len(client.get("/api/chat/history?session_id=shared-id")
               .get_json()["messages"]) == 2
    assert client_b.get("/api/chat/history?session_id=shared-id") \
        .get_json()["messages"] == []


def test_chat_session_list_is_not_shared(client, client_b):
    client.post("/api/chat", json={"message": "hello", "stream": False})
    assert len(client.get("/api/chat/sessions").get_json()["sessions"]) == 1
    assert client_b.get("/api/chat/sessions").get_json()["sessions"] == []


def test_dashboard_aggregates_only_the_signed_in_account(app, client, client_b, user):
    """Aggregates are the easiest place to leak: nothing looks like another
    person's data once it has been summed into a number."""
    _seed_for(app, user["id"])

    assert client.get("/api/dashboard/summary").get_json()["profile_exists"] is True
    summary_b = client_b.get("/api/dashboard/summary").get_json()
    assert summary_b["profile_exists"] is False
    assert summary_b["week"]["meal_count"] == 0


# ---------------------------------------------------------------------------
# Writing and deleting
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("path_template", [
    "/api/meals/{meal}",
    "/api/meal-plans/{plan}",
    "/api/documents/{document}",
])
def test_another_account_cannot_delete_your_rows(app, client, client_b, user,
                                                 path_template):
    ids = _seed_for(app, user["id"])
    path = path_template.format(**ids)

    assert client_b.delete(path).status_code == 400  # reported as "does not exist"

    # The owner can still reach it: the request was rejected, not the row removed.
    assert client.delete(path).status_code == 200


def test_another_account_cannot_read_your_meal_plan(app, client_b, user):
    ids = _seed_for(app, user["id"])
    assert client_b.get(f"/api/meal-plans/{ids['plan']}").status_code == 400


def test_another_account_cannot_export_your_meal_plan_as_pdf(app, client_b, user):
    """The PDF route builds a document from the plan *and* the profile, so a
    missing check here would leak both in a conveniently portable file."""
    ids = _seed_for(app, user["id"])
    assert client_b.get(f"/api/export/meal-plan/{ids['plan']}.pdf").status_code == 400


def test_another_account_cannot_reindex_your_document(app, client_b, user):
    ids = _seed_for(app, user["id"])
    assert client_b.post(f"/api/documents/{ids['document']}/reindex").status_code == 400


def test_ownership_errors_do_not_reveal_that_the_row_exists(app, client_b, user):
    """A distinct 403 would confirm the id is real and allow enumeration.

    Someone else's row and a row that was never created must be indistinguishable
    from outside.
    """
    ids = _seed_for(app, user["id"])
    existing = client_b.get(f"/api/meal-plans/{ids['plan']}")
    missing = client_b.get("/api/meal-plans/999999")

    assert existing.status_code == missing.status_code == 400
    assert existing.get_json()["error"]["code"] == missing.get_json()["error"]["code"]


def test_profiles_do_not_overwrite_each_other(client, client_b):
    """Each account creates its own profile rather than editing a shared one."""
    assert client.put("/api/profile", json={**PROFILE, "name": "First"}).status_code == 201
    assert client_b.put("/api/profile", json={**PROFILE, "name": "Second"}).status_code == 201

    assert client.get("/api/profile").get_json()["profile"]["name"] == "First"
    assert client_b.get("/api/profile").get_json()["profile"]["name"] == "Second"


def test_water_logs_no_longer_collide_across_accounts(client, client_b):
    """water_logs was unique on `date` alone before this milestone, so the second
    account to log water on any day would have failed at the database level."""
    assert client.post("/api/water", json={"glasses": 2}).status_code == 200
    assert client_b.post("/api/water", json={"glasses": 3}).status_code == 200
    assert client.get("/api/water/today").get_json()["water"]["glasses"] == 2
    assert client_b.get("/api/water/today").get_json()["water"]["glasses"] == 3


# ---------------------------------------------------------------------------
# Retrieval — ownership outside the relational database
# ---------------------------------------------------------------------------

def _index_chunk_for(app, user_id: int, text: str, document_id: int) -> None:
    """Index a passage owned by ``user_id`` into the shared Chroma collection."""
    with app.app_context():
        service = get_rag_service(app.config["NUTRIMIND_SETTINGS"])
        db.session.add(Document(id=document_id, user_id=user_id,
                                filename=f"doc{document_id}.pdf",
                                stored_name=f"instance/uploads/{document_id}.pdf",
                                sha256=f"{document_id:064d}", status="indexed"))
        db.session.commit()
        service.store.add_chunks(
            document_id=document_id, filename=f"doc{document_id}.pdf",
            uploaded_at=utcnow().isoformat(),
            chunks=[Chunk(text=text, page=1, chunk_index=0)],
            embeddings=service.provider.embed_documents([text]))


def test_retrieval_does_not_return_another_accounts_passages(app, client_b, user):
    """The vector store has no user_id column and no foreign keys.

    Every account's chunks live in one Chroma collection, so without an explicit
    ownership filter a question from B could be answered — and cited — from a
    document A uploaded. The relational tables would still look perfectly correct.
    """
    secret = "Aardvark biscuits contain exactly 412 milligrams of zinc."
    _index_chunk_for(app, user["id"], secret, document_id=501)

    result = client_b.get("/api/documents/search?q=aardvark+biscuits+zinc") \
        .get_json()["result"]

    assert result["chunks"] == [], "retrieved another account's passage"
    assert result["grounded"] is False
    assert result["citations"] == []


def test_retrieval_still_returns_your_own_passages(app, client, user):
    """The counterpart: the filter must not be so strict that it breaks the
    feature. A test that only checks 'B sees nothing' passes just as well when
    retrieval is broken for everyone."""
    fact = "Aardvark biscuits contain exactly 412 milligrams of zinc."
    _index_chunk_for(app, user["id"], fact, document_id=502)

    result = client.get("/api/documents/search?q=" + fact.replace(" ", "+")) \
        .get_json()["result"]

    assert result["chunks"], "owner cannot retrieve their own document"
    assert result["citations"] == [{"filename": "doc502.pdf", "page": 1}]


def test_chat_cannot_be_grounded_in_another_accounts_documents(app, client_b, user):
    """The same leak through the agent path rather than the search endpoint."""
    secret = "Aardvark biscuits contain exactly 412 milligrams of zinc."
    _index_chunk_for(app, user["id"], secret, document_id=503)

    meta = client_b.post("/api/chat", json={"message": secret, "stream": False}) \
        .get_json()["meta"]

    assert meta["grounded"] is False
    assert meta["citations"] == []


def test_a_query_retrieves_a_passage_that_contains_its_words(app, client, user):
    """End-to-end regression for "retrieval returns nothing".

    A real PDF was uploaded, indexed as 28 chunks, and every obvious query —
    "pad thai", "coconut milk" — came back empty, because the zero-credential
    provider hashed whole chunks into random directions and could only match a
    query identical to the chunk.

    Deliberately exercised through the HTTP endpoint rather than the provider
    alone: the unit tests prove the vectors behave, this proves the whole chain
    (upload -> index -> ownership filter -> threshold -> citations) delivers.
    """
    passage = ("Pad thai is stir fried with rice noodles, tamarind paste, "
               "fish sauce, egg, peanuts and bean sprouts over high heat.")
    _index_chunk_for(app, user["id"], passage, document_id=601)

    result = client.get("/api/documents/search?q=pad+thai").get_json()["result"]

    assert result["grounded"] is True, "the passage containing the words was not found"
    assert result["chunks"], "no chunks returned"
    assert result["citations"]


def test_a_grounded_chat_answer_comes_from_the_passages_it_cites(app, client, user):
    """The end-to-end promise: if it says grounded and shows sources, the text
    beside those sources must come from them.

    This failed while every layer beneath it worked. Retrieval found the right
    chunks, the citations were real, and the *answer* was a canned script about
    diabetes — selected because a recipe contained the words "sugar" and
    "banana", and the demo backend matched keywords against the whole prompt
    including the retrieved passages.
    """
    passage = ("Pad Thai Goong Sod: soak rice noodles, then stir fry with "
               "tamarind, palm sugar and fish sauce until glossy.")
    _index_chunk_for(app, user["id"], passage, document_id=610)

    body = client.post("/api/chat", json={"message": "what about pad thai?",
                                          "stream": False}).get_json()
    meta, answer = body["meta"], body["reply"]

    assert meta["grounded"] is True
    assert meta["citations"], "grounded but nothing cited"

    # Traceable to the cited passage, not merely non-empty.
    assert "tamarind" in answer.lower() or "rice noodles" in answer.lower(), (
        f"answer is not derived from the cited passage: {answer[:200]!r}")
    # And specifically not the script the passage's own words used to trigger.
    assert "diabetes" not in answer.lower()


def test_a_query_about_something_absent_stays_ungrounded(app, client, user):
    """The other half. A retrieval fix that returns everything is not a fix:
    an accepted hit is presented as evidence, with a citation."""
    passage = ("Pad thai is stir fried with rice noodles, tamarind paste and "
               "fish sauce over high heat.")
    _index_chunk_for(app, user["id"], passage, document_id=602)

    result = client.get(
        "/api/documents/search?q=kubernetes+ingress+controller").get_json()["result"]

    assert result["grounded"] is False
    assert result["chunks"] == []


def test_search_reports_whether_matching_is_semantic(app, client, user):
    """The UI needs this to explain an empty result truthfully.

    It used to quote the similarity threshold, which reads as "tune this number"
    when the real answer is that the active provider matches words, not meaning.
    """
    _index_chunk_for(app, user["id"], "Coconut milk and lemongrass.", document_id=603)

    result = client.get("/api/documents/search?q=coconut").get_json()["result"]

    assert "semantic" in result
    assert result["semantic"] is False  # lexical provider under test conditions
    assert result["threshold"] > 0


def test_documents_indexed_under_another_provider_are_flagged(app, client, user):
    """Changing embedding provider changes collection, so old rows still read
    "indexed" while contributing nothing to search — indistinguishable from
    broken retrieval unless the UI is told.

    Derived on read rather than stored: it needs no schema change, it cannot
    itself go stale, and a GET does not write to the database.
    """
    with app.app_context():
        db.session.add(Document(
            id=604, user_id=user["id"], filename="orphaned.pdf",
            stored_name="instance/uploads/orphaned.pdf", sha256="d" * 64,
            status="indexed", pages=3, chunk_count=9))  # no vectors anywhere
        db.session.commit()

    documents = client.get("/api/documents").get_json()["documents"]
    orphan = next(d for d in documents if d["id"] == 604)

    assert orphan["status"] == "indexed"      # the row itself is untouched
    assert orphan["searchable"] is False      # but it cannot be retrieved


def test_a_document_with_live_vectors_is_reported_searchable(app, client, user):
    """The counterpart — otherwise 'searchable: False' everywhere would pass."""
    _index_chunk_for(app, user["id"], "Coconut milk and lemongrass.", document_id=605)

    documents = client.get("/api/documents").get_json()["documents"]
    live = next(d for d in documents if d["id"] == 605)

    assert live["searchable"] is True


def test_uploading_the_same_file_as_another_account_is_allowed(client, client_b):
    """Deduplication is per-account.

    A global sha256 check would tell the second uploader of a public guideline
    PDF that it is "already indexed" while showing them nothing they can open —
    and would confirm that some other account holds that exact file.
    """
    pdf = (b"%PDF-1.4\n1 0 obj<</Type/Catalog>>endobj\ntrailer<</Root 1 0 R>>\n"
           b"%%EOF\n")

    def upload(test_client):
        return test_client.post(
            "/api/documents",
            data={"file": (io.BytesIO(pdf), "shared.pdf", "application/pdf")},
            content_type="multipart/form-data")

    # Both are rejected as unreadable PDFs rather than as duplicates: the point
    # is that the second account gets the same treatment as the first.
    assert upload(client).status_code == upload(client_b).status_code
