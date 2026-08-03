"""Integration tests for the agent layer (demo mode, hash embeddings)."""

from __future__ import annotations

import pytest

from nutrimind.agents import get_coordinator
from nutrimind.agents.base_agent import AgentRequest
from nutrimind.agents.tools import Toolbox, naive_extract_items
from nutrimind.extensions import db
from nutrimind.models import Document, MealLog, MealPlan, UserProfile
from nutrimind.retrieval.chunker import Chunk
from nutrimind.services.llm import get_llm_client
from nutrimind.services.rag_service import get_rag_service
from nutrimind.utils.time import utcnow
from tests.conftest import make_user


@pytest.fixture()
def account(app):
    """An account every agent test runs as."""
    with app.app_context():
        return make_user().id


@pytest.fixture()
def profile(app, account):
    with app.app_context():
        row = UserProfile(user_id=account, name="Harsh", age=21, gender="male", height_cm=175,
                          weight_kg=70, activity_level="moderate",
                          food_preference="vegetarian", weight_goal="maintain",
                          medical_conditions=[], allergies=["peanuts"])
        db.session.add(row)
        db.session.commit()
        yield row


def _run(app, message: str, user_id: int):
    """Drive the coordinator; returns (events, final_event)."""
    settings = app.config["NUTRIMIND_SETTINGS"]
    service = get_rag_service(settings)
    request = AgentRequest(
        message=message,
        toolbox=Toolbox(lambda: service, user_id,
                        document_ids=service.document_ids_for(user_id)),
        llm=get_llm_client(settings, refresh=True),
        profile=UserProfile.for_user(user_id), history=())
    events = list(get_coordinator().handle(request))
    finals = [e for e in events if e["type"] == "final"]
    assert len(finals) == 1, "exactly one final event required"
    return events, finals[0]


def _index_fact(app, text: str, user_id: int, page: int = 4):
    settings = app.config["NUTRIMIND_SETTINGS"]
    service = get_rag_service(settings)
    chunk = Chunk(text=text, page=page, chunk_index=0)
    db.session.add(Document(id=99, user_id=user_id, filename="facts.pdf",
                            stored_name=f"instance/uploads/f{user_id}.pdf",
                            sha256="c" * 64, status="indexed"))
    db.session.commit()
    service.store.add_chunks(document_id=99, filename="facts.pdf",
                             uploaded_at=utcnow().isoformat(),
                             chunks=[chunk],
                             embeddings=service.provider.embed_documents([text]))


def test_knowledge_agent_grounded_with_citations(app, account):
    fact = "Guava contains over 200 mg of vitamin C per hundred grams."
    with app.app_context():
        _index_fact(app, fact, account)
        events, final = _run(app, fact, account)  # exact text -> grounded under hash
        meta = final["meta"]
        assert meta["agent"] == "knowledge_agent"
        assert meta["grounded"] is True
        assert meta["response_source"] == "grounded"
        assert meta["citations"] == [{"filename": "facts.pdf", "page": 4}]
        assert meta["retrieved_chunks"] == 1
        assert any(t["tool"] == "retrieve_knowledge" for t in meta["tools_used"])
        assert "General knowledge" not in final["text"]


def test_knowledge_agent_ungrounded_is_labeled(app, account):
    with app.app_context():
        _, final = _run(app, "what foods are rich in iron?", account)
        meta = final["meta"]
        assert meta["grounded"] is False
        assert meta["response_source"] == "general_knowledge"
        assert meta["citations"] == []
        assert "General knowledge" in final["text"]


def test_meal_planner_creates_validated_plan(app, account, profile):
    with app.app_context():
        events, final = _run(app, "create a meal plan for me", account)
        meta = final["meta"]
        assert meta["agent"] == "meal_planner"
        assert meta["routing"]["intent"] == "Meal Planning"
        assert any(t["tool"] == "calculate_targets" for t in meta["tools_used"])
        assert meta["targets"]["calories"] > 1200

        plan = db.session.get(MealPlan, meta["plan_id"])
        assert plan is not None
        names = [m["name"] for m in plan.plan["meals"]]
        assert names == ["Breakfast", "Lunch", "Snack", "Dinner"]
        assert "Breakfast" in final["text"]


def test_meal_planner_without_profile_asks_for_profile(app, account):
    with app.app_context():
        _, final = _run(app, "make me a diet plan", account)
        assert "profile" in final["text"].lower()
        assert final["meta"]["tokens"]["total"] >= 0


def test_meal_analyzer_deterministic_math_and_logging(app, account, profile):
    with app.app_context():
        _, final = _run(app, "Today I ate 2 rotis, dal, paneer and an apple", account)
        meta = final["meta"]
        assert meta["agent"] == "meal_analyzer"
        assert meta["extraction"] == "deterministic_scanner"  # demo mode
        tools = [t["tool"] for t in meta["tools_used"]]
        assert "lookup_foods" in tools and "log_meal" in tools
        assert meta["totals"]["calories"] > 300
        assert meta["quality_score"] is None or 0 <= meta["quality_score"] <= 100

        logs = list(db.session.execute(db.select(MealLog)).scalars())
        assert len(logs) == 1 and logs[0].calories == meta["totals"]["calories"]
        assert "| Food |" in final["text"]  # numbers table present


def test_meal_analyzer_unknown_foods_handled(app, account):
    with app.app_context():
        # No known food names/aliases appear in this description.
        _, final = _run(app, "today I ate unicorn stew and stardust broth", account)
        assert final["meta"]["agent"] == "meal_analyzer"
        assert "couldn't identify" in final["text"]


def test_health_advisor_condition_query(app, account, profile):
    with app.app_context():
        _, final = _run(app, "what should I eat for high blood pressure?", account)
        meta = final["meta"]
        assert meta["agent"] == "health_advisor"
        assert meta["response_source"] in ("grounded", "general_knowledge")
        assert any(t["tool"] == "retrieve_knowledge" for t in meta["tools_used"])
        assert any(t["tool"] == "calculate_targets" for t in meta["tools_used"])


def test_coordinator_bmi_direct_zero_tokens(app, account, profile):
    with app.app_context():
        events, final = _run(app, "what is my bmi?", account)
        meta = final["meta"]
        assert meta["agent"] == "coordinator"
        assert meta["tokens"] == {"total": 0, "estimated": False}
        assert meta["routing"]["method"] == "rules"
        assert "22.9" in final["text"]
        assert any(t["tool"] == "compute_bmi" for t in meta["tools_used"])


def test_coordinator_smalltalk_direct(app, account):
    with app.app_context():
        _, final = _run(app, "hello", account)
        assert final["meta"]["agent"] == "coordinator"
        assert final["meta"]["tokens"]["total"] == 0


def test_event_protocol_order(app, account):
    with app.app_context():
        events, _ = _run(app, "how much protein is in paneer?", account)
        kinds = [e["type"] for e in events]
        assert kinds[0] == "status"
        assert "routing" in kinds
        assert kinds.index("routing") < kinds.index("token")
        assert kinds[-1] == "final"


def test_naive_extractor_quantities_and_aliases():
    items = naive_extract_items("I had two chapatis, 1 katori dal and an apple")
    names = {i["name"]: i["quantity"] for i in items}
    assert names.get("roti") == 2.0          # alias + word-number
    assert "apple" in names
    assert any("dal" in n for n in names)
