"""Unit tests for the meal-plan PDF export service."""

import io

from pypdf import PdfReader

from nutrimind.extensions import db
from nutrimind.models import MealPlan, UserProfile
from nutrimind.services.export_service import build_meal_plan_pdf
from tests.conftest import make_user

_PLAN = {
    "title": "Test day plan",
    "meals": [
        {"name": name, "items": [{"food": "roti", "portion": "2"}],
         "calories": 500, "protein_g": 20}
        for name in ("Breakfast", "Lunch", "Snack", "Dinner")
    ],
    "hydration": "Drink water with every meal.",
    "notes": "Balanced test plan.",
}
_TARGETS = {"calories": 2000, "protein_g": 84, "fat_g": 60,
            "carbs_g": 250, "fiber_g": 28, "water_l": 2.5}


def _pdf_text(data: bytes) -> str:
    return PdfReader(io.BytesIO(data)).pages[0].extract_text()


def test_pdf_generation_contains_all_sections(app):
    with app.app_context():
        owner = make_user().id
        plan = MealPlan(user_id=owner, title="Test day plan", targets=_TARGETS, plan=_PLAN)
        profile = UserProfile(user_id=owner, name="Harsh", age=21, gender="male", height_cm=175,
                              weight_kg=70, activity_level="moderate",
                              food_preference="vegetarian", weight_goal="maintain")
        db.session.add_all([plan, profile])
        db.session.commit()

        data = build_meal_plan_pdf(plan, profile)
        assert data.startswith(b"%PDF-")
        text = _pdf_text(data)
        for expected in ("NutriMind AI", "Test day plan", "Harsh", "2000",
                         "Breakfast", "Lunch", "Snack", "Dinner",
                         "Hydration", "not medical advice"):
            assert expected in text, f"missing {expected!r} in PDF"


def test_pdf_generation_without_profile(app):
    with app.app_context():
        owner = make_user().id
        plan = MealPlan(user_id=owner, title="Anon plan", targets=_TARGETS, plan=_PLAN)
        db.session.add(plan)
        db.session.commit()
        data = build_meal_plan_pdf(plan, None)
        assert data.startswith(b"%PDF-")
        assert "Personalized plan" in _pdf_text(data)


def test_pdf_deterministic_structure(app):
    """Same plan renders to a valid, similar-size PDF (timestamps vary)."""
    with app.app_context():
        owner = make_user().id
        plan = MealPlan(user_id=owner, title="Repeat plan", targets=_TARGETS, plan=_PLAN)
        db.session.add(plan)
        db.session.commit()
        first = build_meal_plan_pdf(plan)
        second = build_meal_plan_pdf(plan)
        assert abs(len(first) - len(second)) < 200
