"""Unit tests for the SQLAlchemy models (constraints, helpers, round-trips)."""

import datetime as dt

import pytest
from sqlalchemy.exc import IntegrityError

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


def test_profile_singleton_and_prompt_summary(app):
    with app.app_context():
        assert UserProfile.get_singleton() is None
        profile = UserProfile(name="Harsh", age=21, gender="male", height_cm=175,
                              weight_kg=70, activity_level="moderate",
                              food_preference="vegetarian", weight_goal="maintain",
                              medical_conditions=["diabetes"], allergies=[])
        db.session.add(profile)
        db.session.commit()
        loaded = UserProfile.get_singleton()
        assert loaded is not None and loaded.name == "Harsh"
        summary = loaded.summary_for_prompt()
        assert "21-year-old male" in summary and "diabetes" in summary


def test_profile_check_constraint(app):
    with app.app_context():
        db.session.add(UserProfile(name="X", age=500, gender="male", height_cm=175,
                                   weight_kg=70, activity_level="moderate",
                                   food_preference="vegan", weight_goal="lose"))
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()


def test_meal_log_since_and_roundtrip(app):
    with app.app_context():
        old = MealLog(ts=dt.datetime.utcnow() - dt.timedelta(days=30), calories=500)
        new = MealLog(calories=650, items=[{"name": "roti"}], meal_type="lunch")
        db.session.add_all([old, new])
        db.session.commit()
        recent = MealLog.since(7)
        assert len(recent) == 1 and recent[0].to_dict()["calories"] == 650


def test_meal_type_constraint(app):
    with app.app_context():
        db.session.add(MealLog(meal_type="brunch", calories=100))
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()


def test_chat_recent_ordering(app):
    with app.app_context():
        for index in range(5):
            db.session.add(ChatMessage(session_id="s1", role="user",
                                       content=f"message {index}"))
        db.session.add(ChatMessage(session_id="s2", role="user", content="other"))
        db.session.commit()
        recent = ChatMessage.recent("s1", limit=3)
        assert [m.content for m in recent] == ["message 2", "message 3", "message 4"]


def test_document_status_transitions(app):
    with app.app_context():
        doc = Document(filename="who.pdf", stored_name="abc123", sha256="f" * 64)
        db.session.add(doc)
        db.session.commit()
        assert doc.status == "pending"
        doc.mark_indexed(pages=12, chunk_count=48)
        db.session.commit()
        assert doc.to_dict()["status"] == "indexed" and doc.chunk_count == 48
        doc.mark_failed("boom " * 200)
        assert doc.status == "failed" and len(doc.error) <= 500


def test_document_stored_name_fits_seeded_paths(app):
    """Seeded files store a repo-relative path, which can exceed the old 64 chars.

    SQLite ignores VARCHAR limits, so an over-long value passes here regardless;
    this test exists to pin the declared column width, which is what Postgres
    will actually enforce after the database migration.
    """
    assert Document.__table__.c.stored_name.type.length >= 255

    long_path = f"knowledge_base/{'icmr-nin-dietary-guidelines-for-indians-' * 4}.pdf"
    assert len(long_path) > 64
    with app.app_context():
        doc = Document(filename="guidelines.pdf", stored_name=long_path,
                       sha256="a" * 64)
        db.session.add(doc)
        db.session.commit()
        assert doc.stored_name == long_path


def test_water_upsert_and_clamping(app):
    with app.app_context():
        WaterLog.add_glasses(3)
        WaterLog.add_glasses(2)
        db.session.commit()
        rows = list(db.session.execute(db.select(WaterLog)).scalars())
        assert len(rows) == 1 and rows[0].glasses == 5
        WaterLog.add_glasses(100)                 # clamped to 30
        db.session.commit()
        assert rows[0].glasses == 30
        WaterLog.add_glasses(-50)                 # clamped to 0
        db.session.commit()
        assert rows[0].glasses == 0


def test_bmi_record_and_meal_plan_roundtrip(app):
    with app.app_context():
        db.session.add(BMIRecord(height_cm=175, weight_kg=70, bmi=22.9,
                                 category="normal", ideal_weight_min_kg=56.7,
                                 ideal_weight_max_kg=76.3))
        db.session.add(MealPlan(title="Test plan", targets={"calories": 2000},
                                plan={"days": []}))
        db.session.commit()
        plan = db.session.execute(db.select(MealPlan)).scalar_one()
        assert plan.to_dict(include_plan=False).get("plan") is None
        assert plan.to_dict()["plan"] == {"days": []}
